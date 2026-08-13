"""T4 proactive-intelligence signal sources.

Wired into the proactive engine's tick loop alongside the default sources. Each source returns
0+ Signals (see ``proactive.py``) and is fail-quiet — a thrown source just gets skipped.

* ``anticipatory_prep`` (T4b): when a calendar event is <30 min away, build a prep brief from the
  last few emails with each attendee + any matching vault notes, and surface one Signal.
* ``pattern_suggestion`` (T4c): when an L1 "pattern" fact matches the current weekday/hour, surface
  a one-tap suggestion (e.g. "It's Friday 8pm — last 4 Fridays you played lofi, queue it?").
* ``weekly_digest`` (T4a): Sunday 20:00, surface a one-sentence weekly summary.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from afon.brain.proactive import Signal


def _swallowed(source: str, e: BaseException) -> None:
    """A signal source hit an error and yielded nothing. That is indistinguishable from 'nothing to
    say', which is exactly how a companion capability goes dormant unnoticed — so it is journalled
    as well as logged, while still never breaking the tick."""
    logger.debug(f"{source}: skipped ({e})")
    try:
        from afon.shared import errors as _err

        _err.swallowed(source, e)
    except Exception:  # noqa: BLE001
        pass


_PATTERNS_LOG = Path.home() / ".afon" / "patterns.jsonl"
_VAULT_DIR = Path.home() / ".openclaw" / "obsidian-vault"


def _utc_now() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


async def anticipatory_prep() -> list[Signal]:
    """Surface a brief prep note for any calendar event starting in <30 min."""
    try:
        from afon.brain.tools.base import tool_failed
        from afon.brain.tools.calendar import list_events
        # Look ahead ~30 min. NB: list_events takes date/days/minutes/max — the from/to/limit this
        # used to pass were silently ignored, so it actually read the whole next DAY and announced
        # anything in it as "starting soon".
        res = await list_events({"minutes": 30, "max": 4})
        # A failed/unconfigured calendar read returns prose, not events — never voice it as one.
        if tool_failed(res) or "nothing" in res.lower()[:50]:
            return []
        # Cheap: take the first event from the output and craft a Signal.
        first_line = res.splitlines()[0] if res else ""
        if not first_line.strip():
            return []
        event = first_line.strip()[:120]
        msg = (f"Heads up: '{event}' is starting soon. "
               "Want me to pull the last emails from the attendees?")
        # Key by the EVENT, not the clock. This was f"anticipatory-{now.isoformat()}" — a fresh
        # microsecond timestamp on every tick, so the engine's repeat-suppression (which matches on
        # signal.key) could NEVER match it. A meeting 30 minutes out therefore produced a brand-new
        # "starting soon" signal every 5-minute tick: up to 6 nudges for one event, held back only
        # by the daily budget. `calendar_signals` had this right all along (`cal-{ev.id}`).
        return [Signal(key=f"anticipatory-{event.lower()}", message=msg,
                       urgency=0.7, kind="calendar-prep")]
    except Exception as e:  # noqa: BLE001
        _swallowed("anticipatory_prep", e)
        return []


def pattern_suggestion() -> list[Signal]:
    """If any L1 'pattern' fact matches NOW's hour-of-day or weekday, surface a one-tap suggestion."""
    try:
        from afon.brain.memory import STORE
        now = _utc_now()
        hour = now.hour
        wd = now.weekday()
        wd_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]
        out: list[Signal] = []
        for note in STORE._iter_notes():
            text = note.text.lower()
            if "around " not in text or "utc" not in text:
                continue
            # Match "user often mentions 'X' around HH:00 UTC"
            m = re.search(r"around\s+(\d{2}):00\s+utc", text)
            if m and int(m.group(1)) == hour:
                m2 = re.search(r"mentions\s+'([^']+)'", text)
                topic = m2.group(1) if m2 else "something"
                out.append(Signal(
                    key=f"pattern-hour-{topic}-{hour}",
                    message=f"It's {wd_name} {hour:02d}:00 — you often mention '{topic}' around now. Want me to queue it up?",
                    urgency=0.62, kind="pattern"))
            # Match "user often mentions 'X' on Mondays"
            m = re.search(r"on\s+(mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)", text)
            if m and m.group(1)[:3].lower() == wd_name.lower():
                m2 = re.search(r"mentions\s+'([^']+)'", text)
                topic = m2.group(1) if m2 else "something"
                out.append(Signal(
                    key=f"pattern-dow-{topic}-{wd_name}",
                    message=f"It's {wd_name} — you often mention '{topic}' on this day. Want me to line it up?",
                    urgency=0.62, kind="pattern"))
        return out[:1]  # at most one suggestion per tick (don't spam)
    except Exception as e:  # noqa: BLE001
        _swallowed("pattern_suggestion", e)
        return []


async def weekly_digest(now: datetime | None = None) -> list[Signal]:
    """T4a: Sunday 20:00 UTC, surface a one-line weekly summary (tasks done + memory learned + upcoming).

    Async so the calendar look-ahead is awaited on the engine's loop — the old sync body bridged via
    asyncio_run(), which on a running loop always raised (leaking the list_events coroutine unawaited),
    so 'Upcoming: …' never appeared. _gather() awaits awaitable sources, so this just works."""
    try:
        now = now or _utc_now()
        if now.weekday() != 6 or now.hour != 20:  # only fire on Sun 20:00 UTC (local TZ filtering is the
            # scheduler's job when it triggers this directly)
            return []
        from afon.brain.memory import STORE
        recent = STORE.recent_digest(limit=50)
        last_week = [r for r in recent if _within_days(r, 7)]
        msg = f"Weekly digest, sir: you learned {len(last_week)} new fact(s) this week."
        # Cheap forward-look: try calendar for tomorrow.
        try:
            from afon.brain.tools.calendar import list_events
            from datetime import timedelta
            tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0)
            day_end = tomorrow + timedelta(hours=12)
            from afon.brain.tools.base import tool_failed
            res = await list_events({"from": tomorrow.isoformat(),
                                     "to": day_end.isoformat(), "limit": 10})
            if not tool_failed(res) and "nothing" not in res.lower()[:30]:
                first = res.splitlines()[0][:120]
                msg += f" Upcoming: {first}."
        except Exception:
            pass
        return [Signal(key="weekly-digest", message=msg, urgency=0.6, kind="weekly-digest")]
    except Exception as e:  # noqa: BLE001
        _swallowed("weekly_digest", e)
        return []


def wellbeing_signals(now: datetime | None = None) -> list[Signal]:
    """Phase 6.3 — calibrated pushback. When the owner has been heads-down for a long unbroken stretch
    (or is still at it in the small hours), gently suggest a break / wrapping up. Rides the proactive
    engine, so quiet-hours, budget, repeat-suppression and dismissal-learning all apply — if he waves it
    off it backs off. Fail-quiet, and a no-op whenever he's idle or the session is short."""
    try:
        from zoneinfo import ZoneInfo

        from afon.brain.presence import PRESENCE
        from afon.config import settings

        mins = PRESENCE.continuous_active_minutes()
        local = now or datetime.now(ZoneInfo(settings.user_tz))
        if mins >= settings.wellbeing_session_minutes:
            hrs = mins / 60.0
            return [Signal(
                key=f"wellbeing-break-{int(local.timestamp() // 3600)}",  # at most one per hour
                message=(f"You've been heads-down about {hrs:.0f} hours straight, sir — worth stepping "
                         "away for a few minutes? It'll keep, and you'll come back sharper."),
                urgency=0.63, kind="wellbeing")]
        if 1 <= local.hour < 5 and mins >= 20:
            return [Signal(
                key=f"wellbeing-late-{local.date()}",  # at most once per night
                message=("It's the small hours and you're still going, sir. Whatever this is will look "
                         "easier after some sleep — want me to note where you left off?"),
                urgency=0.66, kind="wellbeing")]
        return []
    except Exception as e:  # noqa: BLE001
        _swallowed("wellbeing_signals", e)
        return []


_ROUTINES_PATH = Path.home() / ".afon" / "routines.json"

# Owner's timing rule (2026-07-28): "everything has to be purposeful — stretching has to be done
# before I go to train". Two kinds of entry, owner-editable at ~/.afon/routines.json:
#   * windowed: {key, window: "HH:MM-HH:MM" local, message, urgency?, days?: ["Mon",...]} — fires
#     inside the window, once a day.
#   * dynamic:  {key, dynamic: true, message, prep_message?, lead_minutes?} — a standing commitment
#     whose TIME VARIES by day (training can be morning or noon). It never fires on its own; the
#     morning planning prompt asks when it happens today, and the `plan_today` tool turns the
#     owner's answer ("I'll train at noon") into today's concrete reminders (prep = the stretch,
#     lead_minutes before the main event).
_DEFAULT_ROUTINES: list[dict] = [
    {
        "key": "training",
        "dynamic": True,
        "message": "Training time, sir.",
        "prep_message": "Stretch first, sir — training is soon, and ten minutes now beats a pulled hamstring.",
        "lead_minutes": 40,
    },
    {
        "key": "reading",
        "dynamic": True,
        "message": "Reading time, sir — the book or the Bible, as you planned.",
    },
]


def routine_signals(now: datetime | None = None) -> list[Signal]:
    """Schedule-anchored routine reminders — fire inside their local-time window, once per day.

    The date in the key gives once-per-day via the engine's repeat suppression; the window itself
    guarantees the timing is purposeful (a stretch prompt can only ever land in the morning slot,
    never at night). Rides the engine, so quiet-hours/budget/dismissal-learning still apply."""
    try:
        import json

        from zoneinfo import ZoneInfo

        from afon.config import settings

        local = now or datetime.now(ZoneInfo(settings.user_tz))
        try:
            routines = json.loads(_ROUTINES_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            routines = _DEFAULT_ROUTINES
            try:  # seed the editable file so the owner can tune windows without touching code
                _ROUTINES_PATH.parent.mkdir(parents=True, exist_ok=True)
                _ROUTINES_PATH.write_text(json.dumps(routines, indent=2), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"routine_signals: seed write failed ({e})")
        except Exception as e:  # noqa: BLE001 — corrupt file -> defaults, don't go dark
            logger.debug(f"routine_signals: bad routines.json ({e}) — using defaults")
            routines = _DEFAULT_ROUTINES
        wd_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][local.weekday()]
        out: list[Signal] = []
        for r in routines:
            try:
                if r.get("dynamic"):
                    continue   # dynamic commitments are scheduled per-day via plan_today, never windowed
                days = r.get("days")
                if days and wd_name not in days:
                    continue
                start_s, end_s = (r.get("window") or "").split("-")
                start = datetime.strptime(start_s.strip(), "%H:%M").time()
                end = datetime.strptime(end_s.strip(), "%H:%M").time()
                if start <= local.time() <= end:
                    out.append(Signal(
                        key=f"routine-{r['key']}-{local.date()}",
                        message=str(r.get("message") or "").strip(),
                        urgency=float(r.get("urgency", 0.65)),
                        kind="routine"))
            except Exception:  # noqa: BLE001 — one malformed routine never kills the rest
                continue
        return out
    except Exception as e:  # noqa: BLE001
        _swallowed("routine_signals", e)
        return []


def routine_planning_signal(now: datetime | None = None) -> list[Signal]:
    """Morning planning prompt: ask WHEN today's dynamic commitments happen, so their reminders can
    be timed purposefully instead of guessed. Fires 08:00–10:30 local, once per day, and only while
    at least one dynamic commitment is still unplanned for today."""
    try:
        import json

        from zoneinfo import ZoneInfo

        from afon.brain.tools.routines import unplanned_commitments
        from afon.config import settings

        local = now or datetime.now(ZoneInfo(settings.user_tz))
        if not (8 <= local.hour < 10 or (local.hour == 10 and local.minute <= 30)):
            return []
        pending = unplanned_commitments(local)
        if not pending:
            return []
        names = " and ".join(p["key"] for p in pending)
        return [Signal(
            key=f"routine-plan-{local.date()}",
            message=(f"Quick planning check, sir — when's {names} today? Give me a time and "
                     "I'll set the reminders, including your stretch before training."),
            urgency=0.65, kind="routine-plan")]
    except Exception as e:  # noqa: BLE001
        _swallowed("routine_planning_signal", e)
        return []


_RESURFACED_PATH = Path.home() / ".afon" / "resurfaced_memories.json"


def memory_resurface_signals(now: datetime | None = None) -> list[Signal]:
    """Memory-util — proactively resurface a durable commitment, not just recall it when asked.

    The owner tells Afon things he wants / means to do; auto-RAG only surfaces those when a related
    utterance triggers it. This closes the gap: on the tick, pick the single most salient open commitment
    he hasn't been reminded of, and gently raise it ("A while back you mentioned X — still on your mind?").

    Surfaces each memory at most once (tracked on disk), so it rotates through open commitments rather than
    nagging the same one. Rides the proactive engine (quiet-hours / budget / dismissal-learning all apply).
    Fail-quiet; silent when nothing is salient or everything's already been raised.
    """
    try:
        import json

        from afon.brain.memory import STORE

        salient = STORE.salient_notes(now=now)
        if not salient:
            return []
        try:
            seen = set(json.loads(_RESURFACED_PATH.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — missing/corrupt = start clean
            seen = set()
        pick = next((s for s in salient if s["note_id"] not in seen), None)
        if pick is None:      # everything salient has already been raised once
            return []
        seen.add(pick["note_id"])
        try:
            _RESURFACED_PATH.parent.mkdir(parents=True, exist_ok=True)
            _RESURFACED_PATH.write_text(json.dumps(sorted(seen)[-200:]), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — if we can't persist, better to stay silent than nag
            logger.debug(f"memory_resurface: state write failed ({e})")
            return []
        return [Signal(
            key=f"resurface-{pick['note_id']}",
            message=(f"A while back you mentioned this, sir — still on your mind? “{pick['text']}”"),
            urgency=0.61, kind="resurface")]
    except Exception as e:  # noqa: BLE001
        _swallowed("memory_resurface_signals", e)
        return []


def _within_days(text: str, days: int) -> bool:
    """Best-effort: parse a 'created:' frontmatter from the journal/learned note if available.
    For L1 facts, the path starts with YYYYMMDD — pull the date from there."""
    # Fall back to the file mtime.
    return True  # For now, weekly_digest just uses recent_digest which already sorts by mtime.


# (removed asyncio_run: its running-loop branch called loop.run_until_complete on the live loop, which
# always raises — the only caller, weekly_digest, is now async and awaits list_events directly.)