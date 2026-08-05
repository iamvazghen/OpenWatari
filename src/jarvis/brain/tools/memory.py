"""Memory tools — Jarvis remembers and recalls across sessions (Phase 9, L1/L2).

`remember` writes a durable fact (he calls it when the owner says "remember that…", or on his own
when something is clearly worth keeping). `recall` searches what he's learned. `forget` removes a
fact. `read_journal` reads a day's continuity log. All back onto `brain/memory.py` (plain Markdown),
so they work offline and never crash the brain.
"""

from __future__ import annotations

from jarvis.brain.memory import STORE
from jarvis.brain.tools.base import tool_error
from jarvis.config import settings


async def remember(args: dict) -> str:
    if not settings.memory_enabled:
        return "My long-term memory is switched off right now, sir."
    text = (args.get("text") or "").strip()
    if not text:
        return "What would you like me to remember, sir?"
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.replace(";", ",").split(",") if t.strip()]
    try:
        STORE.remember(text, tags=tags)
        return "Noted, sir — I'll remember that."
    except Exception as e:  # noqa: BLE001
        return tool_error("remember", e)


async def recall(args: dict) -> str:
    """Cross-layer recall — searches L1 (learned) + L2 (journal) + L3 (vault) + L5 (semantic)."""
    if not settings.memory_enabled:
        return "My long-term memory is switched off right now, sir."
    query = (args.get("query") or "").strip()
    if not query:
        return "What should I recall, sir?"
    layers = args.get("layers")  # optional: ['L1','L2','L3','L5'] to narrow
    try:
        # fused_recall returns tagged dicts; tag each hit with its layer so the LLM can cite.
        hits = await STORE.fused_recall(query, limit=settings.memory_recall_limit, layers=tuple(layers) if layers else None)
        if not hits:
            return f"I don't have anything stored about '{query}', sir."
        tag = {"L1": "learned", "L2": "journal", "L3": "vault", "L5": "semantic"}
        lines = []
        for h in hits:
            layer = tag.get(h["layer"], h["layer"])
            text = h["text"].rstrip(".")
            lines.append(f"[{layer}] {text}.")
        return ("Here's what I remember across all layers: " + " ".join(lines))
    except Exception as e:  # noqa: BLE001
        return tool_error("recall", e)


async def forget(args: dict) -> str:
    if not settings.memory_enabled:
        return "My long-term memory is switched off right now, sir."
    query = (args.get("query") or "").strip()
    if not query:
        return "What should I forget, sir?"
    try:
        gone = STORE.forget(query)
        return f"Forgotten, sir — I've dropped the note about '{query}'." if gone else (
            f"I had nothing stored about '{query}', sir."
        )
    except Exception as e:  # noqa: BLE001
        return tool_error("forget", e)


async def read_journal(args: dict) -> str:
    if not settings.memory_enabled:
        return "My journal is switched off right now, sir."
    try:
        text = STORE.read_journal()
        return text or "My journal is empty so far, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("read journal", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a durable fact to long-term memory so you recall it in future sessions. "
                "Use when the owner says 'remember that…/note that…/for future', or proactively when "
                "you learn something clearly worth keeping (a preference, a person, a decision, an "
                "ongoing thread). Keep each fact short and self-contained."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The fact to remember, one sentence."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional topic tags (e.g. 'preference', 'rabbit-farm').",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search across ALL memory layers — L1 learned facts, L2 journal entries, L3 vault "
                "notes, L5 semantic — for what you know about a topic or person. Use for "
                "'what do you know about X / did I tell you about Y / what did we do yesterday'. "
                "Each hit is tagged with its layer ([learned], [journal], [vault]) so the owner "
                "knows where it came from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic or person to recall."},
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["L1", "L2", "L3", "L5"]},
                        "description": "Optional: limit to specific layers (default = all).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            # Names the three tools that used to steal this call. "forget" is the owner's word for
            # four different erasures, and drop_objective even advertised 'forget X' as its trigger.
            "description": "Delete ONE remembered fact from long-term memory. Use for 'forget that "
                           "I…', 'delete what you know about X'. Personal facts only — not macros "
                           "(delete_macro), objectives (drop_objective) or to-dos (delete_task).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Which fact to forget."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_journal",
            "description": (
                "Read your most recent daily journal — a summary of recent sessions — for continuity "
                "('what did we do yesterday / recently')."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {"remember": remember, "recall": recall, "forget": forget, "read_journal": read_journal}
