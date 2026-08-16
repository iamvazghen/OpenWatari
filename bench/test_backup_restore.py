"""A backup that has never been restored is not a backup — SYSTEMS.md 22.F1 and 22.F4.

Two halves. The first is the roundtrip: temp memory dir → backup zip → destroy → restore →
byte-identical, plus the no-clobber guard that stops a fat-fingered restore overwriting live memory.

The second is 22.F4, and it is the one that changes what this file is worth. Creating an archive
proves that a zip was written. It does not prove the zip can be read back, and every way a backup
actually fails — a truncated write, a member whose CRC no longer matches, an archive that unpacks to
nothing, a nightly job that quietly stopped a month ago — is invisible right up until the morning
someone needs it. So the drill restores the newest real archive into a throwaway directory on a
schedule, and this file proves the drill fails when it should.

The corrupt-archive check matters more than it looks: a damaged member's CRC is only checked on
*decompression*, so a verifier that trusted `namelist()` would pass a zip it cannot actually read.

Run:
    uv run python bench/test_backup_restore.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.protocols.backup import (  # noqa: E402
    MAX_BACKUP_AGE_S,
    last_drill,
    latest_backup,
    make_backup,
    restore_backup,
    run_restore_drill,
    verify_backup,
)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _memory(root: Path) -> Path:
    mem = root / "memory"
    (mem / "learned").mkdir(parents=True)
    (mem / "learned" / "fact.md").write_text("owner runs a rabbit farm", encoding="utf-8")
    (mem / "journal.md").write_text("day one", encoding="utf-8")
    return mem


def main() -> int:
    print("[1] the roundtrip: a wiped memory dir comes back byte-identical")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = _memory(root)
        archive = make_backup(memory, root / "backups")
        check("a backup is written", archive.is_file() and archive.stat().st_size > 0)

        shutil.rmtree(memory)                      # disaster
        restore_backup(archive, memory)
        check("a nested fact survives the round trip",
              (memory / "learned" / "fact.md").read_text(encoding="utf-8")
              == "owner runs a rabbit farm")
        check("and so does a top-level file",
              (memory / "journal.md").read_text(encoding="utf-8") == "day one")

        refused = False
        try:
            restore_backup(archive, memory)
        except RuntimeError:
            refused = True
        check("restoring over live memory refuses without force", refused,
              "a mistyped restore would silently clobber everything he has")
        restore_backup(archive, memory, force=True)
        check("and proceeds with it", (memory / "journal.md").is_file())

    print("\n[2] the drill reads the archive back, and says what it found")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = _memory(root)
        archive = make_backup(memory, root / "backups")
        # Scoped to THIS call, deliberately. Globbing the shared temp dir picks up leftovers from any
        # earlier run — including a previous falsification of this very check — so the check would
        # fail for reasons that have nothing to do with the code under test.
        scratch = set(Path(tempfile.gettempdir()).glob("afon-restore-drill-*"))
        res = verify_backup(archive)
        check(f"a good archive verifies ({res['files']} files, {res['bytes']} bytes, {res['ms']}ms)",
              res["ok"] and res["files"] == 2 and res["bytes"] > 0, str(res))
        check("a fresh backup is not flagged stale", res.get("stale") is False)
        left = set(Path(tempfile.gettempdir()).glob("afon-restore-drill-*")) - scratch
        check("the drill leaves no scratch directory behind", not left,
              f"a daily drill that leaks a restore per run fills the disk it protects: {left}")

    print("\n[3] and it fails when the backup is not actually a backup")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = _memory(root)
        archive = make_backup(memory, root / "backups")

        # The failure that matters: bytes damaged inside a member. The index still lists it, so only
        # decompression notices — which is precisely why the drill reads every file.
        raw = bytearray(archive.read_bytes())
        raw[30:60] = b"\x00" * 30
        corrupt = root / "corrupt.zip"
        corrupt.write_bytes(bytes(raw))
        res = verify_backup(corrupt)
        check("a corrupt member is caught, not listed and trusted",
              not res["ok"] and res["error"], str(res)[:160])

        empty = root / "empty.zip"
        with zipfile.ZipFile(empty, "w"):
            pass
        res = verify_backup(empty)
        check("an archive that restores nothing is a failure, not an empty success",
              not res["ok"] and "empty" in res["error"], str(res)[:160])

        res = verify_backup(root / "nope.zip")
        check("a missing archive is a typed failure, not an exception", not res["ok"] and res["error"])

        res = run_restore_drill(backups=root / "no-backups-here", record_to=root / "d1.json")
        check("no backup at all is reported as such — the loudest possible silence",
              not res["ok"] and "no backup" in res["error"], str(res)[:160])

    print("\n[4] a stale archive is reported even when it restores perfectly")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = _memory(root)
        archive = make_backup(memory, root / "backups")
        old = time.time() - (MAX_BACKUP_AGE_S + 3600)
        import os

        os.utime(archive, (old, old))
        res = verify_backup(archive)
        check("a backup older than a day restores fine AND is flagged stale",
              res["ok"] and res["stale"],
              "a job that stopped a month ago leaves a perfectly restorable, useless archive")

    print("\n[5] the newest archive is the one drilled, and the verdict is filed")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = _memory(root)
        backups = root / "backups"
        first = make_backup(memory, backups)
        # The stamp is second-resolution, so a same-second second backup would collide. Name it
        # explicitly rather than sleeping a second in a test.
        second = backups / "afon-memory-29991231-235959.zip"
        shutil.copy(first, second)
        check("latest_backup picks the newest stamp, not the first found",
              latest_backup(backups) == second, str(latest_backup(backups)))
        check("and an empty backups dir yields None rather than raising",
              latest_backup(root / "gone") is None)

        record = root / "drill.json"
        res = run_restore_drill(backups=backups, record_to=record)
        check("the drill runs against the real backups dir", res["ok"], str(res)[:160])
        filed = last_drill(record)
        check("the verdict is filed where a surface can read it without re-restoring",
              filed is not None and filed["ok"] and filed["checked"] == res["checked"])
        check("and a never-run drill reads as None, not as a pass",
              last_drill(root / "never.json") is None)
        check("live memory is untouched by the drill",
              (memory / "journal.md").read_text(encoding="utf-8") == "day one",
              "a drill that restores into live memory is a disaster wearing a checklist")

    print("\n[6] it is actually scheduled — 22.F4 says 'on a schedule', not 'on request'")
    from afon.brain import loops
    from afon.brain.scheduler import _fire_restore_drill  # noqa: F401 — importable = schedulable

    check("the drill is a declared background loop with a period and a budget",
          "daily-restore-drill" in loops.BY_NAME
          and loops.BY_NAME["daily-restore-drill"].period_s == 86_400
          and loops.BY_NAME["daily-restore-drill"].budget_ms > 0)
    sched_src = (ROOT / "src" / "afon" / "brain" / "scheduler.py").read_text(encoding="utf-8")
    check("the scheduler registers it under a fixed job id",
          'id="daily-restore-drill"' in sched_src and "_fire_restore_drill" in sched_src)
    server_src = (ROOT / "src" / "afon" / "brain" / "server.py").read_text(encoding="utf-8")
    check("and the brain calls that registration at start-up",
          "schedule_restore_drill()" in server_src,
          "a scheduling method nobody calls is the exact shape of the dead-cron bug this closes")
    check("it runs after the backup it verifies (03:30 backup, 05:00 drill)",
          'schedule_restore_drill(self, hhmm: str = "05:00")' in sched_src,
          "drilling before the nightly backup verifies yesterday's archive")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
