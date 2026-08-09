"""Persistent L5 vector store (TODO 8.7) — embeddings survive a restart.

The in-memory embedding cache re-embeds every fact on each boot (re-hitting the Jina API when that's
the embedder). This verifies the sqlite-backed VectorStore closes that gap: a fresh SemanticIndex
pointed at the same DB reuses vectors it never computed, only re-embeds when a fact's mtime changes,
and degrades to recompute if the DB is unusable. Offline — a deterministic stub embedder counts calls.

    uv run python bench/test_vector_store.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def _stub():
    """A deterministic embedder that counts how many texts it embeds (hash -> tiny vector)."""
    calls = {"n": 0}

    def embed(texts: list[str]) -> list[list[float]]:
        calls["n"] += len(texts)
        out = []
        for t in texts:
            h = sum(ord(c) for c in t)
            out.append([float(h % 7), float(h % 11), float(h % 13)])
        return out

    return embed, calls


def main() -> None:
    from afon.brain.semantic import SemanticIndex, VectorStore

    tmp = Path(tempfile.mkdtemp())
    db = tmp / "vec.sqlite"

    print("[1] round-trip: put_many then get_many returns the exact vectors")
    store = VectorStore(db)
    store.put_many([("a", 100.0, [1.0, 2.0, 3.0]), ("b", 200.0, [4.0, 5.0, 6.0])])
    got = store.get_many([("a", 100.0), ("b", 200.0)])
    check("both keys returned", set(got) == {"a", "b"}, str(got))
    check("vector a round-trips exactly", got["a"] == [1.0, 2.0, 3.0], str(got.get("a")))
    check("vector b round-trips exactly", got["b"] == [4.0, 5.0, 6.0])

    print("\n[2] mtime guard — a stale mtime is a cache miss (fact changed => re-embed)")
    stale = store.get_many([("a", 999.0)])   # same key, different mtime
    check("mismatched mtime is not served", stale == {}, str(stale))

    print("\n[3] a fresh index reuses persisted vectors instead of re-embedding")
    embed1, calls1 = _stub()
    idx1 = SemanticIndex(embed_fn=embed1, store=VectorStore(db))
    items = [("f1", 10.0, "the rabbit farm in armenia"), ("f2", 20.0, "bitcoin hardware wallet")]
    idx1.scores("bunnies", items)     # embeds query + 2 facts on a cold DB
    check("cold run embeds the facts", calls1["n"] >= 3, f"embedded {calls1['n']}")

    # Simulate a restart: brand-new index + new stub, SAME db file.
    embed2, calls2 = _stub()
    idx2 = SemanticIndex(embed_fn=embed2, store=VectorStore(db))
    idx2.scores("bunnies", items)
    # Only the query should re-embed (1); both facts come from the persistent store.
    check("warm run re-embeds only the query, not the facts", calls2["n"] == 1, f"embedded {calls2['n']}")

    print("\n[4] a changed fact (new mtime) re-embeds; unchanged one still cached")
    embed3, calls3 = _stub()
    idx3 = SemanticIndex(embed_fn=embed3, store=VectorStore(db))
    changed = [("f1", 10.0, "the rabbit farm in armenia"), ("f2", 21.0, "bitcoin hardware wallet EDITED")]
    idx3.scores("bunnies", changed)
    # query (1) + the edited f2 (1) = 2; f1 is unchanged and served from disk.
    check("only query + changed fact re-embed", calls3["n"] == 2, f"embedded {calls3['n']}")

    print("\n[5] scores still correct with persistence on (cosine in [0,1])")
    embed4, _ = _stub()
    idx4 = SemanticIndex(embed_fn=embed4, store=VectorStore(db))
    sc = idx4.scores("rabbit", items)
    check("scores returned for both facts", set(sc) == {"f1", "f2"}, str(sc))
    check("all scores in range", all(-1.0001 <= v <= 1.0001 for v in sc.values()), str(sc))

    print("\n[6] a broken DB path degrades to recompute, never crashes")
    bad = SemanticIndex(embed_fn=_stub()[0], store=VectorStore(Path(tmp / "nope" / "cant" / "x.sqlite")))
    sc2 = bad.scores("anything", items)   # dir doesn't exist -> store read/write fail -> recompute
    check("scoring survives an unwritable store", set(sc2) == {"f1", "f2"}, str(sc2))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
