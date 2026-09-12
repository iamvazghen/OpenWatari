"""One answer to "what's waiting for me" across every channel (38.F2).

Afon could already read mail, read Telegram and list the approvals autonomous work had deferred.
Each of those answered in its own prose, in its own turn, and the owner held the join in his head —
which is the same as not having the answer, because the whole point of asking is to find the thing
you had forgotten about.

Three things this refuses to do:

  * **It never merges across channels.** An email thread and a Telegram chat about the same subject
    are two things to answer, not one. Deduplication folds a thread into itself — five messages in
    one mail thread are one row with a count — and stops there. Guessing that two channels are the
    same conversation would hide one of them, which is the failure this view exists to prevent.
  * **It never reports a channel it could not read as empty.** "Nothing waiting" and "I could not
    look" are different facts, and showing the first when the second is true is how a summary the
    owner relies on becomes one he stops checking.
  * **It never blocks on a slow channel.** Sources are fetched concurrently under a per-source
    deadline; one hung mailbox costs that channel, not the answer.

    uv run python -m afon.brain.tools.inbox     # self-check
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from loguru import logger

from afon.brain.tools.base import clip, tool_error

#: One slow channel must not cost the whole answer. The plan's budget is a ≤5s sweep across every
#: channel, and they run concurrently, so this is the per-source share of it with room to spare.
SOURCE_TIMEOUT_S = 4.0

#: Reply and forward markers, in the languages the owner's mail actually arrives in. Stripped only
#: to build a fallback thread key — the subject he hears is the one that was sent.
_REPLY_PREFIX = re.compile(r"^\s*(re|fwd|fw|aw|wg|antw|rép|res)\s*(\[\d+\])?\s*:\s*", re.I)


@dataclass(frozen=True)
class Waiting:
    channel: str              # "email" | "telegram" | "approval"
    thread: str               # stable within the channel; the dedup key
    who: str
    subject: str
    count: int = 1
    at: float = 0.0           # unix seconds, 0 when the channel doesn't say
    detail: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.channel, self.thread)


def _thread_key(row: dict, channel: str) -> str:
    """The thread this row belongs to. Falls back to sender + subject when the channel gives none."""
    t = str(row.get("thread") or "").strip()
    if t:
        return t
    who = str(row.get("who") or "").strip().lower()
    subj = _REPLY_PREFIX.sub("", str(row.get("subject") or "")).strip().lower()
    return f"{who}|{subj}"


def merge(rows: list[Waiting]) -> list[Waiting]:
    """Fold rows of one thread into one row, newest first. Counts add; the newest message wins."""
    by_key: dict[tuple[str, str], Waiting] = {}
    for r in rows:
        prev = by_key.get(r.key)
        if prev is None:
            by_key[r.key] = r
            continue
        newer = r if r.at >= prev.at else prev
        by_key[r.key] = Waiting(newer.channel, newer.thread, newer.who, newer.subject,
                                prev.count + r.count, newer.at, newer.detail)
    return sorted(by_key.values(), key=lambda w: (-w.at, w.channel, w.who))


async def _email() -> list[Waiting]:
    from afon.brain.tools.gmail import unread_threads

    return [Waiting("email", _thread_key(r, "email"), r["who"], r["subject"],
                    int(r.get("count") or 1), float(r.get("at") or 0.0))
            for r in await unread_threads()]


async def _telegram() -> list[Waiting]:
    from afon.brain.tools.telegram import unread_chats

    return [Waiting("telegram", _thread_key(r, "telegram"), r["who"], r["subject"],
                    int(r.get("count") or 1), float(r.get("at") or 0.0))
            for r in await unread_chats()]


async def _approvals() -> list[Waiting]:
    # An action Afon deferred is waiting on the owner exactly as much as an unread message is, and
    # it was the one kind of waiting he had to remember to go and look for.
    from afon.brain.approvals import APPROVALS

    return [Waiting("approval", a.id, "me", a.summary, 1,
                    _epoch(a.created), {"id": a.id, "origin": a.origin})
            for a in APPROVALS.pending()]


def _epoch(iso: str) -> float:
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso).timestamp()
    except Exception:  # noqa: BLE001 — an unparseable stamp sorts last, it does not drop the row
        return 0.0


#: name -> coroutine factory. A source that raises is named as unknown, never counted as empty.
SOURCES = {"email": _email, "telegram": _telegram, "approvals": _approvals}


async def waiting(timeout: float = SOURCE_TIMEOUT_S) -> tuple[list[Waiting], list[str]]:
    """Everything waiting, deduplicated by thread, plus the names of channels that didn't answer."""
    names = list(SOURCES)
    results = await asyncio.gather(
        *(asyncio.wait_for(SOURCES[n](), timeout) for n in names), return_exceptions=True
    )
    rows: list[Waiting] = []
    unknown: list[str] = []
    for name, res in zip(names, results):
        if isinstance(res, BaseException):
            logger.debug(f"inbox: {name} unreadable ({type(res).__name__}: {res})")
            unknown.append(name)
        else:
            rows.extend(res)
    return merge(rows), unknown


def spoken(rows: list[Waiting], unknown: list[str] | None = None, max_items: int = 6) -> str:
    """One paragraph. Says what is waiting, and says plainly what could not be read."""
    unknown = unknown or []
    gap = (" I couldn't reach " + " or ".join(unknown) + ", so that may not be all of it."
           if unknown else "")
    if not rows:
        return ("Nothing's waiting on you, sir." if not unknown
                else "Nothing I could see is waiting on you, sir." + gap)
    total = sum(r.count for r in rows)
    head = (f"{total} thing{'s' if total != 1 else ''} waiting across "
            f"{len({r.channel for r in rows})} channel"
            f"{'s' if len({r.channel for r in rows}) != 1 else ''}, sir: ")
    lines = []
    for r in rows[:max_items]:
        more = f" ({r.count})" if r.count > 1 else ""
        lines.append(f"{r.channel} from {r.who}{more} — {clip(r.subject, 90)}")
    rest = f", and {len(rows) - max_items} more" if len(rows) > max_items else ""
    return head + "; ".join(lines) + rest + "." + gap


async def whats_waiting(_args: dict) -> str:
    try:
        rows, unknown = await waiting()
        return spoken(rows, unknown)
    except Exception as e:  # noqa: BLE001
        return tool_error("the waiting list", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "whats_waiting",
            "description": ("Everything waiting on the owner across every channel at once — unread "
                            "email, unread Telegram chats, and actions awaiting his approval — "
                            "deduplicated by thread. Use for 'what's waiting', 'anything for me', "
                            "'what needs me'."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {"whats_waiting": whats_waiting}


def _selfcheck() -> None:
    now = time.time()
    # Five messages in one mail thread are one thing to answer, not five.
    rows = [
        Waiting("email", "t1", "Jane", "Invoice", 1, now - 60),
        Waiting("email", "t1", "Jane", "Re: Invoice", 1, now - 30),
        Waiting("email", "t2", "Bob", "Lunch?", 1, now - 600),
        Waiting("telegram", "t1", "Jane", "same subject, other channel", 1, now - 10),
    ]
    out = merge(rows)
    assert len(out) == 3, out
    one = next(r for r in out if r.channel == "email" and r.thread == "t1")
    assert one.count == 2 and one.subject == "Re: Invoice", one
    # Same thread id in two channels must stay two rows — they are two things to answer.
    assert sum(1 for r in out if r.thread == "t1") == 2, out
    assert out[0].channel == "telegram", [r.channel for r in out]   # newest first

    # A channel with no thread id of its own folds on sender + normalised subject.
    assert _thread_key({"who": "Jane", "subject": "Re: Invoice"}, "email") == \
           _thread_key({"who": "jane", "subject": "Invoice"}, "email")
    assert _thread_key({"who": "Jane", "subject": "AW: Rechnung"}, "email").endswith("rechnung")
    assert _thread_key({"thread": "abc", "who": "Jane"}, "email") == "abc"

    said = spoken(out)
    assert "3 channels" not in said and "2 channels" in said, said
    assert "Jane" in said and "(2)" in said, said
    assert "Nothing's waiting" in spoken([])
    # The one thing it must never do: report a channel it could not read as empty.
    quiet = spoken([], ["email"])
    assert "couldn't reach email" in quiet and "Nothing's waiting on you" not in quiet, quiet
    assert "may not be all of it" in spoken(out, ["telegram"])

    async def _one_slow_source() -> None:
        async def hang():
            await asyncio.sleep(9)
            return []

        async def fine():
            return [Waiting("approval", "a1", "me", "send_email(to=x)")]

        SOURCES["email"], SOURCES["telegram"], SOURCES["approvals"] = hang, hang, fine
        started = time.monotonic()
        got, unk = await waiting(timeout=0.2)
        assert time.monotonic() - started < 2.0, "a hung channel blocked the sweep"
        assert [r.channel for r in got] == ["approval"], got
        assert sorted(unk) == ["email", "telegram"], unk

    asyncio.run(_one_slow_source())
    print("inbox self-check OK")


if __name__ == "__main__":
    _selfcheck()
