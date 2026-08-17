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


async def build_body() -> str:
    """Compose the digest body (no greeting): past-due tasks + important email. '' if nothing.

    Sources, each fail-quiet: Notion tasks DB (overdue + due-today), the local task queue (todos
    with a past deadline), and Gmail (important unread). Deliberately compact — it's spoken."""
    overdue: list[str] = []
    due_today: list[str] = []

    try:  # Notion tasks dashboard
        from afon.brain.tools.notion import overdue_and_today

        overdue, due_today = await overdue_and_today()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: notion source failed: {e}")

    try:  # local task-queue todos whose deadline has passed
        from afon.brain.tasks import TASKS

        now_ts = time.time()
        for t in TASKS.todos():
            if t.deadline is not None and t.deadline < now_ts:
                overdue.append(f"{t.title} ({t.human_deadline()})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: local queue source failed: {e}")

    overdue = list(dict.fromkeys(overdue))

    email_phrase = ""
    try:  # important unread email
        from afon.brain.tools.gmail import important_email_phrase

        email_phrase = await important_email_phrase()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: gmail source failed: {e}")

    lead: list[str] = []
    if overdue:
        lead.append(f"{len(overdue)} past-due task{'s' if len(overdue) != 1 else ''}: "
                    + "; ".join(overdue[:8]))
    if due_today:
        lead.append(f"{len(due_today)} due today: " + "; ".join(due_today[:8]))
    body = ". ".join(lead)
    if email_phrase:
        body = (body + ". And " + email_phrase) if body else ("You have " + email_phrase)
    return body.strip()
