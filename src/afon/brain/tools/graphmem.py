"""Graph-memory tools (L5b) — structured, multi-hop associative recall over the entity graph.

These sit in a LAZY tool group ("graph"): they only join the per-turn surface when the owner talks
about connections between things ("what's related to…", "link X to Y"). Free-text remember/recall
stays core; this is the deliberate, structured layer on top. Backed by ``brain/graph.py`` (no-server
sqlite triple store) — see that module's header for why this is lean rather than Neo4j.
"""

from __future__ import annotations

from afon.brain.graph import GRAPH
from afon.brain.tools.base import missing_arg, tool_error
from afon.config import settings


async def link_memory(args: dict) -> str:
    """Record a structured relation (subject predicate object) into the L5b graph."""
    if not settings.memory_enabled:
        return "My long-term memory is switched off right now, sir."
    subject = (args.get("subject") or "").strip()
    predicate = (args.get("predicate") or "").strip()
    obj = (args.get("object") or "").strip()
    if not (subject and predicate and obj):
        return "To link a relation I need a subject, a relation, and an object, sir."
    try:
        ok = GRAPH.add(subject, predicate, obj)
        return (f"Linked, sir — {subject} {predicate} {obj}." if ok
                else "I couldn't store that relation, sir.")
    except Exception as e:  # noqa: BLE001
        return tool_error("link_memory", e)


async def recall_related(args: dict) -> str:
    """Associative recall — surface what's connected to an entity via the L5b graph (multi-hop)."""
    if not settings.memory_enabled:
        return "My long-term memory is switched off right now, sir."
    entity = (args.get("entity") or "").strip()
    if not entity:
        return missing_arg("recall_related", args, "entity", "name", "person", "thing", "subject",
                           ask="Which person or thing should I explore, sir?")
    try:
        direct = GRAPH.describe(entity)
        if not direct:
            return f"I don't have anything linked to '{entity}', sir."
        related = GRAPH.related(entity, hops=2)
        lines = "; ".join(direct)
        extra = f" Related out to two hops: {', '.join(related[:8])}." if related else ""
        return f"Here's what's connected to {entity}, sir: {lines}.{extra}"
    except Exception as e:  # noqa: BLE001
        return tool_error("recall_related", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "link_memory",
            "description": (
                "Record a structured relationship between two things in the knowledge graph "
                "(subject -> relation -> object), e.g. ('Acme', 'located in', 'Berlin') or "
                "('the owner', 'owns', 'Acme'). Use when the owner states a durable connection "
                "between entities that later associative recall should traverse. Complements "
                "'remember' (which stores free-text facts)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "The entity the relation starts from."},
                    "predicate": {"type": "string", "description": "The relation, e.g. 'owns', 'located in'."},
                    "object": {"type": "string", "description": "The entity the relation points to."},
                },
                "required": ["subject", "predicate", "object"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_related",
            "description": (
                "Associative recall — given a person or thing, surface everything connected to it in "
                "the knowledge graph, following links up to two hops out. Use for 'what's connected "
                "to X / what's related to Y / how is X linked to my stuff'. Returns direct relations "
                "plus related entities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "The person or thing to explore."},
                },
                "required": ["entity"],
            },
        },
    },
]

HANDLERS = {"link_memory": link_memory, "recall_related": recall_related}
