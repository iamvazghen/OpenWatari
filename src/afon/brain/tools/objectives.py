"""Multi-day objective tools (Phase 4.1) — hand Afon a goal to DRIVE across days, and check on it.

Voice access to the objective book (``brain/objectives.py``):
  * ``assign_objective``   — "take this on: get Party Map launch-ready" -> Afon owns + advances it daily
  * ``list_objectives``    — "what are you working on?" -> active objectives + their latest progress
  * ``objective_status``   — "how's the Party Map objective going?" -> latest progress + any approvals owed
  * ``complete_objective`` / ``drop_objective`` — mark one done / stop driving it

The autonomous daily advance (bounded worker, outward steps deferred) runs via the scheduler; these just
let the owner manage the list. Fail-quiet.
"""

from __future__ import annotations

from afon.brain.objectives import OBJECTIVES
from afon.brain.tools.base import missing_arg, tool_error


async def assign_objective(args: dict) -> str:
    text = (args.get("objective") or args.get("text") or "").strip()
    if not text:
        return missing_arg("assign_objective", args, "objective", "text", "goal", "task",
                           ask="What objective would you like me to take on, sir?")
    try:
        obj = OBJECTIVES.assign(text, project=(args.get("project") or "").strip())
    except Exception as e:  # noqa: BLE001
        return tool_error("assign objective", e)
    return (f"I'll take that on and drive it, sir — \"{obj.text}\". I'll advance the safe parts a step "
            "each day and report progress; anything that needs your sign-off I'll hold for you.")


async def list_objectives(args: dict) -> str:
    try:
        active = OBJECTIVES.active()
    except Exception as e:  # noqa: BLE001
        return tool_error("list objectives", e)
    if not active:
        return "You haven't handed me any objectives to drive yet, sir. Say 'take this on' with a goal."
    return "Here's what I'm driving, sir:\n" + OBJECTIVES.render()


async def objective_status(args: dict) -> str:
    topic = (args.get("topic") or args.get("objective") or "").strip()
    try:
        obj = OBJECTIVES.find(topic)
    except Exception as e:  # noqa: BLE001
        return tool_error("objective status", e)
    if obj is None:
        if not topic:
            return "Which objective, sir?"
        return f"I couldn't find a single active objective matching '{topic}', sir."
    if not obj.progress:
        line = f"I'm on \"{obj.text}\", sir, but haven't logged progress yet."
    else:
        line = f"On \"{obj.text}\", sir — latest: {obj.progress[-1]['note']}"
    if obj.deferred:
        line += f" Waiting on your approval: {'; '.join(obj.deferred[:3])}."
    return line


async def complete_objective(args: dict) -> str:
    topic = (args.get("topic") or args.get("objective") or "").strip()
    try:
        obj = OBJECTIVES.find(topic)
        if obj is None:
            return f"I couldn't find a single active objective matching '{topic}', sir."
        OBJECTIVES.set_status(obj.id, "done")
    except Exception as e:  # noqa: BLE001
        return tool_error("complete objective", e)
    return f"Marked \"{obj.text}\" done, sir. I'll stop driving it."


async def drop_objective(args: dict) -> str:
    topic = (args.get("topic") or args.get("objective") or "").strip()
    try:
        obj = OBJECTIVES.find(topic)
        if obj is None:
            return f"I couldn't find a single active objective matching '{topic}', sir."
        OBJECTIVES.set_status(obj.id, "dropped")
    except Exception as e:  # noqa: BLE001
        return tool_error("drop objective", e)
    return f"Dropped \"{obj.text}\", sir — I'll no longer work on it."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "assign_objective",
            "description": (
                "Hand Afon a MULTI-DAY objective/goal to OWN and drive across days (not a one-off task). "
                "Use for 'take this on', 'own this', 'drive this to done', 'make X happen over the next "
                "while'. Afon advances the safe parts daily and reports progress unprompted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "The goal, e.g. 'get Party Map beta launch-ready'."},
                    "project": {"type": "string", "description": "Optional project tag."},
                },
                "required": ["objective"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_objectives",
            "description": ("List the multi-day objectives Afon is currently driving, with each one's latest "
                            "progress. Use for 'what are you working on', 'what objectives do you have'."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "objective_status",
            "description": ("Report progress on ONE named multi-day objective, including anything "
                            "waiting on the owner's approval. Use for 'how's the X objective "
                            "going', 'progress on X'. For every objective at once, use "
                            "list_objectives."),
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "A word from the objective to match."}},
                # Required: OBJECTIVES.find("") returns None, so without a topic these can do nothing
                # but ask again. Saying so up front stops the model calling them empty.
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_objective",
            "description": "Mark a multi-day objective DONE so Afon stops driving it. Use for "
                           "'that objective is done', 'I've finished X'. Objectives only — a to-do "
                           "is complete_task, a Notion row is notion_complete_task.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "A word from the objective to match."}},
                # Required: OBJECTIVES.find("") returns None, so without a topic these can do nothing
                # but ask again. Saying so up front stops the model calling them empty.
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drop_objective",
            # 'forget X' deliberately removed: that is memory.forget's trigger, and advertising it
            # here made an objective the model's first guess for "forget what I told you about X".
            "description": "ABANDON a multi-day objective unfinished so Afon stops driving it. "
                           "Use for 'drop that objective', 'stop working on X'. Objectives only — "
                           "to erase a remembered fact use forget.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "A word from the objective to match."}},
                # Required: OBJECTIVES.find("") returns None, so without a topic these can do nothing
                # but ask again. Saying so up front stops the model calling them empty.
                "required": ["topic"],
            },
        },
    },
]

HANDLERS = {
    "assign_objective": assign_objective,
    "list_objectives": list_objectives,
    "objective_status": objective_status,
    "complete_objective": complete_objective,
    "drop_objective": drop_objective,
}
