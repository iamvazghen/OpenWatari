"""Manageable to-do list — add, describe, prioritise, deadline, track progress, complete, delete.

Proves the owner-facing task queue (distinct from background fleet jobs):
  * add a task with description + priority + deadline; it persists;
  * list is sorted most-urgent first (priority, then nearest deadline);
  * update changes priority/deadline/progress; progress is tracked and clamped;
  * a deadline schedules a SPOKEN reminder (edge path) — we assert the scheduler was asked;
  * complete marks it done + drops it from the live list; delete removes it;
  * the whole list survives a restart (reload from SQLite).

Hermetic: temp SQLite queue, the scheduler is monkeypatched (no APScheduler/network).

    uv run python bench/test_task_todos.py
"""

from __future__ import annotations

import asyncio
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


async def main() -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from afon.brain.tasks import TaskQueue, normalize_priority
    import afon.brain.tools.tasks as tt
    from afon.config import settings

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db = Path(tmp.name) / "tasks.sqlite"
    q = TaskQueue(db_path=db)
    tt.TASKS = q  # the handlers resolve TASKS from this module binding at call time

    # Capture scheduled reminders instead of touching APScheduler / ntfy.
    scheduled: list[tuple[str, str]] = []

    class _FakeSched:
        def add_reminder(self, message, in_minutes=None, at=None, daily=None):
            scheduled.append((message, at))
            return ("job-" + str(len(scheduled)), "soon", None)

        def cancel(self, job_id):
            scheduled.append(("CANCEL", job_id))
            return True

        def set_push_phone(self, *_a):
            pass

    import afon.brain.scheduler as sched_mod
    sched_mod.SCHEDULER = _FakeSched()

    print("[1] priority normalisation")
    check("'critical' -> urgent", normalize_priority("critical") == "urgent")
    check("'med' -> normal", normalize_priority("med") == "normal")
    check("blank -> normal", normalize_priority("") == "normal")
    check("'important' -> high", normalize_priority("important") == "high")

    print("\n[2] add tasks with description / priority / deadline")
    tz = ZoneInfo(settings.user_tz)
    soon = (datetime.now(tz) + timedelta(days=2)).strftime("%Y-%m-%d")
    later = (datetime.now(tz) + timedelta(days=9)).strftime("%Y-%m-%d")
    r1 = await tt.add_task({"title": "file taxes", "description": "gather receipts first",
                            "priority": "high", "deadline": soon})
    r2 = await tt.add_task({"title": "buy milk", "priority": "low"})
    r3 = await tt.add_task({"title": "ship release", "priority": "urgent", "deadline": later})
    check("add_task confirms", "Added to your list" in r1, r1)
    check("deadline task scheduled a spoken reminder", any("file taxes" in m for m, _ in scheduled))
    todos = q.todos()
    check("all three stored", len(todos) == 3, str(len(todos)))

    print("\n[3] list is most-urgent-first (priority, then deadline)")
    order = [t.title for t in q.todos()]
    check("urgent 'ship release' sorts first", order[0] == "ship release", str(order))
    check("high 'file taxes' second", order[1] == "file taxes", str(order))
    check("low 'buy milk' last", order[-1] == "buy milk", str(order))
    listed = await tt.list_tasks({})
    check("list_tasks shows priority + deadline", "urgent" in listed and "due in" in listed, listed)

    print("\n[4] update: progress tracking + priority change")
    up = await tt.update_task({"topic": "milk", "priority": "high", "progress": 40,
                               "note": "found the shop"})
    check("update confirms", "Updated" in up, up)
    milk = q.find_todo("milk")[0]
    check("priority bumped to high", milk.priority == "high")
    check("progress recorded (40)", milk.progress == 40, str(milk.progress))
    over = await tt.update_task({"topic": "milk", "progress": 250})
    check("progress clamped to 100", q.find_todo("milk")[0].progress == 100)

    print("\n[5] update deadline reschedules the spoken reminder")
    scheduled.clear()
    new_dl = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    await tt.update_task({"topic": "taxes", "deadline": new_dl})
    check("old reminder cancelled", any(m == "CANCEL" for m, _ in scheduled))
    check("new reminder scheduled", any("file taxes" in m for m, _ in scheduled if m != "CANCEL"))

    print("\n[6] status query on one task")
    st = await tt.task_status({"topic": "release"})
    check("task_status reports the item", "ship release" in st and "urgent" in st, st)

    print("\n[7] complete + delete")
    done = await tt.complete_task({"topic": "milk"})
    check("complete confirms", "done" in done.lower(), done)
    check("completed task left the open list", not any(t.title == "buy milk" for t in q.todos()))
    check("completed row kept as a record", any(t.title == "buy milk" for t in q.todos(include_done=True)))
    dele = await tt.delete_task({"topic": "release"})
    check("delete confirms", "Deleted" in dele, dele)
    check("deleted task gone entirely", not any(t.title == "ship release" for t in q.todos(include_done=True)))

    print("\n[8] the list survives a restart (reload from SQLite)")
    q2 = TaskQueue(db_path=db)
    reloaded = {t.title for t in q2.todos()}
    check("open task reloaded", "file taxes" in reloaded, str(reloaded))
    ft = q2.find_todo("taxes")[0]
    check("reloaded task kept its priority", ft.priority == "high")
    check("reloaded task kept its deadline", ft.deadline is not None)

    print("\n[9] tools registered for the model")
    from afon.brain.tools import tool_names
    names = tool_names()
    for n in ("add_task", "update_task", "complete_task", "delete_task", "list_tasks", "task_status"):
        check(f"{n} registered", n in names)

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
