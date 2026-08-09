"""Self-improvement loop — adapted from NousResearch/hermes-agent's ``background_review``.

Hermes, after a turn, forks the agent to replay the conversation and ask itself "should anything be
saved to memory?", writing straight to its memory store while the main conversation + prompt cache
stay untouched. We apply the same idea to Afon's brain: a background pass replays the recent
conversation and asks the model what DURABLE facts about the owner are worth remembering long-term,
then writes them to **L1 learned memory** (``STORE.remember``, which dedups).

Why it matters here: Afon's 5-layer memory was elaborate but EMPTY (L1 = 0 facts) because nothing
ever wrote to it. This loop is what makes the memory actually learn as you talk — and it runs OFF
the hot path (a separate task) so it never slows a reply.

Invariants (mirroring Hermes): only ADD/dedup, never delete here; never touch the live conversation
or the main model's prompt; degrade silently on any error.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from afon.brain.graph import GRAPH
from afon.brain.memory import STORE

# Adapted from Hermes' _MEMORY_REVIEW_PROMPT, tightened to return machine-parseable output. Now asks
# for BOTH flat facts (L1) and explicit entity relations (L5b graph) in ONE call, so the graph layer
# populates itself as the owner talks instead of sitting empty.
_EXTRACT_PROMPT = (
    "You are Afon's private memory reviewer. Read the conversation and extract what's worth "
    "remembering about the owner for months. Return ONLY a compact JSON object with two keys:\n"
    '  "facts": array (max 8) of short, self-contained third-person sentences — stable preferences '
    "and persona, decisions, people/projects/places, and expectations about how Afon should behave. "
    'e.g. ["the owner prefers replies under two sentences.", "the owner renamed his assistant to Afon."]\n'
    '  "relations": array (max 8) of [subject, predicate, object] triples for EXPLICIT structured '
    'links the owner stated, e.g. [["rabbit farm","located in","Armavir"],["owner","trains at","6am"]]. '
    "Use short lowercase entity/predicate phrases. Only include a relation actually stated.\n"
    "IGNORE transient chit-chat, one-off task mechanics, the current time/date, and the obvious. "
    'If nothing is worth saving, return {"facts": [], "relations": []}.'
)


def _parse_facts(raw: str) -> list[str]:
    """Pull fact strings out of the model's reply, defensively.

    Accepts BOTH the current object form ({"facts":[...]}) and the legacy bare-array form ([...]),
    so older prompts/models and existing tests keep working.
    """
    obj = _parse_object(raw)
    if obj is not None:
        arr = obj.get("facts")
        return _clean_str_list(arr if isinstance(arr, list) else [])
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return _clean_str_list(arr)


def _parse_object(raw: str) -> dict | None:
    """Extract the first JSON *object* from the reply, or None (it's a bare array / not JSON)."""
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _clean_str_list(arr: Any) -> list[str]:
    out: list[str] = []
    for x in arr or []:
        s = str(x).strip()
        if 3 < len(s) <= 240:
            out.append(s)
    return out[:8]


def _parse_relations(raw: str) -> list[tuple[str, str, str]]:
    """Pull [subject, predicate, object] triples out of the object reply (empty for the legacy form)."""
    obj = _parse_object(raw)
    if obj is None:
        return []
    out: list[tuple[str, str, str]] = []
    for r in obj.get("relations") or []:
        if isinstance(r, (list, tuple)) and len(r) == 3:
            s, p, o = (str(x).strip() for x in r)
            if s and p and o and all(len(x) <= 80 for x in (s, p, o)):
                out.append((s, p, o))
    return out[:8]


async def review_and_learn(history: list[dict[str, Any]], llm, store: Any = STORE,
                           graph: Any = GRAPH) -> list[str]:
    """Replay ``history``, extract durable facts (L1) + explicit relations (L5b graph), and write the
    new ones. Returns the facts learned. ONE model call; graph writes are best-effort + deduped."""
    convo = "\n".join(
        f"{m['role']}: {m['content']}"
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    )
    if len(convo) < 40:
        return []  # not enough said to learn anything
    try:
        msg = await llm.complete(
            [{"role": "system", "content": _EXTRACT_PROMPT},
             {"role": "user", "content": convo[:6000]}],
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001 — self-improvement is best-effort, never breaks a turn
        logger.warning(f"self-improve review skipped ({type(e).__name__}: {e})")
        return []
    raw = getattr(msg, "content", "") or ""
    facts = _parse_facts(raw)
    before = store.count()
    learned: list[str] = []
    for f in facts:
        if store.remember(f, tags=["learned", "auto"]):
            learned.append(f)
    # L5b: write any explicit relations into the graph (dedup via INSERT OR IGNORE). Never fatal.
    triples = 0
    for s, p, o in _parse_relations(raw):
        try:
            if graph.add(s, p, o):
                triples += 1
        except Exception as e:  # noqa: BLE001
            logger.debug(f"graph relation write skipped ({type(e).__name__})")
    new = store.count() - before
    if learned or triples:
        logger.info(f"self-improve: reviewed {len(history)} msgs -> {new} new fact(s) into L1, "
                    f"{triples} relation(s) into L5b graph")
    return learned
