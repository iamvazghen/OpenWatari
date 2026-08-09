"""Reminder tools — Afon's proactive memory (Phase 4).

Thin tool wrappers over ``brain/scheduler.py``. Afon sets one-shot or daily reminders that
later fire and get spoken (if he's running) and/or pushed to the phone via ntfy.
"""

from __future__ import annotations

import re

from loguru import logger

from afon.brain.scheduler import SCHEDULER
from afon.brain.tools.base import tool_error
from afon.config import settings

# A relative delay phrase a model may put anywhere ("in 90 minutes", "90 min", "in 2 hours", "in a day").
_REL_DELAY_RE = re.compile(r"\b(?:in\s+)?(\d+(?:\.\d+)?)\s*(month|week|day|hour|hr|minute|min|sec|h|m|d)s?\b", re.I)
_REL_UNIT_MIN = {"month": 43200, "week": 10080, "day": 1440, "d": 1440, "hour": 60, "hr": 60, "h": 60,
                 "minute": 1, "min": 1, "m": 1, "sec": 1 / 60}


def _rel_to_minutes(text: str | None) -> float | None:
    """Parse a relative delay ('in 90 minutes', '2 hours') to minutes, or None if it isn't one."""
    m = _REL_DELAY_RE.search(text or "")
    if not m:
        return None
    return float(m.group(1)) * _REL_UNIT_MIN[m.group(2).lower()]


def _normalize_reminder_args(args: dict) -> tuple[str, float | None, str | None, str | None]:
    """Tolerate the arg shapes different models emit so a reminder never fails on formatting.

    The non-thinking primary + fallbacks variously send the text as message/text/reminder/task, the
    delay as a numeric string, or a relative phrase ('in 90 minutes') in `at`/`when` — which the old code
    fed straight to `datetime.fromisoformat`, raising 'I couldn't set your reminder'. Returns
    (message, in_minutes, at, daily) with a relative `at`/`when` folded into in_minutes and `at` kept
    only if it's an absolute time."""
    message = ""
    for k in ("message", "text", "reminder", "task", "about", "what", "content"):
        if (v := (args.get(k) or "").strip()):
            message = v
            break
    in_minutes = args.get("in_minutes")
    if in_minutes in (None, "") and args.get("minutes") not in (None, ""):
        in_minutes = args.get("minutes")
    if isinstance(in_minutes, str):
        in_minutes = float(in_minutes) if re.fullmatch(r"\d+(?:\.\d+)?", in_minutes.strip()) else None
    at = (args.get("at") or args.get("time") or args.get("when") or "").strip() or None
    daily = (args.get("daily") or "").strip() or None
    # A relative phrase landed in at/when: fold it into a delay so it never reaches fromisoformat.
    if in_minutes is None and at:
        try:
            from datetime import datetime
            datetime.fromisoformat(at)          # already an absolute ISO time -> leave it for the scheduler
        except ValueError:
            rel = _rel_to_minutes(at)
            if rel is not None:
                in_minutes, at = rel, None
    return message, in_minutes, at, daily


async def _register_daily_with_ticker(job_id: str, message: str, daily: str) -> bool:
    """Best-effort: mirror a daily reminder to the always-on VPS ticker. Never raises."""
    if not settings.ticker_url:
        return False
    try:
        import httpx

        headers = {}
        if settings.ticker_token:
            headers["Authorization"] = f"Bearer {settings.ticker_token}"
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
            r = await c.post(
                f"{settings.ticker_url.rstrip('/')}/reminders",
                json={"id": job_id, "message": message, "daily": daily},
                headers=headers,
            )
            r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ticker register failed: {type(e).__name__}: {e}")
        return False


async def _cancel_on_ticker(job_id: str) -> None:
    """Best-effort: drop a reminder from the VPS ticker too. Never raises."""
    if not settings.ticker_url:
        return
    try:
        import httpx

        headers = {}
        if settings.ticker_token:
            headers["Authorization"] = f"Bearer {settings.ticker_token}"
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
            await c.post(
                f"{settings.ticker_url.rstrip('/')}/reminders/cancel",
                json={"id": job_id}, headers=headers,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ticker cancel failed: {type(e).__name__}: {e}")


async def set_reminder(args: dict) -> str:
    message, in_minutes, at, daily = _normalize_reminder_args(args)
    if not message:
        return "What should I remind you about, sir?"
    if in_minutes is None and not at and not daily:
        return "When should I remind you, sir? Give me a delay, a time, or a daily time."
    try:
        job_id, when, ntfy_epoch = SCHEDULER.add_reminder(
            message, in_minutes=in_minutes, at=at, daily=daily
        )
        # Phase 4b — hand one-shot phone delivery to ntfy's server-side scheduler so it reaches
        # the phone even if the PC is off at fire time. If ntfy refuses, fall back to the
        # in-process push (re-enable push_phone on the job).
        suffix = ""
        if ntfy_epoch is not None:
            from afon.brain.tools.notify import push

            if await push(message, title="Reminder", at=ntfy_epoch):
                suffix = " I've also queued it to your phone, so it'll reach you even if this PC is off."
            else:
                SCHEDULER.set_push_phone(job_id, True)
        elif daily:
            # Recurring reminders can't be held by ntfy; register with the always-on VPS ticker
            # (if configured) so they still push with the PC off (Phase 4b).
            if await _register_daily_with_ticker(job_id, message, daily):
                suffix = " I've also registered it on the always-on host, so it'll fire even if this PC is off."
        return f"Done, sir — I'll remind you {when}: \"{message}\".{suffix} (id {job_id[:8]})"
    except Exception as e:  # noqa: BLE001
        return tool_error("reminder", e)


async def list_reminders(args: dict) -> str:
    try:
        jobs = SCHEDULER.list_reminders()
        if not jobs:
            return "You have no reminders set, sir."
        lines = [f"- {name} — next {when} (id {jid[:8]})" for jid, name, when in jobs]
        return f"You have {len(jobs)} reminder(s), sir:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return tool_error("reminder list", e)


async def cancel_reminder(args: dict) -> str:
    job_id = (args.get("id") or "").strip()
    if not job_id:
        return "Which reminder should I cancel, sir? Tell me its id from the list."
    try:
        # Allow a short id prefix from the spoken list.
        full = next((jid for jid, _, _ in SCHEDULER.list_reminders() if jid.startswith(job_id)), job_id)
        ok = SCHEDULER.cancel(full)
        await _cancel_on_ticker(full)  # best-effort: also drop it from the always-on host
        return "Cancelled, sir." if ok else f"I couldn't find a reminder '{job_id}', sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("reminder cancel", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Set a reminder that will be spoken (and pushed to his phone) when it fires. "
                "Give exactly one timing: in_minutes (delay), at (ISO datetime like "
                "'2026-06-11T08:00'), or daily ('HH:MM' for a recurring daily reminder)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "What to remind him about."},
                    "in_minutes": {"type": "number", "description": "Fire after this many minutes."},
                    "at": {"type": "string", "description": "Absolute time, ISO-8601."},
                    "daily": {"type": "string", "description": "Recurring daily time 'HH:MM'."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List pending reminders with each one's next fire time and short id. Use "
                           "for 'what reminders do I have', 'what am I being reminded about'. Get "
                           "the id here before cancel_reminder.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel ONE pending reminder by its id (a short prefix from "
                           "list_reminders works). Use for 'cancel that reminder', 'drop the 6pm "
                           "one'. Call list_reminders first if you don't have the id.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Reminder id (or prefix)."}},
                "required": ["id"],
            },
        },
    },
]

HANDLERS = {
    "set_reminder": set_reminder,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
}
