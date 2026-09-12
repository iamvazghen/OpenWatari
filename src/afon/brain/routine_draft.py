"""Draft the owner's routines from what he actually does, so he corrects instead of composes (21.F1).

`routines.json` ships with two dynamic commitments and no windowed entries at all, which means every
downstream capability — the morning planning prompt, routine anchors, focus protection, conflict
detection — reasons about a day that does not exist. The task has sat at "owner: 30 minutes to write
the real routines" since August, and a blank file is the reason: nobody writes a schedule from
nothing on a Tuesday evening.

Afon already has fourteen days of evidence about the shape of a normal day sitting in the presence
database. This turns that into a draft he can read back and have corrected in five minutes.

Two things this deliberately does NOT do:

  * **It never installs itself.** A drafted routine is a proposal with evidence attached, written to
    a separate file. Adopting it is an explicit, confirm-gated act. A system that learns your habits
    and then starts nagging you about them without being asked is the thing people uninstall.
  * **It never invents a habit it did not observe.** Every drafted entry carries how many of the
    observed days it actually held on. A routine drafted from two days is worse than no routine,
    because it looks like knowledge.

ponytail: one SQL scan and a dict of counters. No clustering library, no model — "the same category
was active in the same hour on most days" is what a habit IS, and a k-means over four hundred
samples would give the owner a schedule he cannot argue with.

    uv run python -m afon.brain.routine_draft     # self-check
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from afon.shared.paths import state_dir

#: How far back to look. Two weeks covers both weekday shapes and a couple of weekends without
#: reaching back into a schedule the owner has since abandoned.
LOOKBACK_DAYS = 14

#: A habit must hold on at least this fraction of the observed days, and never on fewer than
#: MIN_DAYS regardless of the fraction — three days is the least that can distinguish a routine
#: from a busy week.
MIN_FRACTION = 0.5
MIN_DAYS = 3

#: Drafts land here, never in routines.json. The owner reads this, edits it, and adopts it.
def draft_path() -> Path:
    return state_dir() / "routines_draft.json"


#: Categories that are activities rather than a place the mouse happened to be. "other" and
#: "browsing" are excluded: a window drafted from them says "you use a browser most mornings",
#: which is true of everyone and useful to no one.
_MEANINGFUL = ("coding", "comms", "docs", "media", "gaming")

_WHAT = {
    "coding": "deep work",
    "comms": "messages and calls",
    "docs": "writing and reading",
    "media": "watching something",
    "gaming": "gaming",
}


def _rows(db: Path, since: float) -> list[tuple[float, str, float]]:
    from afon.brain.dbconn import connect

    try:
        with connect(db) as conn:
            cur = conn.execute("SELECT ts, app, idle FROM activity WHERE ts >= ?", (since,))
            return [(r[0], r[1] or "", r[2] or 0.0) for r in cur.fetchall()]
    except sqlite3.Error as e:
        logger.debug(f"routine draft: activity read failed ({e})")
        return []


def observe(db: Path | None = None, now: float | None = None,
            days: int = LOOKBACK_DAYS) -> list[dict]:
    """Windowed routine drafts for every (category, hour) block that held on most observed days."""
    from zoneinfo import ZoneInfo

    from afon.brain.presence import _category
    from afon.config import settings

    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if db is None:
        from afon.brain.presence import PRESENCE

        db = PRESENCE._path
    tz = ZoneInfo(settings.user_tz)
    idle_max = settings.presence_idle_threshold_seconds

    # (category, hour) -> the set of local dates it was actively used in
    seen: dict[tuple[str, int], set[str]] = {}
    all_days: set[str] = set()
    for ts, app, idle in _rows(Path(db), now - days * 86400):
        local = datetime.fromtimestamp(ts, tz)
        all_days.add(local.date().isoformat())
        if idle >= idle_max:
            continue  # away from the machine: not evidence of anything
        cat = _category(app)
        if cat not in _MEANINGFUL:
            continue
        seen.setdefault((cat, local.hour), set()).add(local.date().isoformat())

    observed = len(all_days)
    if observed < MIN_DAYS:
        return []  # not enough of a record to claim a habit from
    floor = max(MIN_DAYS, int(observed * MIN_FRACTION))

    habitual = {k: len(v) for k, v in seen.items() if len(v) >= floor}
    return _merge(habitual, observed)


def _merge(habitual: dict[tuple[str, int], int], observed: int) -> list[dict]:
    """Fold consecutive hours of the same category into one window."""
    out: list[dict] = []
    for cat in sorted({c for c, _ in habitual}):
        hours = sorted(h for c, h in habitual if c == cat)
        run: list[int] = []
        for h in hours + [None]:  # the None flushes the final run
            if run and h is not None and h == run[-1] + 1:
                run.append(h)
                continue
            if run:
                held = min(habitual[(cat, x)] for x in run)
                out.append({
                    "key": f"{cat}-{run[0]:02d}",
                    "window": f"{run[0]:02d}:00-{run[-1]:02d}:59",
                    "message": f"Sir, this is usually your {_WHAT.get(cat, cat)} block.",
                    "urgency": 0.6,
                    "source": "observed",
                    "evidence": f"active on {held} of the last {observed} days",
                })
            run = [h] if h is not None else []
    return out


# ---- validation -----------------------------------------------------------------------------
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def validate(routines: object) -> list[str]:
    """Every structural problem in a routines list, as sentences. Empty means it will load.

    The loader in ``proactive_signals.routine_signals`` swallows a malformed entry and moves on,
    which is right at runtime and wrong at adoption time: a typo'd window silently means that
    routine never fires again, and nothing ever says so.
    """
    if not isinstance(routines, list):
        return ["the file must contain a list of routines"]
    if not routines:
        return ["the list is empty, so nothing can ever fire"]
    problems: list[str] = []
    keys: set[str] = set()
    for i, r in enumerate(routines):
        where = f"entry {i + 1}"
        if not isinstance(r, dict):
            problems.append(f"{where} is not an object")
            continue
        key = r.get("key")
        where = f"'{key}'" if isinstance(key, str) and key else where
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{where} has no key")
        elif key in keys:
            problems.append(f"{where} is a duplicate key — the later one wins silently")
        else:
            keys.add(key)
        if not str(r.get("message") or "").strip():
            problems.append(f"{where} has no message, so firing it would say nothing")
        u = r.get("urgency", 0.65)
        if not isinstance(u, (int, float)) or not 0.0 <= float(u) <= 1.0:
            problems.append(f"{where} has an urgency outside 0..1 ({u!r})")
        days = r.get("days")
        if days is not None:
            if not isinstance(days, list) or any(d not in _DAYS for d in days):
                problems.append(f"{where} has days outside {list(_DAYS)} ({days!r})")
        if r.get("dynamic"):
            if r.get("window"):
                problems.append(f"{where} is dynamic AND windowed — a dynamic commitment never "
                                "fires from a window, so the window is dead weight")
            continue
        problems.extend(_window_problems(where, r.get("window")))
    return problems


def _window_problems(where: str, window: object) -> list[str]:
    if not isinstance(window, str) or not window.strip():
        return [f"{where} has no window, and is not marked dynamic — it can never fire"]
    start_s, _, end_s = window.partition("-")
    try:
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except ValueError:
        return [f"{where} has an unreadable window ({window!r}); expected 'HH:MM-HH:MM'"]
    if start >= end:
        return [f"{where} has a window that ends before it starts ({window}); a window that "
                "crosses midnight has to be written as two entries"]
    return []


# ---- the owner-facing pair ------------------------------------------------------------------
def write_draft(routines: list[dict]) -> Path:
    p = draft_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(routines, indent=2), encoding="utf-8")
    return p


def read_draft() -> list[dict]:
    try:
        rows = json.loads(draft_path().read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"routines draft unreadable ({type(e).__name__})")
        return []


def spoken(routines: list[dict]) -> str:
    """The draft read back, with its evidence. Evidence is not decoration: it is how he knows
    which lines to trust and which to rewrite."""
    if not routines:
        return ("Sir, I haven't watched you for long enough to draft a routine yet. Give it a few "
                "days of normal work and ask me again.")
    lines = [f"{r['window']} — {r['message']} ({r.get('evidence', 'no evidence recorded')})"
             for r in routines]
    return ("Sir, here is the shape of your day as I've actually observed it. Correct what's wrong "
            "and tell me to adopt it. " + " ".join(lines))


def _selfcheck() -> None:
    import sys
    import tempfile
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from afon.config import settings

    me = sys.modules[__name__]
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    me.draft_path = lambda: Path(tmp.name) / "draft.json"  # type: ignore[assignment]

    tz = ZoneInfo(settings.user_tz)
    now = datetime.now(tz)
    from afon.brain.dbconn import connect

    db = Path(tmp.name) / "afon_presence.sqlite"
    with closing(connect(db)) as conn:
        conn.execute("CREATE TABLE activity (ts REAL, app TEXT, title TEXT, idle REAL)")
        for day in range(1, 11):
            midnight = (now - timedelta(days=day)).replace(hour=0, minute=0, second=0,
                                                           microsecond=0)
            for hour in (9, 10):            # coding, every day -> a habit
                conn.execute("INSERT INTO activity VALUES (?,?,?,?)",
                             ((midnight + timedelta(hours=hour)).timestamp(), "Code.exe", "t", 0.0))
            if day < 3:                     # gaming, twice -> not a habit
                conn.execute("INSERT INTO activity VALUES (?,?,?,?)",
                             ((midnight + timedelta(hours=21)).timestamp(), "steam.exe", "t", 0.0))
        conn.commit()

    drafts = observe(db=db, now=now.timestamp())
    assert [d["key"] for d in drafts] == ["coding-09"], drafts
    assert drafts[0]["window"] == "09:00-10:59", drafts
    assert "of the last" in drafts[0]["evidence"], drafts
    assert not validate(drafts), validate(drafts)

    assert validate([]) == ["the list is empty, so nothing can ever fire"]
    assert validate("nope")
    assert any("window" in p for p in validate([{"key": "a", "message": "m"}]))
    assert any("ends before it starts" in p
               for p in validate([{"key": "a", "message": "m", "window": "18:00-09:00"}]))
    assert any("duplicate" in p for p in validate(
        [{"key": "a", "message": "m", "window": "09:00-10:00"},
         {"key": "a", "message": "m", "window": "11:00-12:00"}]))
    assert any("dynamic AND windowed" in p for p in validate(
        [{"key": "a", "message": "m", "dynamic": True, "window": "09:00-10:00"}]))
    assert not validate([{"key": "a", "message": "m", "dynamic": True}])

    write_draft(drafts)
    assert read_draft() == drafts
    assert "09:00" in spoken(drafts)
    assert "long enough" in spoken([])

    tmp.cleanup()
    print("routine draft self-check OK")


if __name__ == "__main__":
    _selfcheck()
