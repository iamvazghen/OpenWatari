"""Routines & modes — the cheap, non-privileged 'protocols' from the Phase-X expansion.

Two tools:

* ``routine(name, minutes)`` — runs a named behavioural routine: **briefing** (a spoken morning
  brief), **focus**/**lockdown**/**guest**/**commute** (flip a runtime mode), **panic** (go quiet +
  alert the phone), **backup** (archive his memory), **normal** (clear all modes). These are NOT the
  password-gated protocols (goodnight/phoenix/ragnarok) — they change behaviour, not the system, so
  they need no password.
* ``self_health`` — Afon reports on his own organs (vault, cache, reminder host).

All degrade gracefully and compose from existing tools, so the briefing simply skips any capability
that isn't configured.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from afon.brain.modes import MODES
from afon.brain.tools.base import tool_error
from afon.config import settings  # noqa: F401 - bench monkeypatches this module attribute.
from afon.shared.paths import state_dir

USER_TZ = ZoneInfo(settings.user_tz)
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _degraded(text: str) -> bool:
    return "isn't configured" in text or "not configured" in text or "couldn't" in text


async def _daily_briefing() -> str:
    """Compose a short spoken brief from time + weather + calendar + headlines (skip what's off)."""
    from afon.brain.tools.calendar import list_events
    from afon.brain.tools.utility import news_brief, weather

    from afon.brain import prefs

    now = datetime.now(USER_TZ)
    parts = [f"Good {('morning' if now.hour < 12 else 'afternoon' if now.hour < 18 else 'evening')}, "
             f"sir. It's {now:%A %H:%M}."]
    home = prefs.home_location()
    if home:
        w = await weather({"location": home})
        if not _degraded(w):
            parts.append(w)
    events = await list_events({"days": 1})
    if not _degraded(events):
        parts.append(events)
    news = await news_brief({})
    if not _degraded(news):
        parts.append(news)
    return " ".join(parts)


async def _backup_memory() -> str:
    """Zip the memory/ dir to backups/ with a timestamp. Returns a spoken confirmation."""
    mem = _REPO_ROOT / "memory"
    if not mem.is_dir():
        return "There's no memory folder to back up yet, sir."
    backups = _REPO_ROOT / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(USER_TZ).strftime("%Y%m%d-%H%M%S")
    base = backups / f"afon-memory-{stamp}"
    archive = shutil.make_archive(str(base), "zip", root_dir=str(mem))
    size_kb = Path(archive).stat().st_size // 1024
    return f"Backed up your memory, sir — {Path(archive).name}, about {size_kb} kilobytes."


async def routine(args: dict) -> str:
    name = (args.get("name") or "").strip().lower()
    try:
        minutes = int(args.get("minutes") or 60)
    except (TypeError, ValueError):
        minutes = 60
    try:
        if name in ("briefing", "morning", "brief"):
            return await _daily_briefing()
        if name in ("focus", "deepwork", "deep-work"):
            MODES.set_focus(minutes)
            return f"Focus mode on for {minutes} minutes, sir — I'll hold anything non-urgent."
        if name in ("lockdown", "privacy", "quiet"):
            MODES.lockdown = True
            return "Lockdown engaged, sir — I'll stay quiet and stop volunteering things until you lift it."
        if name == "guest":
            MODES.guest = True
            return "Guest mode on, sir — I'll respond to others too until you switch it off."
        if name == "commute":
            MODES.commute = True
            return "Commute mode on, sir — I'll keep it brief."
        if name in ("panic", "safe"):
            MODES.lockdown = True
            from afon.brain.tools.notify import push

            await push("Panic routine triggered.", title="Afon")
            return "Panic routine, sir — I've gone quiet and pinged your phone."
        if name == "backup":
            return await _backup_memory()
        if name in ("normal", "resume", "end", "off", "clear"):
            MODES.clear()
            return "Back to normal, sir — all modes cleared."
        return (f"I don't have a '{name}' routine, sir. I can do: briefing, focus, lockdown, guest, "
                "commute, panic, backup, or normal.")
    except Exception as e:  # noqa: BLE001
        return tool_error("routine", e)


async def set_home_location(args: dict) -> str:
    """Change (or report) the owner's current home location — a runtime variable, not a constant."""
    from afon.brain import prefs

    loc = (args.get("location") or "").strip()
    if not loc:
        cur = prefs.home_location()
        return (f"Home is currently set to {cur}, sir." if cur
                else "No home location is set yet, sir — where are you based?")
    prefs.set("home_location", loc)
    return f"Done, sir — home is now {loc}. I'll use it for weather and your daily briefing."


async def self_health(args: dict) -> str:
    try:
        from afon.brain.health import check, summarize

        snap = await check()
        modes = MODES.status()
        tail = "" if modes == "normal" else f" Current mode: {modes}."
        return summarize(snap) + tail
    except Exception as e:  # noqa: BLE001
        return tool_error("self health", e)


# ---- dynamic day planning (2026-07-28) -----------------------------------------------------------
# The owner's standing commitments (train daily, read) have NO fixed time — "it can be morning, it
# can be noon". plan_today turns his stated time for TODAY into concrete reminders (incl. a prep
# reminder — the stretch — lead_minutes before training). day_plan.json records what's planned so
# the morning planning prompt only asks about what's still open.
import json as _json

_DAY_PLAN = state_dir() / "day_plan.json"


def _load_routines() -> list[dict]:
    from afon.brain.proactive_signals import _DEFAULT_ROUTINES, _ROUTINES_PATH
    try:
        return _json.loads(_ROUTINES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt -> defaults
        return _DEFAULT_ROUTINES


def _load_day_plan(date_str: str) -> dict:
    try:
        plan = _json.loads(_DAY_PLAN.read_text(encoding="utf-8"))
        return plan if plan.get("date") == date_str else {"date": date_str}
    except Exception:  # noqa: BLE001
        return {"date": date_str}


def unplanned_commitments(local_now: datetime) -> list[dict]:
    """Dynamic routine entries with no time planned for today yet."""
    plan = _load_day_plan(str(local_now.date()))
    return [r for r in _load_routines()
            if r.get("dynamic") and r.get("key") and r["key"] not in plan]


async def plan_today(args: dict) -> str:
    """Owner said when a commitment happens today -> schedule its reminders now."""
    commitment = (args.get("commitment") or "").strip().lower()
    when = (args.get("time") or "").strip()
    if not (commitment and when):
        return "Tell me which commitment and what time today, sir — e.g. training at 12:30."
    entry = next((r for r in _load_routines()
                  if r.get("dynamic") and (commitment in r.get("key", "") or r.get("key", "") in commitment)),
                 None) or {"key": commitment, "message": f"{commitment.title()} time, sir."}
    now = datetime.now(USER_TZ)
    try:
        if ":" in when and "T" not in when and "-" not in when:
            h, m = when.split(":", 1)
            target = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        else:
            target = datetime.fromisoformat(when)
            if target.tzinfo is None:
                target = target.replace(tzinfo=USER_TZ)
    except (ValueError, TypeError):
        return f"I couldn't read '{when}' as a time, sir — give me HH:MM."
    if target <= now:
        return f"{when} today is already past, sir — did you mean tomorrow, or a later time?"
    try:
        from datetime import timedelta

        from afon.brain.tools.reminders import set_reminder

        notes = [await set_reminder({"message": entry.get("message") or f"{commitment.title()} time, sir.",
                                     "at": target.isoformat()})]
        lead = int(entry.get("lead_minutes", 0) or 0)
        prep = entry.get("prep_message")
        if lead and prep:
            prep_at = target - timedelta(minutes=lead)
            if prep_at > now:
                notes.append(await set_reminder({"message": prep, "at": prep_at.isoformat()}))
        plan = _load_day_plan(str(now.date()))
        plan[entry.get("key", commitment)] = target.strftime("%H:%M")
        _DAY_PLAN.parent.mkdir(parents=True, exist_ok=True)
        _DAY_PLAN.write_text(_json.dumps(plan), encoding="utf-8")
        extra = f" I'll cue your prep {lead} minutes before." if lead and prep and len(notes) > 1 else ""
        return f"Planned, sir — {entry.get('key', commitment)} at {target.strftime('%H:%M')}.{extra}"
    except Exception as e:  # noqa: BLE001
        return tool_error("day planning", e)


# ---- routine drafting (21.F1) --------------------------------------------------------------
# routines.json shipped with two dynamic commitments and no windowed entries, so every capability
# that reasons about "the shape of a normal day" was reasoning about an empty file. The blank page
# is why: nobody sits down and writes their own schedule out. Afon has the evidence already, so he
# drafts it and the owner corrects it.


async def propose_routines(_args: dict) -> str:
    """Draft routines from observed activity and read them back with their evidence. Never installs."""
    try:
        from afon.brain.routine_draft import observe, spoken, write_draft

        drafts = observe()
        if drafts:
            write_draft(drafts)
        return spoken(drafts)
    except Exception as e:  # noqa: BLE001
        return tool_error("routine draft", e)


async def adopt_routines(args: dict) -> str:
    """Install the drafted routines, keeping every existing entry the draft does not name.

    Confirm-gated: this is the file that decides when Afon speaks up unprompted, so replacing it is
    exactly the kind of change that must be said out loud before it happens.
    """
    try:
        from afon.brain.proactive_signals import _ROUTINES_PATH
        from afon.brain.routine_draft import read_draft, validate

        drafts = read_draft()
        if not drafts:
            return "There's no draft to adopt, sir — ask me to propose your routines first."
        keep = [r for r in _load_routines() if r.get("key") not in {d["key"] for d in drafts}]
        merged = keep + drafts
        problems = validate(merged)
        if problems:
            # Refusing here is the point: the live loader swallows a malformed entry and moves on,
            # so a bad routine does not fail — it just never fires again, silently.
            return ("I won't install that, sir — " + "; ".join(problems[:3]) +
                    ". Fix the draft and tell me again.")
        _ROUTINES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROUTINES_PATH.write_text(_json.dumps(merged, indent=2), encoding="utf-8")
        kept = f", keeping {len(keep)} you already had" if keep else ""
        return f"Adopted, sir — {len(drafts)} routines from what I've observed{kept}."
    except Exception as e:  # noqa: BLE001
        return tool_error("routine adoption", e)


async def day_clashes(args: dict) -> str:
    """Look at a day's calendar as a whole and name what will not work (21.F2)."""
    from afon.brain.tools.base import not_configured
    from afon.brain.tools.calendar import _NEEDS, configured, raw_events

    if not configured():
        # "Your day holds together" over an empty list would be a lie with no calendar attached —
        # the same overclaim as a health check that reports green because it never asked.
        return not_configured("Google Calendar", _NEEDS)
    try:
        from afon.brain.schedule import clashes, spoken

        return spoken(clashes(await raw_events(date=(args.get("date") or "").strip())))
    except Exception as e:  # noqa: BLE001
        return tool_error("schedule check", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "propose_routines",
            "description": (
                "Draft the owner's daily routines from the activity Afon has actually observed, and "
                "read them back with the evidence for each. Use for 'what does my day look like', "
                "'draft my routines', 'set up my schedule'. Proposes only — never installs."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adopt_routines",
            "description": (
                "Install the routines previously proposed, after the owner has agreed to them. Use "
                "only when he says yes to a draft you read him."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "day_clashes",
            "description": (
                "Check a day's calendar for double-bookings, journeys with no travel time, and long "
                "runs with no break. Use for 'does my day work', 'am I double-booked', 'check my "
                "schedule', 'will Friday work'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "ISO date, e.g. 2026-09-14. Omit for today."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_today",
            "description": (
                "The owner tells you WHEN a standing commitment happens TODAY — 'I'll train at "
                "noon', 'training at 19:00', 'reading around 21:30'. Schedules today's reminder(s), "
                "including the stretch prep before training. Frictionless — no confirmation needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commitment": {"type": "string",
                                   "description": "Which commitment: training, reading, ..."},
                    "time": {"type": "string", "description": "Today's time, HH:MM (owner's timezone)."},
                },
                "required": ["commitment", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "routine",
            "description": (
                "Run a named behavioural routine (not a password protocol). Names: 'briefing' (a "
                "short spoken morning brief), 'focus' (hold non-urgent interjections for N minutes), "
                "'lockdown' (go quiet/private), 'guest' (respond to others too), 'commute' (keep it "
                "brief), 'panic' (go quiet and alert his phone), 'backup' (archive your memory), "
                "'normal' (clear all modes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Routine name."},
                    "minutes": {"type": "integer", "description": "Duration for focus mode (default 60)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_home_location",
            "description": (
                "Set or report the owner's CURRENT home location (a changeable variable — e.g. Cologne "
                "normally, but Armenia or France for a summer). With no location, reports the current "
                "one. This is what 'what's the weather' and the daily briefing default to. Use when he "
                "says 'I'm in X now' / 'set home to X' / 'I'm spending the summer in X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City/place, e.g. 'Yerevan, Armenia'. Omit to report current."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_health",
            "description": (
                "Report on your own health — vault readability, cache backend, reminder host — and "
                "your current mode. Use for 'are you all right / status / are you healthy'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {"routine": routine, "set_home_location": set_home_location, "self_health": self_health,
            "plan_today": plan_today, "propose_routines": propose_routines,
            "adopt_routines": adopt_routines, "day_clashes": day_clashes}
