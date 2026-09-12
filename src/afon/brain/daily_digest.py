"""One daily catch-up: past-due tasks + important unread email, delivered ONCE per channel/day.

Replaces the old per-tick task/email proactive nudges. Those were keyed per-day but only
repeat-suppressed for ``proactive_repeat_suppress_minutes`` (120 min) and reset on every brain
restart, so the SAME two lines ("N past-due tasks", "M important emails") got pushed to Telegram
3-4 times each — the 7-8 duplicate messages the owner was seeing every day.

Now that information is a single consolidated digest delivered on at most two channels per local day:
  * ``"push"`` — the timed morning briefing (06:00 by default), reaching the phone via a Telegram
    voice-note / ntfy push even with the PC off;
  * ``"edge"`` — appended to the owner's FIRST live-edge turn of the day ("By the way, sir — …"),
    so it's heard conversationally the moment he starts talking.

A tiny JSON state file records which channel already delivered on which date, so neither channel
repeats within a day and a brain restart never re-fires it. Every source is fail-quiet: a broken
Notion/Gmail call just drops that part of the digest, never raises.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from afon.config import settings

from afon.shared.paths import state_dir


def _state_path() -> Path:
    return state_dir() / "daily_digest_state.json"


def _load() -> dict:
    try:
        return json.loads(_state_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt state just means "nothing delivered yet"
        return {}


def _save(state: dict) -> None:
    try:
        _state_path().write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"daily digest state save failed: {e}")


def _today(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo(settings.user_tz))
    return now.strftime("%Y-%m-%d")


def due(channel: str, now: datetime | None = None) -> bool:
    """True if ``channel`` ('push' | 'edge') hasn't delivered today's digest yet."""
    return _load().get(channel) != _today(now)


def mark_delivered(channel: str, now: datetime | None = None) -> None:
    """Record that ``channel`` delivered the digest today (so it won't repeat / survive a restart)."""
    state = _load()
    state[channel] = _today(now)
    _save(state)


#: Spoken names for the sources, so a failure is reported in words the owner can act on rather than
#: a module path. Keys are the ids `last_failed()` returns.
SOURCE_NAMES = {
    "notion": "your task board",
    "queue": "the local task queue",
    "gmail": "your mail",
}

#: Sources that failed in the most recent build. Exposed because 17.F3 requires the brief to say
#: WHICH source failed, and because a health snapshot wants the same answer without rebuilding the
#: brief to get it.
_LAST_FAILED: list[str] = []


def last_failed() -> list[str]:
    """Source ids that failed the last time the brief was built. Empty when all of them answered."""
    return list(_LAST_FAILED)


def _spoken_list(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c' — spoken, so no Oxford comma and no dangling 'and'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


async def build_body() -> str:
    """Compose the digest body (no greeting): past-due tasks + important email.

    **`''` means "a complete brief with nothing in it", and nothing else.** Every source here is
    fail-quiet, and before 17.F3 a failure was logged and then dropped: an unreachable task board
    produced a brief that confidently reported no past-due tasks. Worse, when *every* source failed
    the result was `''` — and `''` tells the caller there is nothing worth saying, so the single most
    alarming state produced silence.

    A failed source is therefore named in the body, and a total failure never returns `''`. A
    genuinely empty day still does, which is what the caller relies on.

    Sources, each fail-quiet: the Notion tasks DB (overdue + due-today), the local task queue (todos
    with a past deadline), and Gmail (important unread). Deliberately compact — it is spoken.
    """
    global _LAST_FAILED
    failed: list[str] = []
    overdue: list[str] = []
    due_today: list[str] = []

    try:  # Notion tasks dashboard
        from afon.brain.tools.notion import overdue_and_today

        overdue, due_today = await overdue_and_today()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: notion source failed: {e}")
        failed.append("notion")

    try:  # local task-queue todos whose deadline has passed
        from afon.brain.tasks import TASKS

        now_ts = time.time()
        for t in TASKS.todos():
            if t.deadline is not None and t.deadline < now_ts:
                overdue.append(f"{t.title} ({t.human_deadline()})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: local queue source failed: {e}")
        failed.append("queue")

    overdue = list(dict.fromkeys(overdue))

    email_phrase = ""
    try:  # important unread email
        from afon.brain.tools.gmail import important_email_phrase

        email_phrase = await important_email_phrase()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: gmail source failed: {e}")
        failed.append("gmail")

    lead: list[str] = []
    if overdue:
        lead.append(f"{len(overdue)} past-due task{'s' if len(overdue) != 1 else ''}: "
                    + "; ".join(overdue[:8]))
    if due_today:
        lead.append(f"{len(due_today)} due today: " + "; ".join(due_today[:8]))
    body = ". ".join(lead)
    if email_phrase:
        body = (body + ". And " + email_phrase) if body else ("You have " + email_phrase)

    _LAST_FAILED = failed
    if failed:
        names = _spoken_list([SOURCE_NAMES.get(f, f) for f in failed])
        if body:
            # What DID answer is still delivered; the caveat is scoped to the part that is missing.
            body = f"{body}. I couldn't reach {names}, so that part may be incomplete"
        else:
            body = f"I couldn't reach {names} this morning, so I can't give you a full brief"
    return body.strip()
