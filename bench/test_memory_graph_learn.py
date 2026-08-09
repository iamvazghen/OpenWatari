"""Fix #4 — the self-improvement reviewer now feeds the L5b graph, and Fix #3 — L5 semantic is on.

Previously the entity-relation graph sat empty (nothing wrote to it) and semantic recall was off by
default. This verifies:
  * review_and_learn parses the new {"facts":[...], "relations":[...]} object, writes facts to L1 AND
    triples to the graph — in ONE model call — and still accepts the legacy bare-array form;
  * the populated graph answers multi-hop queries;
  * memory_semantic_enabled now defaults True (graceful — no embedder still degrades to keyword).

Hermetic: temp stores, a fake LLM. No network.

    uv run python bench/test_memory_graph_learn.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.calls += 1
        return SimpleNamespace(content=self.content, tool_calls=None)


CONVO = [
    {"role": "user", "content": "My rabbit farm is in Armavir, which is in Armenia, and I train at 6am."},
    {"role": "assistant", "content": "Noted, sir."},
    {"role": "user", "content": "Keep replies under two sentences."},
    {"role": "assistant", "content": "Understood, sir."},
]


async def main() -> None:
    from afon.brain.background_review import _parse_facts, _parse_relations, review_and_learn
    from afon.brain.graph import GraphMemory
    from afon.brain.memory import MemoryStore

    print("[1] the reviewer extracts facts -> L1 AND relations -> L5b graph, in one call")
    obj = ('{"facts": ["the owner has a rabbit farm in Armavir.", '
           '"the owner trains at 6am.", "the owner prefers replies under two sentences."], '
           '"relations": [["rabbit farm","located in","armavir"], '
           '["armavir","located in","armenia"], ["owner","trains at","6am"]]}')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        store = MemoryStore(base_dir=Path(d))
        graph = GraphMemory(path=Path(d) / "g.sqlite")
        llm = FakeLLM(obj)
        learned = await review_and_learn(CONVO, llm, store=store, graph=graph)
        check("exactly one model call (facts+relations together)", llm.calls == 1, str(llm.calls))
        check("three facts written to L1", store.count() == 3, str(store.count()))
        check("three relations written to the graph", len(graph.all_triples()) == 3, str(graph.all_triples()))
        check("returned the learned facts", len(learned) == 3, str(learned))

        print("\n[2] the populated graph answers a MULTI-HOP query")
        related = graph.related("rabbit farm", hops=2)
        check("farm -> Armavir -> Armenia resolves (2-hop)", "armenia" in related, str(related))
        check("direct describe lists the farm's links", any("armavir" in x for x in graph.describe("rabbit farm")))

        print("\n[3] dedup — re-running writes no new triples")
        await review_and_learn(CONVO, llm, store=store, graph=graph)
        check("still three triples (INSERT OR IGNORE dedup)", len(graph.all_triples()) == 3, str(len(graph.all_triples())))

    print("\n[4] backward compat — a legacy bare ARRAY still yields facts, no relations, no crash")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        store = MemoryStore(base_dir=Path(d))
        graph = GraphMemory(path=Path(d) / "g.sqlite")
        legacy = FakeLLM('["the owner likes lofi.", "the owner uses Windows."]')
        learned = await review_and_learn(CONVO, legacy, store=store, graph=graph)
        check("legacy array still learns facts", store.count() == 2, str(store.count()))
        check("legacy array yields no triples", graph.all_triples() == [], str(graph.all_triples()))
        check("_parse_relations on a bare array -> []", _parse_relations('["a","b"]') == [])
        check("_parse_facts still parses the object form",
              set(_parse_facts(('{"facts":["x is y."],"relations":[]}'))) == {"x is y."})

    print("\n[5] Fix #3 — L5 semantic defaults ON (graceful)")
    from afon.config import settings
    check("memory_semantic_enabled defaults True", settings.memory_semantic_enabled is True)
    # Graceful: recall must not crash even if no embedder resolves (it degrades to keyword).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        store = MemoryStore(base_dir=Path(d))
        store.remember("the owner runs a rabbit farm", tags=["project"])
        hits = store.recall("rabbit farm")   # semantic path exercised; must return the fact
        check("recall works with semantic enabled (no crash)", any("rabbit" in h for h in hits), str(hits))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
