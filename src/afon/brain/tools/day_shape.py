"""The shape of a day: drafting the owner's routines, and checking a day holds together (S21).

Three tools, deliberately in their own module rather than in `routines.py`. That module is core —
advertised on every single turn — and these are asked for a few times a month. Adding them there
pushed the per-turn tool surface from 56 to 59 and the fine-tuning guard caught it, which is what
the guard is for: every schema on the surface is prefill tokens on every turn of every day.

  * ``propose_routines`` — draft his routines from observed activity, with the evidence (21.F1)
  * ``adopt_routines``   — install a draft he has agreed to (confirm-gated)
  * ``day_clashes``      — name what will not work about a day before it starts (21.F2)
"""

from __future__ import annotations

import json as _json

from afon.brain.tools.base import tool_error


def _live_routines() -> list[dict]:
    from afon.brain.tools.routines import _load_routines

    return _load_routines()


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
        keep = [r for r in _live_routines() if r.get("key") not in {d["key"] for d in drafts}]
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
]

HANDLERS = {
    "propose_routines": propose_routines,
    "adopt_routines": adopt_routines,
    "day_clashes": day_clashes,
}
