"""Diagnostics tool — Afon can report his own failures, out loud.

The error journal (``afon.shared.errors``) is the union of every process: the voice edge and
pc_agent on the laptop ship their entries to the brain, so one query answers "what's broken?" for
the whole system. Exposing it as a TOOL matters — it means the owner can just ask ("are you having
any trouble?", "what went wrong just now?") instead of reading logs, and Afon answers from what
actually happened rather than guessing.

Read-only and fail-quiet; degrades to a plain note when nothing has been recorded.
"""

from __future__ import annotations

from afon.brain.tools.base import tool_error
from afon.shared import errors as _err


def _speakable(e: dict) -> str:
    sub = e.get("subsystem", "?")
    typ = e.get("type") or e.get("level", "")
    msg = (e.get("message") or "").strip()
    when = (e.get("ts") or "")[11:16]        # HH:MM — a spoken answer doesn't want a full ISO stamp
    host = e.get("host", "")
    where = f" on {host}" if host and host not in sub else ""
    return f"{when} {sub}{where}: {typ + ' — ' if typ else ''}{msg}"[:220]


async def diagnose(args: dict) -> str:
    """What has gone wrong recently, across every process."""
    try:
        minutes = int(args.get("minutes") or 60)
    except (TypeError, ValueError):
        minutes = 60
    subsystem = (args.get("subsystem") or "").strip()
    turn = (args.get("turn") or "").strip()
    try:
        if turn:
            entries = _err.read(limit=40, turn=turn)
            if not entries:
                return f"Nothing was recorded for turn {turn}, sir."
            lines = "; ".join(_speakable(e) for e in entries[:8])
            return f"Turn {turn}, sir — {len(entries)} entr(ies): {lines}"

        s = _err.summary(since_minutes=minutes)
        if not s["total"]:
            window = "the last hour" if minutes == 60 else f"the last {minutes} minutes"
            return f"Nothing has failed in {window}, sir — all subsystems quiet."
        entries = _err.read(limit=8, since_minutes=minutes, subsystem=subsystem)
        worst = ", ".join(f"{k} ({v})" for k, v in list(s["by_subsystem"].items())[:4])
        levels = ", ".join(f"{v} {k.lower()}" for k, v in s["by_level"].items())
        head = (f"{s['total']} problem(s) in the last {minutes} minutes, sir — {levels}. "
                f"Worst: {worst}.")
        if entries:
            head += " Most recent: " + "; ".join(_speakable(e) for e in entries[:3])
        return head
    except Exception as e:  # noqa: BLE001
        return tool_error("diagnose", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "diagnose",
            "description": (
                "Report Afon's OWN recent failures — errors and warnings from every process "
                "(the laptop's voice edge and pc_agent, and this brain), with the subsystem that "
                "produced each. Use when the owner asks 'are you having trouble?', 'what went "
                "wrong?', 'why did that fail?', 'is anything broken?', or reports that something "
                "didn't work. Pass turn=<id> to explain one specific turn end to end. This is real "
                "recorded data — never guess about a failure when this tool can answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer",
                                "description": "How far back to look (default 60)."},
                    "subsystem": {"type": "string",
                                  "description": "Filter, e.g. 'edge', 'brain/tools', 'pipecat'."},
                    "turn": {"type": "string",
                             "description": "A turn id, to trace one turn across both machines."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {"diagnose": diagnose}
