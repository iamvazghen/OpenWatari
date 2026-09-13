"""37.F3 — "forget X" removes X from every store, and proves it by asking again.

`forget` deleted the single best-matching learned note. Right for "forget that" about one thing said
a moment ago; wrong for everything else. Told to forget a subject Afon knew four facts about, it
dropped ONE and said "Forgotten, sir". The embedding stayed in the vector store, the entities stayed
in the relationship graph where they still shaped answers, and the journal still described the day in
prose. Four stores, one deletion, and a confident report of success.

The plan states the test: "forget X that leaves X in two stores is the failure". So this asserts the
sweep across all four AND the re-query, because the only evidence a thing is forgotten is asking for
it again and getting nothing back.

Hermetic: temporary memory, vector and graph stores. No network, no embedder.

    uv run python bench/test_right_to_forget.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

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


def main() -> None:
    from afon.brain import forget as F
    from afon.brain.graph import GraphMemory
    from afon.brain.memory import MemoryStore
    from afon.brain.semantic import VectorStore

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        store = MemoryStore(base_dir=d)
        store.remember("The Lpstrak rabbit farm is in Armenia")
        store.remember("The rabbit farm needs a new water system by spring")
        store.remember("The rabbit farm has forty does")
        store.remember("Vazghen trains at the gym on Mondays")
        store.journal_append("Reviewed the rabbit farm numbers today.\nAlso went to the gym.")

        vec = VectorStore(Path(d) / "vectors.sqlite")
        vec.put_many([(str(n.path), 0.0, [0.1, 0.2, 0.3]) for n in store._iter_notes()])
        graph = GraphMemory(Path(d) / "graph.sqlite")
        graph.add("rabbit farm", "located_in", "Armenia")
        graph.add("rabbit farm", "needs", "a water system")
        graph.add("Vazghen", "trains_at", "the gym")

        print("[1] the sweep reaches all four stores, not just the best-matching note")
        before_vec = len(vec.keys())
        out = F.everywhere("rabbit farm", store=store, index=vec, graph=graph)
        check("EVERY matching fact goes, not one", len(out.facts) == 3, out.facts)
        check("the embeddings go with them", out.vectors == 3, out.vectors)
        check("...and really leave the vector store", len(vec.keys()) == before_vec - 3,
              f"{before_vec} -> {len(vec.keys())}")
        check("the graph relations go", out.triples == 2, out.triples)
        check("...and the graph really forgets the entity",
              graph.neighbors("rabbit farm") == [], graph.neighbors("rabbit farm"))
        check("the journal line goes", out.journal_lines == 1, out.journal_lines)

        print("\n[2] the re-query is the proof, and it is actually run")
        check("nothing comes back", out.clean, out.survived)
        check("...and he says he checked", "nothing comes back" in out.spoken(), out.spoken())
        check("recall really finds nothing", not store.recall("rabbit farm", semantic=False))

        print("\n[3] the neighbours survive — a sweep that over-deletes is worse than none")
        check("an unrelated fact is untouched", bool(store.recall("gym", semantic=False)))
        check("an unrelated relation is untouched", bool(graph.neighbors("Vazghen")))
        check("an unrelated journal line is untouched", "gym" in store.read_journal())
        check("its embedding is untouched", len(vec.keys()) == 1, vec.keys())

        print("\n[4] what it refuses to do")
        said = out.spoken()
        check("it says the owner's own vault notes were left alone",
              "vault notes I've left alone" in said, said)
        check("...and points at the tool that can check them", "search me" in said or
              "search them" in said, said)
        src = (Path(__file__).resolve().parents[1]
               / "src/afon/brain/forget.py").read_text(encoding="utf-8")
        check("the audit log is deliberately not rewritten",
              "audit log" in src and "much worse thing" in src,
              "a forget that edits the record of what Afon DID is a different thing entirely")

        print("\n[5] a store that will not give it up is REPORTED, never papered over")
        class _Stubborn:
            """Deletes nothing and keeps answering — the exact failure the floor names."""

            def forget_all(self, q):
                return []

            def redact_journal(self, q):
                return 0

            def recall(self, q, semantic=None):
                return [{"text": "The rabbit farm is still here"}]

            def _iter_notes(self):
                return iter(())

        stuck = F.everywhere("rabbit farm", store=_Stubborn(), index=vec, graph=graph)
        check("it is not called clean", not stuck.clean, stuck.survived)
        check("the survivor is named", "still came back" in stuck.spoken(), stuck.spoken())
        check("...with what it was", "still here" in stuck.spoken(), stuck.spoken())
        check("it does NOT claim nothing was stored",
              "had nothing stored" not in stuck.spoken(), stuck.spoken())

        print("\n[6] a re-check that cannot run is not silence")
        class _Mute(_Stubborn):
            def recall(self, q, semantic=None):
                raise RuntimeError("index gone")

        broken = F.everywhere("rabbit farm", store=_Mute(), index=vec, graph=graph)
        check("an unusable memory is reported as unverified, not as clean",
              not broken.clean, broken.survived)
        check("...naming why", "couldn't re-check" in " ".join(broken.survived), broken.survived)

        print("\n[7] nothing to forget says so, and an empty query does nothing")
        nada = F.everywhere("something he never mentioned", store=store, index=vec, graph=graph)
        check("an unknown subject is reported honestly", nada.removed == 0, nada)
        check("...in plain words", "had nothing stored" in nada.spoken(), nada.spoken())
        empty = F.everywhere("   ", store=store, index=vec, graph=graph)
        check("an empty query removes nothing", empty.removed == 0 and empty.clean)

        print("\n[8] a broken store never takes the turn down with it")
        class _Explodes:
            def forget_all(self, q):
                raise OSError("disk gone")

            def redact_journal(self, q):
                raise OSError("disk gone")

            def recall(self, q, semantic=None):
                return []

            def _iter_notes(self):
                raise OSError("disk gone")

        survived = F.everywhere("anything", store=_Explodes(), index=vec, graph=graph)
        check("a failing store degrades instead of raising", isinstance(survived, F.Erasure))
        check("...and claims nothing it did not do", survived.facts == [] and survived.vectors == 0)

        print("\n[9] the tool uses the sweep, not the single-note delete")
        tool_src = (Path(__file__).resolve().parents[1]
                    / "src/afon/brain/tools/memory.py").read_text(encoding="utf-8")
        check("the forget tool calls the everywhere sweep",
              "from afon.brain.forget import everywhere" in tool_src)
        check("...and no longer the one-note delete", "STORE.forget(query)" not in tool_src)
        check("the schema tells the model what it really does",
              "every matching" in tool_src and "verified by asking again" in tool_src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
