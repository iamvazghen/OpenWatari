"""Activity / screen-time tools (Phase 0 companion).

Voice access to what the presence layer (``brain/presence.py``) has been observing on the laptop:
  * ``screen_time`` — how long you've spent, by app/category, today or a past day;
  * ``current_activity`` — what you're doing right this second (context);
  * ``activity_tracking`` — the privacy off-switch (status / pause / resume).

All local; nothing leaves the machine. Degrades to a plain note if no laptop is attached / no samples.
"""

from __future__ import annotations

from afon.brain.presence import PRESENCE
from afon.brain.tools.base import tool_error


def _day_offset(scope: str) -> int:
    s = (scope or "today").strip().lower()
    if s in ("yesterday", "prev", "previous"):
        return -1
    return 0


async def screen_time(args: dict) -> str:
    try:
        return PRESENCE.report(_day_offset(args.get("scope") or "today"))
    except Exception as e:  # noqa: BLE001
        return tool_error("screen time", e)


async def current_activity(_args: dict) -> str:
    try:
        return PRESENCE.current_line()
    except Exception as e:  # noqa: BLE001
        return tool_error("current activity", e)


async def activity_tracking(args: dict) -> str:
    action = (args.get("action") or "status").strip().lower()
    try:
        if action in ("pause", "off", "stop", "disable"):
            PRESENCE.set_enabled(False)
            return "Activity tracking paused, sir — I'll stop watching your screen until you resume it."
        if action in ("resume", "on", "start", "enable"):
            PRESENCE.set_enabled(True)
            return "Activity tracking resumed, sir."
        return (f"Activity tracking is {'on' if PRESENCE.enabled else 'paused'}, sir. "
                "It's all local — nothing leaves this machine.")
    except Exception as e:  # noqa: BLE001
        return tool_error("activity tracking", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "screen_time",
            "description": ("Report how long the owner has spent on the computer today (or a past "
                            "day), broken down by app and category. Use for 'how much screen time "
                            "today?', 'what have I been doing?', 'how long was I on YouTube?'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["today", "yesterday"],
                              "description": "Which day (default today)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_activity",
            "description": ("What the owner is doing on the computer right now (the foreground app + "
                            "window, or how long he's been idle). Use for 'what am I doing?', 'am I "
                            "focused?', or before deciding whether now is a good moment."),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "activity_tracking",
            "description": ("Turn the local activity/screen-time tracking on or off, or report its "
                            "status. Use for 'pause activity tracking', 'stop watching my screen', "
                            "'resume tracking', 'is tracking on?'. It's the privacy switch."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "pause", "resume"]},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {
    "screen_time": screen_time,
    "current_activity": current_activity,
    "activity_tracking": activity_tracking,
}
