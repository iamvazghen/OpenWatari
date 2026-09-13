"""Forget something everywhere, then go back and check it is gone (37.F3).

`forget` deleted the single best-matching learned note. That is right for "forget that" about one
thing said a moment ago, and wrong for everything else:

  * told to forget a subject Afon knew four facts about, it dropped ONE and said "forgotten, sir";
  * the fact's embedding stayed in the vector store — harmless to recall, since nothing queries a
    note that is gone, and still the fact, on disk, in a store the owner had been told no longer
    held it;
  * its entities stayed in the relationship graph, where they still shaped answers;
  * the journal still described the day it happened, in prose.

Every one of those is the same failure: a deletion that reports success on the strength of having
started. So this deletes across all four, and then RE-QUERIES — because the only evidence that a
thing is forgotten is asking for it again and getting nothing back. If anything survives, it says
what survived rather than reporting a clean sweep.

What it deliberately does NOT touch:

  * **the Obsidian vault.** Those notes are the owner's own writing, synced to a VPS he controls,
    and deleting them from a voice command would be editing his documents, not clearing Afon's
    memory of him. It says so, and leaves `search_vault` to answer whether anything is still there
    — counting it here meant a full-text scan of 6,551 notes on a path that ends in Afon speaking,
    which is how the first version of this hung.
  * **the audit log.** It records that an action happened, with values scrubbed. Rewriting the
    record of what Afon did is a different and much worse thing than forgetting a fact.

    uv run python -m afon.brain.forget     # self-check
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger


@dataclass
class Erasure:
    query: str
    facts: list[str] = field(default_factory=list)   # L1 notes deleted, by text
    vectors: int = 0                                 # L5 embeddings dropped
    triples: int = 0                                 # L5b graph edges dropped
    journal_lines: int = 0                           # L2 lines redacted
    survived: list[str] = field(default_factory=list)  # what a re-query still finds

    @property
    def removed(self) -> int:
        return len(self.facts) + self.vectors + self.triples + self.journal_lines

    @property
    def clean(self) -> bool:
        return not self.survived

    def spoken(self) -> str:
        # Both halves matter. Nothing removed AND nothing found is genuinely "I had nothing".
        # Nothing removed while the re-query still answers is the failure case, and saying
        # "I had nothing stored" there would be the most confident wrong answer in the module.
        if not self.removed and self.clean:
            return f"I had nothing stored about '{self.query}', sir."
        parts = []
        if self.facts:
            parts.append(f"{len(self.facts)} fact{'s' if len(self.facts) != 1 else ''}")
        if self.vectors:
            parts.append(f"{self.vectors} embedding{'s' if self.vectors != 1 else ''}")
        if self.triples:
            parts.append(f"{self.triples} relation{'s' if self.triples != 1 else ''}")
        if self.journal_lines:
            parts.append(f"{self.journal_lines} journal line{'s' if self.journal_lines != 1 else ''}")
        said = (f"Forgotten, sir — I removed {', '.join(parts)} about '{self.query}'."
                if parts else f"Nothing of mine held '{self.query}', sir.")
        if not self.clean:
            # The half that makes the whole thing worth doing. A sweep that says "done" while two
            # stores still answer is worse than one that never ran, because he stops checking.
            said += (f" I checked, and {len(self.survived)} thing"
                     f"{'s' if len(self.survived) != 1 else ''} still came back: "
                     f"{'; '.join(s[:70] for s in self.survived[:3])}.")
        else:
            said += " I checked afterwards and nothing comes back."
        # Said every time, not only when something is found: the owner needs to know the boundary
        # of what a forget covers, and he cannot infer it from a sentence that is sometimes absent.
        said += " Your own vault notes I've left alone — ask me to search them if you want to check."
        return said


def everywhere(query: str, store=None, index=None, graph=None) -> Erasure:
    """Delete `query` from every store Afon owns, then re-query to prove it. Never raises.

    The stores are injectable so the gate can run against temporary ones; production passes none
    and gets the singletons.
    """
    out = Erasure(query=(query or "").strip())
    if not out.query:
        return out

    if store is None:
        from afon.brain.memory import STORE as store
    # The note paths BEFORE deleting, so the vector rows can be found by key afterwards. Read first:
    # once the files are gone there is nothing left to derive the keys from.
    keys: list[str] = []
    try:
        from afon.brain.memory import _terms

        terms = _terms(out.query)
        keys = [str(n.path) for n in store._iter_notes()
                if any(t in n.text.lower() for t in terms)]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"forget: could not list note keys ({type(e).__name__}: {e})")

    try:
        out.facts = store.forget_all(out.query)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"forget: L1 sweep failed ({type(e).__name__}: {e})")
    try:
        out.journal_lines = store.redact_journal(out.query)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"forget: journal redaction failed ({type(e).__name__}: {e})")

    if keys:
        try:
            if index is None:
                from afon.brain.semantic import INDEX

                index = getattr(INDEX, "_store", None) or getattr(INDEX, "_cache_store", None)
            if index is not None and hasattr(index, "forget"):
                out.vectors = index.forget(keys)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"forget: vector sweep failed ({type(e).__name__}: {e})")

    try:
        if graph is None:
            from afon.brain.graph import GRAPH as graph
        out.triples = _sweep_graph(graph, out.query)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"forget: graph sweep failed ({type(e).__name__}: {e})")

    out.survived = _requery(out.query, store)
    logger.info(f"forget {out.query!r}: removed {out.removed} item(s) across four stores; "
                f"{'clean' if out.clean else str(len(out.survived)) + ' survived'}")
    return out


def _sweep_graph(graph, query: str) -> int:
    """Remove every triple whose subject, predicate or object mentions `query`."""
    from afon.brain.memory import _terms

    terms = _terms(query)
    if not terms or graph is None:
        return 0
    gone = 0
    for subj, pred, obj in list(graph.all_triples()):
        blob = f"{subj} {pred} {obj}".lower()
        if any(t in blob for t in terms):
            gone += graph.remove(subj, pred, obj)
    return gone


def _requery(query: str, store) -> list[str]:
    """Ask again. The only evidence a thing is forgotten is that it does not come back."""
    found: list[str] = []
    try:
        # Keyword only: the semantic leg would need the embedder, which is a network call and can
        # be unavailable — and "I could not check" must never read as "nothing came back".
        for hit in store.recall(query, semantic=False) or []:
            text = hit["text"] if isinstance(hit, dict) else str(hit)
            found.append(text)
    except Exception as e:  # noqa: BLE001
        found.append(f"(I couldn't re-check my own memory: {type(e).__name__})")
    return found


def _selfcheck() -> None:
    import tempfile
    from pathlib import Path

    from afon.brain.graph import GraphMemory
    from afon.brain.memory import MemoryStore
    from afon.brain.semantic import VectorStore

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        store = MemoryStore(base_dir=d)
        store.remember("The Lpstrak rabbit farm is in Armenia", source="selfcheck")
        store.remember("The rabbit farm needs a new water system", source="selfcheck")
        store.remember("The owner trains at the gym on Mondays", source="selfcheck")
        store.journal_append("Talked about the rabbit farm today.\nAlso went to the gym.")

        vec = VectorStore(Path(d) / "vectors.sqlite")
        keys = [str(n.path) for n in store._iter_notes()]
        vec.put_many([(k, 0.0, [0.1, 0.2]) for k in keys])

        graph = GraphMemory(Path(d) / "graph.sqlite")
        graph.add("rabbit farm", "located_in", "Armenia")
        graph.add("the owner", "trains_at", "the gym")

        out = everywhere("rabbit farm", store=store, index=vec, graph=graph)
        assert len(out.facts) == 2, out.facts            # BOTH, not the best one
        assert out.triples == 1, out.triples
        assert out.journal_lines == 1, out.journal_lines
        assert out.clean, out.survived
        said = out.spoken()
        assert "Forgotten" in said and "nothing comes back" in said, said
        assert "vault notes I've left alone" in said, said

        # The untouched fact is still there — a sweep that takes the neighbours is worse than none.
        assert store.recall("gym", semantic=False), "forget took an unrelated fact with it"
        assert graph.neighbors("the owner"), "forget took an unrelated relation with it"
        assert "gym" in store.read_journal()

        assert everywhere("nothing at all about this", store=store, index=vec,
                          graph=graph).removed == 0

        # And the honest path: a store that refuses to give the thing up is REPORTED.
        store.remember("The rabbit farm is still mentioned here", source="selfcheck")

        class _Stubborn:
            def forget_all(self, q):
                return []

            def redact_journal(self, q):
                return 0

            def recall(self, q, semantic=None):
                return [{"text": "The rabbit farm is still mentioned here"}]

            def _iter_notes(self):
                return iter(())

        stuck = everywhere("rabbit farm", store=_Stubborn(), index=vec, graph=graph)
        assert not stuck.clean and "still came back" in stuck.spoken(), stuck.spoken()
    print("forget self-check OK")


if __name__ == "__main__":
    _selfcheck()
