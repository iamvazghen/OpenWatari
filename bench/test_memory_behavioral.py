"""Behavioral verification — which memory layer engages, and WHEN, as the agent runs.

Unit tests prove each layer works in isolation; this proves the AGENT actually reaches for the right
layer at the right moment during real turns, and that every fix from this session is wired into the
live turn path:

  L0 working memory  -> carried into every turn's messages (rolling history).
  L1 learned facts   -> auto-injected (Fix #1) when the utterance is about a known topic; in the
                        startup digest; refreshed mid-session (Fix #2). Read on demand via `recall`.
  L2 journal         -> auto-injected when the utterance matches a recent journal entry.
  L3 vault + L5 sem  -> reached through the `recall` tool (fused across layers; semantic on, Fix #3).
  L5b graph          -> WRITTEN by consolidation (Fix #4); READ when the utterance is relational
                        (the 'graph' lazy tool group activates).
  Consolidation      -> background review fires on the cadence and writes facts + relations.
  Discipline         -> a trivial or off-topic turn injects NOTHING (stopword filter + semantic floor).

Hermetic: temp stores swapped for the singletons, a capturing/scripted fake LLM. No network.

    uv run python bench/test_memory_behavioral.py
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


class CapturingLLM:
    """Records every completion's messages; ends the turn with a plain reply (no tool calls)."""

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.seen.append([dict(m) for m in messages])
        return SimpleNamespace(content="Noted, sir.", tool_calls=None)

    async def stream_with_tools(self, messages, tools=None, tool_choice="auto", skip_primary=False):
        self.seen.append([dict(m) for m in messages])
        yield "text", "Noted, sir."

    async def warmup(self):
        pass

    def _sys_notes(self, turn: int = -1) -> str:
        return " ".join(m.get("content", "") for m in self.seen[turn] if m.get("role") == "system")


async def main() -> None:
    from afon.config import settings
    settings.memory_enabled = True
    settings.memory_autorecall_enabled = True
    settings.memory_semantic_enabled = True
    settings.memory_digest_refresh_every_turns = 3

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    # Isolate the rolling-thread snapshot + disable idle-reset so the agent starts clean (not from a
    # real leftover afon_session.json) and doesn't reset history mid-test.
    settings.session_persist_path = str(Path(tmp.name) / "session.json")
    settings.session_idle_reset_minutes = 0
    import afon.brain.memory as mem
    import afon.brain.graph as graphmod
    import afon.brain.tools.graphmem as graphtools

    store = mem.MemoryStore(base_dir=Path(tmp.name))
    graph = graphmod.GraphMemory(path=Path(tmp.name) / "g.sqlite")
    mem.STORE = store
    graphtools.GRAPH = graph   # the read tool resolves this module global
    store.remember("the owner runs a rabbit farm called Lpstrak in Armenia", tags=["project"])
    store.remember("the owner prefers replies under two sentences", tags=["preference"])
    store.journal_append("Reviewed the rabbit farm irrigation schedule for next week.")

    from afon.brain.agent import AfonAgent
    from afon.brain.tools import groups_for_text
    agent = AfonAgent()
    agent._llm = CapturingLLM()

    print("[L0] working memory — prior turns are carried forward")
    await agent.respond("let's talk about the farm")
    n_after_1 = len(agent._history)
    await agent.respond("what did I just say?")
    carried = any("let's talk about the farm" in (m.get("content") or "") for m in agent._llm.seen[-1])
    check("turn 2's context still contains turn 1 (rolling history)", carried)
    check("history grew across turns (L0 persists the thread)", len(agent._history) > n_after_1)

    print("\n[L1] auto-recall injects a KNOWN topic at the right moment (Fix #1)")
    await agent.respond("how's the rabbit farm looking these days?")
    note = agent._llm._sys_notes()
    check("the rabbit-farm fact (L1) is injected into this turn", "Lpstrak" in note or "rabbit farm" in note, note[:160])
    check("it's presented as background context", "don't recite" in note.lower())

    print("\n[L2] a journal-matching turn injects the journal line")
    await agent.respond("remind me about the irrigation schedule")
    note = agent._llm._sys_notes()
    check("the journal entry (L2) surfaces for a matching turn", "irrigation" in note.lower(), note[:160])

    print("\n[discipline] trivial + off-topic turns inject NOTHING (stopword + floor)")
    await agent.respond("what is the capital of Japan")
    off = agent._llm._sys_notes()
    check("an unrelated turn injects no memory note", "Relevant things you already know" not in off, off[:120])
    await agent.respond("thanks")
    triv = agent._llm._sys_notes()
    check("a bare acknowledgement injects nothing", "Relevant things you already know" not in triv)

    print("\n[L1 digest] Fix #2 — a mid-session fact enters the prompt without a restart")
    check("startup prompt already carries an old fact", "rabbit farm" in agent._system["content"])
    store.remember("the owner adopted a cat named Milo", tags=["personal"])
    check("brand-new fact not yet in the frozen prompt", "Milo" not in agent._system["content"])
    for _ in range(3):
        agent._maybe_refresh_digest()
    check("after the refresh cadence, the new fact is in the prompt", "Milo" in agent._system["content"])

    print("\n[L3+L5] the recall TOOL fuses layers (semantic on, Fix #3)")
    # Inject a deterministic stub embedder so this stays hermetic (the REAL embedder — local model or
    # the Jina API — is verified separately; here we assert the wiring + the semantic floor). Concept
    # dims: [rabbit, submarine, cat]. A query and a fact score high only when they share a concept.
    import afon.brain.semantic as semmod

    def _stub_embed(texts):
        def vec(t):
            t = t.lower()
            return [
                1.0 if any(w in t for w in ("rabbit", "bunn", "farm", "lpstrak", "armenia")) else 0.0,
                1.0 if any(w in t for w in ("submarine", "warfare", "naval", "torpedo")) else 0.0,
                1.0 if any(w in t for w in ("cat", "milo", "kitten")) else 0.0,
            ]
        return [vec(t) for t in texts]

    semmod.INDEX = semmod.SemanticIndex(embed_fn=_stub_embed, persist=False)
    import afon.brain.tools.memory as memtool
    res = await memtool.recall({"query": "what do you know about my bunnies", "layers": ["L1", "L5"]})
    check("recall surfaces the rabbit farm by MEANING (L5 semantic)", "rabbit farm" in res.lower(), res[:160])
    empty = await memtool.recall({"query": "submarine warfare tactics", "layers": ["L1", "L5"]})
    check("an unrelated recall stays empty (semantic floor holds the line)",
          "don't have" in empty.lower() or "nothing" in empty.lower(), empty[:120])

    print("\n[L5b] graph is READ on relational turns, WRITTEN by consolidation (Fix #4)")
    check("'what's connected to X' activates the graph tool group", "graph" in groups_for_text("what's connected to the rabbit farm"))
    check("a plain factual turn does NOT activate the graph group", "graph" not in groups_for_text("how's the rabbit farm"))
    from afon.brain.background_review import review_and_learn
    convo = [{"role": "user", "content": "My rabbit farm is in Armavir, which is in Armenia."},
             {"role": "assistant", "content": "Noted, sir."}]
    obj = ('{"facts": ["the owner has a rabbit farm in Armavir."], '
           '"relations": [["rabbit farm","located in","armavir"],["armavir","located in","armenia"]]}')

    class OneShot:
        async def complete(self, *a, **k):
            return SimpleNamespace(content=obj, tool_calls=None)
    await review_and_learn(convo, OneShot(), store=store, graph=graph)
    check("consolidation WROTE relations into the graph", len(graph.all_triples()) >= 2, str(graph.all_triples()))
    related = await graphtools.recall_related({"entity": "rabbit farm"})
    check("the graph tool answers a MULTI-HOP query (farm->Armavir->Armenia)", "armenia" in related.lower(), related[:160])

    print("\n[consolidation] the reviewer fires on cadence")
    check("agent schedules a background review every N turns", agent._review_every >= 2)
    check("both stores are populated after a session (L1 facts + L5b triples)",
          store.count() >= 3 and len(graph.all_triples()) >= 2, f"L1={store.count()} triples={len(graph.all_triples())}")

    print("\n[30.F2] one slow store cannot own the answer")
    # test_layering.py checks that the facade EXISTS and that nobody bypasses it. This checks the
    # part a structural scan cannot: that the per-layer budget is actually applied. Written after
    # planting a rename of `LAYER_TIMEOUT_S`, which the grep-for-the-name check waved through
    # because the constant is also mentioned where it is used.
    import time as _time

    from afon.brain.recall import recall as _recall_all

    class _Stalled:
        async def fused_recall(self, query, limit=8, layers=None):
            await asyncio.sleep(30)
            return [{"layer": "L1", "text": "far too late", "score": 9.0, "source": "learned"}]

    t0 = _time.perf_counter()
    hits = await _recall_all("rabbit farm", store=_Stalled(), layers=("L1", "L2"))
    local = _time.perf_counter() - t0
    check(f"a stalled LOCAL layer is abandoned in about a second ({local:.1f}s)", local < 2.0,
          "auto-recall runs these on every turn; S30 budgets recall at p95 <= 300ms")
    check("...and recall answers without it rather than raising", hits == [], str(hits))
    t0 = _time.perf_counter()
    await _recall_all("rabbit farm", store=_Stalled(), layers=("L1", "L3"))
    network = _time.perf_counter() - t0
    check(f"a NETWORK layer gets a real allowance instead ({network:.1f}s)",
          network > local + 1.0,
          "a single flat budget either strangles the vault search or puts its latency on a greeting")

    class _Twice:
        async def fused_recall(self, query, limit=8, layers=None):
            return [{"layer": "L1", "text": "He has a rabbit farm.", "score": 3.0, "source": "learned"},
                    {"layer": "L5b", "text": "he has a rabbit farm", "score": 1.0, "source": "graph"}]

    merged = await _recall_all("rabbit farm", store=_Twice(), layers=("L1", "L5b"))
    check("the same fact held in two stores is reported once", len(merged) == 1, str(merged))
    check("...keeping the better-scoring copy", merged and merged[0]["score"] == 3.0, str(merged))

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
