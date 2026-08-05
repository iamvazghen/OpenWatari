"""Owner-facing tools for relationship memory (Phase 6.2).

Let the owner shape how Watari relates to him: mark a topic as sensitive ("that's a sore subject"),
record a running joke, or ask where they stand. Mood-over-time is logged automatically each turn by the
affect reader — these tools cover the parts only the owner can declare.
"""

from __future__ import annotations

from jarvis.brain.relationship import RELATIONSHIP
from jarvis.brain.tools.base import tool_error


async def note_sensitivity(args: dict) -> str:
    topic = (args.get("topic") or "").strip()
    if not topic:
        return "What topic should I be careful around, sir?"
    # These all touch a JSON store on disk: a truncated file or a locked write raises, and without
    # this the owner hears the raw exception name from the agent's blanket catch. tool_error also
    # produces a string `tool_failed()` recognises, so a proactive caller can tell it from data.
    try:
        added = RELATIONSHIP.add_sensitivity(topic)
    except Exception as e:  # noqa: BLE001
        return tool_error("sensitivity note", e)
    if added:
        return f"Noted, sir — I'll handle anything about {topic} gently."
    return f"I already treat {topic} as a sensitive one, sir."


async def note_running_joke(args: dict) -> str:
    joke = (args.get("joke") or "").strip()
    if not joke:
        return "What's the running joke, sir?"
    try:
        added = RELATIONSHIP.add_running_joke(joke)
    except Exception as e:  # noqa: BLE001
        return tool_error("running-joke note", e)
    if added:
        return "Got it — I'll remember that one, sir."
    return "That one's already in the book, sir."


async def relationship_status(args: dict) -> str:
    try:
        r = RELATIONSHIP.render()
    except Exception as e:  # noqa: BLE001
        return tool_error("relationship check", e)
    return r or "Nothing noted on that front, sir — we're on an even keel."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "note_sensitivity",
            "description": ("Record a topic the owner wants handled gently / carefully from now on "
                            "('that's a sore subject', 'go easy on the money stuff')."),
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "The sensitive topic."}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note_running_joke",
            "description": "Remember a running joke or shared reference so Watari can call back to "
                           "it later. Use for 'that's our running joke', 'remember that bit'. For "
                           "a factual detail about the owner, use remember.",
            "parameters": {
                "type": "object",
                "properties": {"joke": {"type": "string", "description": "The running joke / reference."}},
                "required": ["joke"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "relationship_status",
            "description": ("Report how things stand relationally — the owner's recent mood, topics to "
                            "handle gently, and running jokes ('how am I doing', 'read the room')."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {
    "note_sensitivity": note_sensitivity,
    "note_running_joke": note_running_joke,
    "relationship_status": relationship_status,
}
