"""Phase 9c — L5 semantic recall, offline + hermetic.

Proves the gap and the fix: "bunnies" has zero keyword overlap with a stored "rabbit farm" fact, so
keyword recall misses it — but with an embedder the semantic blend surfaces it. Also verifies the
embedding cache (a fact embeds once across calls) and graceful no-op when no embedder is available.
Uses a tiny deterministic stub embedder, so no model download / no network.
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
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


# A toy "meaning" embedder: each fact/query maps to a bag-of-concepts vector. Synonyms share a
# dimension (rabbit/bunny/farm all live in dim 0), so cosine links them with no shared keyword.
_CONCEPTS = {
    0: {"rabbit", "rabbits", "bunny", "bunnies", "farm", "lpstrak", "hare"},
    1: {"crypto", "bitcoin", "btc", "price", "coin"},
    2: {"morning", "short", "answer", "answers", "brief", "preference"},
}


def _make_stub():
    calls = {"texts": 0}

    def embed(texts):
        calls["texts"] += len(texts)
        out = []
        for t in texts:
            low = t.lower()
            v = [float(sum(w in low for w in words)) for _, words in sorted(_CONCEPTS.items())]
            out.append(v)
        return out

    return embed, calls


def main() -> None:
    from afon.brain.memory import MemoryStore
    from afon.brain.semantic import SemanticIndex

    tmp = Path(tempfile.mkdtemp(prefix="afon-sem-"))
    store = MemoryStore(base_dir=tmp)
    store.remember("Vazghen runs a rabbit farm called Lpstrak in Armenia.", tags=["rabbit-farm"])
    store.remember("Vazghen prefers short answers in the morning.", tags=["preference"])
    store.remember("Vazghen holds some Bitcoin in a hardware wallet.", tags=["crypto"])

    print("[1] keyword recall misses a meaning-only match")
    kw_only = store.recall("bunnies", semantic=False)
    check("'bunnies' finds nothing by keyword", kw_only == [], str(kw_only))

    print("\n[2] semantic recall surfaces the rabbit-farm fact by meaning")
    embed, calls = _make_stub()
    index = SemanticIndex(embed_fn=embed, persist=False)  # in-memory only; keep this test hermetic
    check("injected index reports available", index.available)
    hits = store.recall("bunnies", semantic=index)
    check("semantic recall finds the rabbit farm", any("Lpstrak" in h for h in hits), str(hits))
    check("it does not wrongly surface crypto/preference first",
          hits and "Lpstrak" in hits[0], str(hits[:1]))

    print("\n[3] embedding cache — facts embed once, not per call")
    before = calls["texts"]
    store.recall("crypto coin", semantic=index)   # 3 facts already cached + 1 query
    after = calls["texts"]
    # Only the new query should be embedded again (the 3 facts are memoised by path+mtime).
    check("only the query re-embeds on a second recall", after - before == 1, f"delta {after - before}")

    print("\n[4] graceful no-op when no embedder is installed")
    # A truly bare index = no injected stub, no local sentence-transformers, AND no Jina key to fall
    # back on. The personal .env DOES set AFON_JINA_API_KEY (it powers the web reader), which would
    # otherwise let the index reach the Jina embeddings API and report available. Null it for this
    # check so we're testing the genuine "no embedder anywhere" path.
    from afon.config import settings as _cfg
    _saved_jina = _cfg.jina_api_key
    _cfg.jina_api_key = None
    try:
        # No stub, no Jina key, AND a bogus model name so the local sentence-transformers path (which
        # IS installed in some envs) can't load either — the genuine "no embedder anywhere" path.
        bare = SemanticIndex(embed_fn=None, model_name="__no_such_model_zzz__", persist=False)
        check("bare index is unavailable", not bare.available)
        check("scores() returns {} with no embedder",
              bare.scores("anything", [("k", 0.0, "some text")]) == {})
    finally:
        _cfg.jina_api_key = _saved_jina
    # recall with the (unavailable) real index must equal keyword-only behaviour.
    check("recall stays keyword-only without an embedder", store.recall("submarines") == [])

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
