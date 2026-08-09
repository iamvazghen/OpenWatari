"""Phase 9 — persistent memory (L1 learned facts + L2 journal), offline + hermetic.

Verifies remember/recall/recency/dedup/forget, the journal, the system-prompt digest, the recall
tool's graceful degradation, and vault (L3) validation. Uses a temp memory dir — touches nothing real.
"""

from __future__ import annotations

import asyncio
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


def main() -> None:
    from afon.brain.memory import MemoryStore

    tmp = Path(tempfile.mkdtemp(prefix="afon-mem-"))
    store = MemoryStore(base_dir=tmp)

    print("[1] remember + recall")
    store.remember("Vazghen runs a rabbit farm called Lpstrak in Armenia.", tags=["rabbit-farm"])
    store.remember("Vazghen prefers short answers in the morning.", tags=["preference"])
    store.remember("The IS24 realty key is openclawsahakagentKey.", tags=["realty"])
    check("count is 3", store.count() == 3, str(store.count()))
    hits = store.recall("rabbit farm")
    check("recall finds the rabbit-farm fact", any("Lpstrak" in h for h in hits), str(hits))
    check("recall returns nothing for unknown topic", store.recall("submarines") == [])

    print("\n[2] tag boosts ranking")
    top = store.recall("preference")
    check("tag-matched fact ranks first", top and "short answers" in top[0], str(top))

    print("\n[3] dedup — identical fact isn't duplicated")
    p1 = store.remember("Vazghen prefers short answers in the morning.")
    check("dedup keeps count at 3", store.count() == 3, str(store.count()))
    check("dedup returns the existing note", p1 is not None and p1.exists())

    print("\n[4] recent_digest is newest-first and capped")
    for i in range(5):
        store.remember(f"Filler fact number {i} about project {i}.")
    digest = store.recent_digest(limit=4)
    check("digest respects the limit", len(digest) == 4, str(len(digest)))
    check("digest is newest-first", "Filler fact number 4" in digest[0], str(digest[0]))

    print("\n[5] forget removes the best match")
    gone = store.forget("IS24 realty key")
    check("forget returns the removed text", gone is not None and "IS24" in gone)
    # Assert the fact is truly GONE via the keyword path. (Semantic recall is on by default now, and
    # an odd token like "IS24" has weak fuzzy neighbours among the other facts — scores ~0.33, on par
    # with a real meaning-match — so a semantic query wouldn't be empty. Its removal is what we test.)
    check("forgotten fact no longer recalls (keyword)", store.recall("IS24", semantic=False) == [])

    print("\n[6] journal append + read")
    store.journal_append("Vazghen asked about the rabbit farm; recalled the Lpstrak charter.")
    store.journal_append("Set a reminder for the IS24 listing review.")
    j = store.read_journal()
    check("journal has both entries", "Lpstrak" in j and "IS24" in j, j)
    check("journal has a day header", j.startswith("# Journal —"), j[:40])

    print("\n[7] empty / missing inputs are safe")
    empty = MemoryStore(base_dir=tmp / "does-not-exist-yet")
    check("recall on empty store -> []", empty.recall("anything") == [])
    check("digest on empty store -> []", empty.recent_digest() == [])
    check("remember('') -> None", store.remember("   ") is None)

    print("\n[8] recall tool degrades when memory disabled")
    import afon.brain.tools.memory as memtool
    import afon.config as cfg

    old = cfg.settings.memory_enabled
    try:
        cfg.settings.memory_enabled = False
        out = asyncio.run(memtool.recall({"query": "rabbit farm"}))
        check("disabled memory -> spoken note, no crash", "switched off" in out, out)
    finally:
        cfg.settings.memory_enabled = old

    print("\n[9] vault (L3) validation reports clearly")
    from afon.brain.context import validate_vault

    ok_v, msg_v = validate_vault()
    check("validate_vault returns (bool, message)", isinstance(ok_v, bool) and isinstance(msg_v, str), msg_v)

    print("\n[10] parse cache: hot reads served from memory, but edits/deletes still seen")
    import time as _time
    cstore = MemoryStore(base_dir=tmp / "cache-test")
    cstore.remember("The rabbit farm has forty does.", tags=["rabbit-farm"])
    _ = cstore.recall("rabbit farm")                       # warms the parse cache
    check("a fact file is cached after first read", len(cstore._note_cache) == 1, str(len(cstore._note_cache)))
    # An external edit (new mtime) must invalidate the cache, not serve stale text.
    note_path = next((tmp / "cache-test" / "learned").glob("*.md"))
    _time.sleep(0.01)
    note_path.write_text(note_path.read_text(encoding="utf-8").replace("forty", "sixty"), encoding="utf-8")
    check("an external edit is picked up (cache invalidated by mtime)",
          any("sixty" in h for h in cstore.recall("rabbit farm")), str(cstore.recall("rabbit farm")))
    # A deleted file must drop out of the cache (no ghosts, no unbounded growth).
    note_path.unlink()
    _ = cstore.recall("rabbit farm")
    check("a deleted fact drops from the cache", len(cstore._note_cache) == 0, str(len(cstore._note_cache)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
