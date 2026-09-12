"""Google Calendar tools — list and create events (Phase 11).

Same Google OAuth app as Gmail (``brain/google.py``). ``list_events`` is the backbone of the
proactive engine ("you have a standup in 10 minutes"); ``create_event`` is confirm-gated. Times are
ISO-8601; the brain's timezone is the owner's configured timezone. Degrades to a spoken note until login.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from afon.brain.google import api_get, api_post, configured
from afon.brain.tools.base import not_configured, tool_error
from afon.config import settings

_CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_NEEDS = "your Google login — run bench/google_login.py once (AFON_GOOGLE_* keys)"


def _fmt_when(ev: dict) -> str:
    """A speakable time. The old version sliced the raw ISO string to 16 chars, which cut the clock in
    half — '2026-07-31T14:00:00' was read aloud as '2026-07-31 at 14'."""
    start = ev.get("start", {})
    when = start.get("dateTime") or start.get("date") or ""
    if not when:
        return "sometime"
    try:
        dt = datetime.fromisoformat(when)
    except ValueError:
        return when
    if not start.get("dateTime"):          # all-day event: no clock to speak
        return f"on {dt.strftime('%a %d %B')}"
    # "Today" is the owner's day, not the machine's (21.F3). The brain runs on a UTC VPS and he
    # does not, so reading the process clock meant that between midnight and 02:00 his time,
    # tonight's events were spoken as "tomorrow" — correct for the server, wrong for the person
    # being spoken to. Two hours a day of confidently wrong answers, every day, invisible in a
    # suite that only ever ran in the afternoon.
    tz = ZoneInfo(settings.user_tz)
    local = dt.astimezone(tz) if dt.tzinfo else dt
    delta = (local.date() - datetime.now(tz).date()).days
    day = {0: "today", 1: "tomorrow"}.get(delta) or f"on {local.strftime('%a %d %B')}"
    return f"{day} at {local.strftime('%H:%M')}"


async def list_events(args: dict) -> str:
    if not configured():
        return not_configured("Google Calendar", _NEEDS)
    try:
        days = int(args.get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    try:
        max_n = int(args.get("max") or 10)
    except (TypeError, ValueError):
        max_n = 10
    # A specific day ("what's on tomorrow?", "am I free Friday?") needs its OWN window, in the owner's
    # timezone. Without it the only expressible query was "the next N days from right now", so asking
    # about tomorrow answered about today (verified live 2026-07-30) — the answer even said "today".
    date_arg = (args.get("date") or "").strip()
    day_label = ""
    if date_arg:
        try:
            tz = ZoneInfo(settings.user_tz)
            day = datetime.fromisoformat(date_arg).date()
            start_dt = datetime.combine(day, time.min, tzinfo=tz)
            end_dt = start_dt + timedelta(days=1)
            today = datetime.now(tz).date()
            day_label = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(
                (day - today).days, day.strftime("%A %d %B"))
        except ValueError:
            return (f"I couldn't read '{date_arg}' as a date, sir — give me one like "
                    f"{datetime.now(ZoneInfo(settings.user_tz)).date().isoformat()}.")
    else:
        start_dt = datetime.now(timezone.utc)
        try:                       # a short "what's imminent" window, used by the prep signal
            minutes = int(args.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        end_dt = start_dt + (timedelta(minutes=minutes) if minutes > 0 else timedelta(days=days))
    try:
        data = await api_get(
            _CAL,
            params={
                "timeMin": start_dt.isoformat(),
                "timeMax": end_dt.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max_n,
            },
        )
        events = data.get("items") or []
        if not events:
            window = day_label or ("today" if days <= 1 else f"the next {days} days")
            return f"Nothing on your calendar for {window}, sir."
        parts = [f"{ev.get('summary', '(busy)')} {_fmt_when(ev)}" for ev in events[:max_n]]
        return f"You have {len(parts)} event(s), sir: " + "; ".join(parts)
    except Exception as e:  # noqa: BLE001
        return tool_error("calendar read", e)


async def raw_events(date: str = "", days: int = 1, max_n: int = 25) -> list[dict]:
    """One day's events as Google returns them (21.F2).

    `list_events` composes a spoken sentence, which is right for an answer and useless as input:
    re-parsing "dentist on Tue 11 August at 10:00" back into datetimes would be a second date parser
    to keep in step with the first. The clash checker needs the objects.
    """
    tz = ZoneInfo(settings.user_tz)
    try:
        day = datetime.fromisoformat(date).date() if date else datetime.now(tz).date()
    except ValueError:
        day = datetime.now(tz).date()
    start_dt = datetime.combine(day, time.min, tzinfo=tz)
    data = await api_get(
        _CAL,
        params={
            "timeMin": start_dt.isoformat(),
            "timeMax": (start_dt + timedelta(days=max(1, days))).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max_n,
        },
    )
    return data.get("items") or []


def _rfc3339(value: str) -> str:
    """Local ISO datetime -> the seconds-bearing form Google's API demands. Unparseable input is
    passed through untouched so the API's own error still surfaces rather than a mangled value."""
    try:
        return datetime.fromisoformat(value.strip()).replace(microsecond=0).isoformat()
    except (ValueError, AttributeError):
        return value


async def create_event(args: dict) -> str:
    if not configured():
        return not_configured("Google Calendar", _NEEDS)
    summary = (args.get("summary") or "").strip()
    start = (args.get("start") or "").strip()
    end = (args.get("end") or "").strip()
    if not (summary and start):
        return "I need at least a title and a start time to add that, sir."
    if not end:
        # Default to a one-hour block when no end is given.
        try:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        except ValueError:
            end = start
    # Google rejects a dateTime without seconds with a bare "400 Bad Request" — and '2026-06-12T15:00'
    # is exactly the shape an LLM produces (our own schema example showed it), so ordinary requests
    # like "put gym in my calendar at three" failed every time. Normalise instead of relying on the
    # model to get RFC3339 right.
    start, end = _rfc3339(start), _rfc3339(end)
    try:
        body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": settings.user_tz},
            "end": {"dateTime": end, "timeZone": settings.user_tz},
        }
        url = _CAL
        if args.get("meet"):
            # A Google Meet link rides the same calendar write — no separate Zoom/Meet integration
            # needed for "schedule a call with X". conferenceDataVersion=1 activates creation.
            import uuid
            body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex}}
            url = _CAL + "?conferenceDataVersion=1"
        created = await api_post(url, body)
        note = f"Added '{summary}' to your calendar, sir, {_fmt_when({'start': {'dateTime': start}})}."
        link = (created or {}).get("hangoutLink")
        if link:
            note += f" Meet link ready: {link}"
        return note
    except Exception as e:  # noqa: BLE001
        return tool_error("calendar create", e)


async def calendar_signals():
    """Proactive signal source: a heads-up for each timed event starting in the next ~15 min.

    This is the calendar 'backbone' the proactive engine was always meant to tick over (it had only
    health signals wired, so it stayed silent whenever the system was healthy). Fail-quiet: not
    logged in, no events, or any API error -> no signals. Repeat-suppression (by event id, in the
    engine) keeps it to one nudge per event even though the 5-min tick re-sees the 15-min window."""
    from afon.brain.proactive import Signal  # lazy: avoid a calendar<->proactive import cycle

    if not configured():
        return []
    now = datetime.now(timezone.utc)
    try:
        data = await api_get(_CAL, params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(minutes=15)).isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 5,
        })
    except Exception:  # noqa: BLE001 — a broken source must never throw into the tick loop
        return []
    out = []
    for ev in data.get("items") or []:
        start = (ev.get("start") or {}).get("dateTime")  # timed events only; skip all-day
        if not start:
            continue
        try:
            when = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        mins = max(0, round((when - now).total_seconds() / 60))
        title = ev.get("summary", "an event")
        msg = f"Sir, {title} is starting now." if mins == 0 else f"Sir, {title} starts in {mins} minute(s)."
        out.append(Signal(key=f"cal-{ev.get('id', start)}", kind="calendar", urgency=0.75, message=msg))
    return out


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "List Google Calendar events. For ONE named day ('tomorrow', 'Friday', 'the 3rd') "
                "pass date=YYYY-MM-DD — compute it from today yourself; do NOT use days for that, "
                "which only looks forward from now and would answer about today. Use days for a "
                "window: 'what's on this week' (days=7), 'my next meeting' (days=1)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "A single day to read, ISO YYYY-MM-DD (owner's timezone)."},
                    "days": {"type": "integer", "description": "Look-ahead window in days (default 1)."},
                    "max": {"type": "integer", "description": "Max events to read (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a Google Calendar event. Confirm the title and time with the owner first. "
                "start/end are ISO-8601 local times (e.g. '2026-06-12T15:00'); end defaults to +1h. "
                "Set meet=true to attach a Google Meet video link ('schedule a call with X')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title."},
                    "start": {"type": "string", "description": "ISO-8601 start, e.g. 2026-06-12T15:00."},
                    "end": {"type": "string", "description": "ISO-8601 end (optional; defaults +1h)."},
                    "meet": {"type": "boolean", "description": "Attach a Google Meet link."},
                },
                "required": ["summary", "start"],
            },
        },
    },
]

HANDLERS = {"list_events": list_events, "create_event": create_event}
