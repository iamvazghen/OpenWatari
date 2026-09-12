"""What happened to a notification after it left (13.F3).

Every sender in this repo returns a boolean and forgets. `push()` reports that ntfy accepted the
POST; the proactive engine records that it interjected. Neither answers the only question that
matters when something urgent goes out: *did it reach him, and did he do anything about it?* The
visible cost is an urgent item pushed to a phone in a coat pocket, counted as delivered, and never
mentioned again.

Three states, and the gap between them is the whole point:

    delivered  the transport accepted it. Nothing more. A push is ALWAYS only this — ntfy reports
               nothing about eyes, so inferring "seen" from a successful POST would be the same
               overclaim as a status page that shows green because it never asked.
    seen       there is evidence he received it. Exactly one inference is allowed, and it is named:
               a line spoken into a live session, followed by a turn from him inside the window,
               was heard. Everything else must be told.
    acted      there is evidence he did something about it.

An item may only move forward. A later `delivered` cannot un-see something, because the evidence
that he saw it does not stop being true.

Re-raising is deliberately once. An urgent item still sitting at `delivered` after
``RERAISE_AFTER_S`` is offered back to the caller exactly one time; after that it is his to ignore.
A ledger that keeps re-raising is a notification loop, which is the failure this system already
fixed once (7-8 duplicate messages a day) and must not reintroduce under a new name.

ponytail: a JSON file, not a table. It holds a few hundred rows, is read once per re-raise sweep,
and has one writer — the brain process. A store would be a schema and a migration for something
`json.dumps` does correctly.

    uv run python -m afon.brain.delivery     # self-check
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loguru import logger

from afon.shared.paths import state_dir

DELIVERED = "delivered"
SEEN = "seen"
ACTED = "acted"

#: Forward-only ordering. Comparing positions is what makes a late `delivered` harmless.
_ORDER = {DELIVERED: 0, SEEN: 1, ACTED: 2}

#: At or above this urgency an unacknowledged item is worth raising a second time. Below it, a
#: missed nudge is a nudge missed — re-raising ordinary suggestions is nagging.
URGENT = 0.8

#: How long to wait before deciding he has not acknowledged it. Long enough that a push landing
#: while he is in a meeting is not re-raised as he walks out of it.
RERAISE_AFTER_S = 1800.0

#: Rows kept. Old deliveries are only ever read for the re-raise sweep, which looks at the recent
#: tail; keeping every one forever would grow a file nobody reads the head of.
KEEP = 200


def _path() -> Path:
    return state_dir() / "delivery_ledger.json"


def _load() -> list[dict]:
    try:
        rows = json.loads(_path().read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001 — a corrupt ledger must not take the turn with it
        logger.warning(f"delivery ledger unreadable ({type(e).__name__}); starting empty")
        return []


def _save(rows: list[dict]) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-KEEP:], ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"delivery ledger write failed ({type(e).__name__})")


def _find(rows: list[dict], key: str) -> dict | None:
    for row in reversed(rows):  # the newest delivery of a repeating key is the live one
        if row.get("key") == key:
            return row
    return None


def record(key: str, message: str, urgency: float = 0.0, channel: str = "push",
           now: float | None = None) -> dict:
    """Note that something left for the owner. Returns the row.

    `channel` matters: only a line spoken into a live session can later be inferred as seen.
    """
    now = time.time() if now is None else now
    rows = _load()
    row = {"key": key, "message": (message or "")[:300], "urgency": float(urgency),
           "channel": channel, "at": now, "state": DELIVERED, "evidence": "",
           "state_at": now, "reraised_at": None}
    rows.append(row)
    _save(rows)
    return row


def mark(key: str, state: str, evidence: str, now: float | None = None) -> bool:
    """Move a delivery forward. Returns False if there is nothing to move, or it is already past.

    `evidence` is required and stored: a state change nobody can explain later is indistinguishable
    from a guess, which is the thing this module exists to stop.
    """
    if state not in _ORDER or not evidence:
        return False
    now = time.time() if now is None else now
    rows = _load()
    row = _find(rows, key)
    if row is None or _ORDER.get(row.get("state"), 0) >= _ORDER[state]:
        return False
    row["state"], row["evidence"], row["state_at"] = state, evidence, now
    _save(rows)
    return True


def state_of(key: str) -> str | None:
    """The current state of the newest delivery under `key`, or None if it was never sent."""
    row = _find(_load(), key)
    return row.get("state") if row else None


def due_for_reraise(now: float | None = None) -> list[dict]:
    """Urgent deliveries still unacknowledged past the window, never yet re-raised."""
    now = time.time() if now is None else now
    return [r for r in _load()
            if r.get("state") == DELIVERED
            and float(r.get("urgency") or 0) >= URGENT
            and r.get("reraised_at") is None
            and (now - float(r.get("at") or 0)) >= RERAISE_AFTER_S]


def mark_reraised(key: str, now: float | None = None) -> bool:
    """Spend the one re-raise. Returns False if it was already spent (so a caller cannot loop)."""
    now = time.time() if now is None else now
    rows = _load()
    row = _find(rows, key)
    if row is None or row.get("reraised_at") is not None:
        return False
    row["reraised_at"] = now
    _save(rows)
    return True


#: Prefix for the re-raise's own key. A re-raise is NOT recorded as a fresh delivery: it is urgent
#: by construction, so recording it would make it due for its own re-raise half an hour later, and
#: the ledger would become the notification loop it exists to prevent.
RERAISE_PREFIX = "reraise:"


async def reraise_signals() -> list:
    """Proactive source: one second chance per urgent item he never acknowledged.

    The chance is spent when the signal is *delivered*, not here. A signal generated on a tick and
    then held by quiet hours, the daily budget or a focus mode never reached him, and marking it
    spent at generation would consume his only second chance on a message he never heard.
    """
    from afon.brain.proactive import Signal

    return [Signal(key=f"{RERAISE_PREFIX}{row['key']}", kind="reraise",
                   urgency=float(row["urgency"]),
                   message=f"Sir, you never came back to this: {row['message']}")
            for row in due_for_reraise()]


def _selfcheck() -> None:
    import sys
    import tempfile

    me = sys.modules[__name__]  # under `python -m` this file is __main__; importing by name would
    tmp = tempfile.TemporaryDirectory()  # patch a second copy and let the real ledger be written
    me._path = lambda: Path(tmp.name) / "ledger.json"  # type: ignore[assignment]

    t = 1_000_000.0
    record("k1", "your visa is due Friday", urgency=0.9, channel="push", now=t)
    assert state_of("k1") == DELIVERED
    assert not due_for_reraise(now=t + 60), "fresh delivery is not due"
    assert [r["key"] for r in due_for_reraise(now=t + RERAISE_AFTER_S)] == ["k1"]

    assert mark_reraised("k1", now=t + RERAISE_AFTER_S)
    assert not mark_reraised("k1", now=t + RERAISE_AFTER_S), "the re-raise is spent"
    assert not due_for_reraise(now=t + 10 * RERAISE_AFTER_S), "and it never comes back"

    record("k2", "stand up", urgency=0.5, channel="push", now=t)
    assert not due_for_reraise(now=t + 10 * RERAISE_AFTER_S), "non-urgent is never re-raised"

    record("k3", "the build is red", urgency=0.95, channel="voice", now=t)
    assert mark("k3", SEEN, "replied within the window", now=t + 5)
    assert not mark("k3", DELIVERED, "late transport ack", now=t + 6), "states never go backwards"
    assert state_of("k3") == SEEN
    assert not due_for_reraise(now=t + 10 * RERAISE_AFTER_S), "acknowledged is not re-raised"
    assert mark("k3", ACTED, "fixed the build", now=t + 90)
    assert state_of("k3") == ACTED
    assert not mark("k3", SEEN, "backwards", now=t + 91)

    assert not mark("nope", SEEN, "evidence"), "an unsent key cannot be marked"
    assert not mark("k3", SEEN, ""), "a state change with no evidence is refused"
    assert state_of("never-sent") is None

    tmp.cleanup()
    print("delivery ledger self-check OK")


if __name__ == "__main__":
    _selfcheck()
