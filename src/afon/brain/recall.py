"""One question, every store, once — the recall facade (SYSTEMS.md 30.F2).

"What do we know about X?" had no single answer. `MemoryStore.fused_recall` reached five layers
(L1 learned, L2 journal, L3 vault, L5 semantic, L5b graph) and was the closest thing to a facade,
but it lived *on* the L1/L2 store, so a caller had to know which store to ask in order to ask
everything — and the task store, which holds the open commitments a question about a project most
often means, was not in it at all.

What this adds over calling `fused_recall` directly:

  * **the tasks layer (L4)**, so "what's going on with the rabbit farm" reaches the open work;
  * **a per-store timeout**, so one slow layer cannot own the answer. L5's embedder is a network
    call to api.jina.ai; before this, a Jina stall meant recall stalled with it, and the budget
    (p95 ≤300ms) was a hope rather than a bound;
  * **dedup across stores**, because the same fact genuinely lives in two of them — the pattern
    detector writes into L1 and the graph, and the owner reading the same sentence twice with
    different layer tags reads as confusion rather than corroboration;
  * **one import for callers**, so adding a store later is one edit here instead of an audit of
    every caller.

It is deliberately thin. `fused_recall` is a working, tuned fan-out and rewriting it here would
have been a second implementation of the thing this exists to stop there being two of.

    from afon.brain.recall import recall
    hits = await recall("rabbit farm")        # [{"layer": "L1", "text": ..., "score": ...}, ...]

ponytail: the merge is a score sort with a normalised-text dedup, not a learned ranker. 30.R1
asks for precision@3 over a query corpus, and that is the point at which a real ranker earns its
place — measuring first is the whole shape of this plan.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

#: Every store this fans out to. Named here so a store that is added later is either in this
#: table or is deliberately not memory — the same reason 31.F4's loop table is declared.
LAYERS: dict[str, str] = {
    "L1": "learned facts",
    "L2": "the daily journal",
    "L3": "the Obsidian vault",
    "L5": "semantic similarity over L1",   # folds into L1 rather than returning separately
    "L5b": "the entity graph",
    "L4": "open tasks and to-dos",
}

#: Per-store wall-clock budget. The whole point: one slow layer must not own the answer.
#:
#: Split local from network deliberately. L1, L2, L4 and L5b are sqlite and files on this host and
#: have no business taking a second; L3 searches the vault and L5 embeds through api.jina.ai, so
#: they get real network budgets. That distinction is what makes the number matter: auto-recall
#: runs on EVERY turn over the local layers only, and S30 budgets recall at p95 <= 300ms — a
#: single flat backstop generous enough for Jina would have put five seconds on the turn path.
LAYER_TIMEOUT_S: dict[str, float] = {
    "L1": 1.0, "L2": 1.0, "L4": 0.5, "L5b": 1.0,   # local
    "L3": 4.0, "L5": 4.0,                           # network
}
DEFAULT_TIMEOUT_S = 2.5


def budget_for(layers: tuple[str, ...]) -> float:
    """The budget for a group of layers run together: the slowest one they contain.

    `fused_recall` runs L1/L2/L3/L5/L5b as one unit, so they can only be bounded as a group — but
    a group of purely local layers must not inherit the network allowance.
    """
    return max((LAYER_TIMEOUT_S.get(lay, DEFAULT_TIMEOUT_S) for lay in layers),
               default=DEFAULT_TIMEOUT_S)

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


async def _with_budget(coro, layer: str, timeout: float | None = None) -> list[dict]:
    """Run one layer under its budget. A layer that times out or fails contributes nothing and
    says so in the log — it must never take the answer down with it."""
    budget = timeout if timeout is not None else LAYER_TIMEOUT_S.get(layer, DEFAULT_TIMEOUT_S)
    try:
        return await asyncio.wait_for(coro, timeout=budget)
    except asyncio.TimeoutError:
        logger.debug(f"recall: layer {layer} exceeded its {budget:.1f}s budget — answering without it")
    except Exception as e:  # noqa: BLE001 — a broken store must not break recall
        logger.debug(f"recall: layer {layer} failed ({type(e).__name__}: {e})")
    return []


async def _tasks_layer(query: str, limit: int) -> list[dict]:
    """L4 — open work matching the query. Synchronous and in-process, so threaded rather than
    awaited: `find` walks the task list under a lock."""
    from afon.brain.tasks import TASKS

    def _find() -> list[dict]:
        out = []
        for t in TASKS.find(query)[:limit]:
            title = getattr(t, "title", "") or ""
            status = getattr(t, "status", "") or ""
            out.append({"layer": "L4", "text": f"{title} ({status})".strip(),
                        "score": 2.0, "source": "tasks"})
        return out

    return await asyncio.to_thread(_find)


async def recall(query: str, limit: int = 8, layers: tuple[str, ...] | None = None,
                 store: Any | None = None) -> list[dict]:
    """Ask every memory store one question and return ranked, deduplicated hits.

    Each hit is ``{"layer", "text", "score", "source"}``. Unavailable layers are skipped, not
    raised — an answer from four stores beats an exception from five.
    """
    query = (query or "").strip()
    if not query:
        return []
    want = tuple(layers) if layers else tuple(LAYERS)
    unknown = [lay for lay in want if lay not in LAYERS]
    if unknown:
        logger.debug(f"recall: ignoring unknown layer(s) {unknown}")
        want = tuple(lay for lay in want if lay in LAYERS)

    if store is None:
        from afon.brain.memory import STORE as store

    jobs: list[tuple[str, Any]] = []
    fused = tuple(lay for lay in want if lay in ("L1", "L2", "L3", "L5", "L5b"))
    if fused:
        # fused_recall applies its own internal per-layer guards; the budget here is the backstop
        # for the whole group, so a hung embedder cannot hold the turn.
        jobs.append(("fused", _with_budget(store.fused_recall(query, limit=limit, layers=fused),
                                           "fused", budget_for(fused))))
    if "L4" in want:
        jobs.append(("L4", _with_budget(_tasks_layer(query, limit), "L4")))

    results = await asyncio.gather(*(j for _, j in jobs))
    hits: list[dict] = [h for group in results for h in group]

    # Dedup across stores. The pattern detector writes the same sentence into L1 and the graph,
    # and hearing it twice with different tags reads as confusion, not corroboration. The
    # best-scoring copy wins and keeps its layer.
    best: dict[str, dict] = {}
    for h in hits:
        key = _norm(h.get("text", ""))
        if not key:
            continue
        prev = best.get(key)
        if prev is None or h.get("score", 0) > prev.get("score", 0):
            best[key] = h
    merged = sorted(best.values(), key=lambda h: -float(h.get("score", 0)))
    return merged[:limit]


async def _selfcheck() -> None:
    """python -m afon.brain.recall — the smallest thing that fails if the fan-out breaks."""

    class _Store:
        async def fused_recall(self, query, limit=8, layers=None):
            return [{"layer": "L1", "text": "He has a rabbit farm.", "score": 3.0, "source": "learned"},
                    {"layer": "L5b", "text": "he has a rabbit farm", "score": 1.0, "source": "graph"}]

    class _Slow(_Store):
        async def fused_recall(self, query, limit=8, layers=None):
            await asyncio.sleep(10)
            return [{"layer": "L1", "text": "too late", "score": 9.0, "source": "learned"}]

    hits = await recall("rabbit", store=_Store(), layers=("L1", "L5b"))
    assert len(hits) == 1, hits                     # the graph copy is deduped away
    assert hits[0]["score"] == 3.0, hits            # ...and the stronger copy survived
    assert await recall("", store=_Store()) == []
    slow = await recall("rabbit", store=_Slow(), layers=("L1",))
    assert slow == [], slow                          # a stalled layer contributes nothing
    print("recall selfcheck ok")


if __name__ == "__main__":
    asyncio.run(_selfcheck())
