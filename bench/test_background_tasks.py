"""Background task queue — fire long work, keep live status, announce completion by voice.

Proves the "status-keeping butler" path (docs/TASK-QUEUE-PLAN.md item 3):
  * a backgrounded job runs to completion, streams progress, then is announced + dropped;
  * a failing job is recorded as failed and announced — never crashes the brain;
  * the read tools (list_tasks / task_status) report the live queue;
  * the tools + the delegate 'background' option are registered for the model to use.

Hermetic: a temp SQLite queue, no network, controllable coroutines.

    uv run python bench/test_background_tasks.py
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


async def _settle(q, timeout: float = 2.0) -> None:
    """Wait until the queue has no active tasks (or a timeout)."""
    for _ in range(int(timeout / 0.02)):
        if not q.active():
            return
        await asyncio.sleep(0.02)


async def main() -> None:
    from afon.brain.tasks import TaskQueue
    import afon.brain.tools.tasks as tt

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    q = TaskQueue(db_path=Path(tmp.name) / "tasks.sqlite")
    tt.TASKS = q  # the read tools resolve TASKS at call time from this module binding

    announced: list[tuple[str, str, str]] = []

    async def on_complete(t) -> None:
        announced.append((t.title, t.status, t.result))

    q.on_complete = on_complete

    print("[1] a backgrounded job runs, streams progress, completes, announces, drops")

    async def work(on_progress):
        on_progress("step 1 of 2")
        on_progress("step 2 of 2")
        return "all done: site rebuilt, tests green"

    t = q.run("rebuild the website", work, kind="fleet")
    check("run() returns a task id immediately", bool(t.id))
    check("task is active right after run()", t.id in {x.id for x in q.active()})
    await _settle(q)
    check("task left the active queue once done", t.id not in {x.id for x in q.active()})
    check("completion announced as done", bool(announced) and announced[0][1] == "done")
    check("announcement carries the result payload", "site rebuilt" in (announced[0][2] if announced else ""))

    print("\n[2] a failing job is recorded failed + announced, never raises")
    announced.clear()

    async def boom(on_progress):
        raise RuntimeError("ispir timed out")

    q.run("deep research", boom)
    await _settle(q)
    check("failed task announced (brain did not crash)", bool(announced) and announced[0][1] == "failed")

    print("\n[3] the read tools report the live queue")
    release = asyncio.Event()

    async def slow(on_progress):
        on_progress("working on the hero section")
        await release.wait()
        return "hero done"

    t3 = q.run("portfolio site", slow)
    await asyncio.sleep(0.05)
    listed = await tt.list_tasks({})
    check("list_tasks shows the running task", "portfolio site" in listed)
    status = await tt.task_status({"topic": "portfolio"})
    check("task_status finds it by a topic word", "portfolio site" in status)
    check("task_status reports it as in progress", "in progress" in status)
    check("task_status surfaces the latest progress note", "hero section" in status)
    release.set()
    await _settle(q)
    check("slow task completed after release", t3.id not in {x.id for x in q.active()})

    print("\n[4] the tools + the delegate 'background' option are registered for the model")
    from afon.brain.tools import tool_names

    names = tool_names()
    check("list_tasks registered in the tool surface", "list_tasks" in names)
    check("task_status registered in the tool surface", "task_status" in names)
    from afon.brain.fleet import FLEET_TOOL_SCHEMA

    props = FLEET_TOOL_SCHEMA["function"]["parameters"]["properties"]
    check("delegate_to_fleet exposes a 'background' option", "background" in props)

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001 — Windows may still hold the sqlite handle; harmless
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
