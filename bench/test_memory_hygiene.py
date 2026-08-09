"""P1 #6 — memory hygiene keeps the learned/journal stores healthy over time.

Hermetic: a temp MemoryStore. Seeds near-duplicate facts + an old journal day, runs the hygiene
passes, and asserts: near-dupes are deduped (newest kept, recall still finds it), the active set is
capped (oldest archived, not lost), and old journals are rotated out of the hot path.

    uv run python bench/test_memory_hygiene.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
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
    from afon.brain.maintenance import cap_learned, compact_learned, rotate_journals
    from afon.brain.memory import MemoryStore

    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(base_dir=d)

        print("[1] near-duplicate learned facts are deduped (newest kept, recall still works)")
        store.remember("Vazghen trains at the gym on Mondays and Wednesdays")
        store.remember("Vazghen trains at the gym on Mondays and Wednesdays usually")  # near-dupe
        store.remember("Vazghen trains at the gym on Mondays and also on Wednesdays normally")  # near-dupe
        store.remember("The Lpstrak rabbit farm is in Armenia")                        # distinct
        before = store.count()
        res = compact_learned(store)
        after = store.count()
        check("started with 4 facts", before == 4, str(before))
        check("removed the near-duplicates", res["removed"] >= 1, str(res))
        check("kept the distinct fact", after < before and after >= 2, f"{before}->{after}")
        check("recall still finds the training fact", bool(store.recall("gym training", semantic=False)))
        check("recall still finds the rabbit farm", bool(store.recall("rabbit farm", semantic=False)))

        print("\n[2] the active set is capped (oldest archived, not deleted)")
        for i in range(12):
            store.remember(f"Distinct fact number {i} about topic {i} and detail {i}")
        capped = cap_learned(store, max_facts=5)
        check("archived the overflow", capped["archived"] >= 1, str(capped))
        check("active set is now at the cap", store.count() == 5, str(store.count()))
        archive = Path(d) / "learned" / "archive"
        check("archived facts are preserved on disk", archive.is_dir() and any(archive.glob("*.md")))

        print("\n[3] old journals rotate out of the hot path; recent stay")
        today = datetime.now(timezone.utc)
        old = today - timedelta(days=60)
        store.journal_append("ancient entry", when=old)
        store.journal_append("todays entry", when=today)
        rot = rotate_journals(store, keep_days=35)
        check("archived the old journal day", rot["archived"] == 1, str(rot))
        hot = list((Path(d) / "journal").glob("*.md"))
        check("only the recent day remains in the hot path", len(hot) == 1, str([p.name for p in hot]))
        check("read_journal returns today's entry", "todays entry" in store.read_journal())
        jarchive = Path(d) / "journal" / "archive"
        check("the old day is archived, not lost", jarchive.is_dir() and any(jarchive.glob("*.md")))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
