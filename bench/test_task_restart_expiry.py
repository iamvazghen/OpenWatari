"""A background WORK job must never survive a restart as ``running``.

Background jobs are in-process asyncio tasks. A persisted ``running`` row is therefore
ALWAYS from a dead process, and reloading it as ``running`` is what produced the
fake-"running" pile-up (20 orphans, oldest ~275h, all of them showing on the HUD).
``TaskQueue._load`` expires them to ``failed`` — but nothing asserted that, so a refactor
could reintroduce the pile-up silently. This is that assertion.

Hermetic: temp SQLite, no network, no scheduler.

    uv run python bench/test_task_restart_expiry.py
"""

from __future__ import annotations

import sqlite3
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
    from afon.brain.tasks import TaskQueue

    db = Path(tempfile.mkdtemp()) / "tasks.sqlite"

    # --- process 1: a work job is running, and an owner to-do is open, when we die.
    q1 = TaskQueue(db_path=db)
    job = q1.add("index the vault", kind="work")
    todo = q1.add_todo("call the accountant")
    check("work job starts running", job.status == "running", job.status)
    check("todo starts open", todo.status == "open", todo.status)

    # --- process 2: fresh queue over the SAME file == a restart.
    q2 = TaskQueue(db_path=db)

    # NOTE: these two pass even with the expiry DELETED — `_load` only reloads
    # `kind='todo' AND status='open'`, so `active()` is empty either way. Kept because they
    # guard the HUD-visible behaviour, but they are NOT what proves the expiry: the three
    # DB-level checks below are. Verified by removing the UPDATE and watching exactly those
    # three fail (6/9). Which check fails on sabotage is the only evidence a check works.
    check("dead work job is NOT reloaded as active", job.id not in {t.id for t in q2.active()},
          f"active={[t.id for t in q2.active()]}")
    check("no task reloads as running", q2.active() == [], f"active={q2.active()}")

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT status, last_progress FROM tasks WHERE id=?", (job.id,)).fetchone()
        n_running = c.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0]
    check("dead work job expired to failed", row["status"] == "failed", row["status"])
    check("and says why", "abandoned" in (row["last_progress"] or "").lower(), row["last_progress"])
    check("zero running rows left in the db", n_running == 0, f"n={n_running}")

    # The other half: expiry must not eat the owner's real list.
    check("open todo survives the restart", todo.id in {t.id for t in q2.todos()},
          f"todos={[t.id for t in q2.todos()]}")
    check("todo is still open", (q2.get(todo.id) or todo).status == "open")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
