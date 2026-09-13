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

    print("\n[per-store TTL] 37.F2 — retention is enforced by the job, not by intention")
    # Every store had a retention in somebody's head, and exactly one — presence — had it in a
    # setting something actually read. Everything else grew forever while the docs said otherwise,
    # which is the worse of the two failures: a stated policy nobody executes is a promise to the
    # owner that is quietly not kept.
    import os
    import time

    from afon.brain import inventory as I
    from afon.brain.maintenance import sweep_retention
    from afon.config import settings

    real_root = settings.state_dir
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        try:
            settings.state_dir = root
            base = Path(root)
            long_ago = time.time() - 400 * 86400
            recently = time.time() - 60

            # A rolling store: files age out. Two old, one new.
            traces = base / "traces"
            traces.mkdir()
            for name, when in (("old-1.jsonl", long_ago), ("old-2.jsonl", long_ago),
                               ("today.jsonl", recently)):
                f = traces / name
                f.write_text("{}", encoding="utf-8")
                os.utime(f, (when, when))

            # A WHILE_CURRENT document that has not been written in a year, and must survive.
            pending = base / "approvals.json"
            pending.write_text('{"approvals": [{"id": "a1"}]}', encoding="utf-8")
            os.utime(pending, (long_ago, long_ago))

            # A sqlite store, which the sweep must not touch: deleting rows by age needs a schema.
            db = base / "afon_presence.sqlite"
            db.write_text("not really sqlite", encoding="utf-8")
            os.utime(db, (long_ago, long_ago))

            dry = sweep_retention(dry_run=True)
            check("a dry run reports what it would remove", "traces" in dry, sorted(dry))
            check("...and removes nothing", (traces / "old-1.jsonl").exists())

            out = sweep_retention()
            check("expired files in a rolling store are deleted",
                  not (traces / "old-1.jsonl").exists() and not (traces / "old-2.jsonl").exists())
            check("...both of them counted", out.get("traces", {}).get("removed") == 2, out)
            check("...and the recent one is kept", (traces / "today.jsonl").exists())
            check("the freed space is reported", out.get("traces", {}).get("freed_bytes", 0) > 0, out)
            check("the store's declared retention is named in the result",
                  out.get("traces", {}).get("days") == I.by_name("traces").retention_days, out)

            # The two refusals, both found by dry-running the first version of this sweep.
            check("a pending-approvals file is NOT deleted for being untouched",
                  pending.exists(), "file mtime is not age for a document that IS current state")
            check("...and it is not in the result either", "approvals.json" not in out, sorted(out))
            check("a sqlite store is left to its own module", db.exists())
            check("...and is not claimed as swept", "afon_presence.sqlite" not in out, sorted(out))

            # Idempotent: a second run has nothing left to do.
            again = sweep_retention()
            check("a second sweep is a no-op", again == {}, again)

            # And it is wired into the job that already runs daily, not a script nobody calls.
            src = (Path(__file__).resolve().parents[1]
                   / "src/afon/brain/maintenance.py").read_text(encoding="utf-8")
            check("the daily hygiene job calls it",
                  "sweep_retention()" in src.split("async def run_maintenance")[1])
            check("...and says what it removed", "retention dropped" in src)
        finally:
            settings.state_dir = real_root

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
