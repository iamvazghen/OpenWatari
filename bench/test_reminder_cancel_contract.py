"""Cancelling a reminder must clear EVERY place it lives — from all three entry points.

A reminder can exist in up to three systems: the in-process APScheduler job, the always-on VPS
ticker, and (for one-shots inside its window) an ntfy server-side push. Cancellation was open-coded
in three modules and only ONE remembered the ticker:

    reminders.cancel_reminder      -> SCHEDULER.cancel + ticker      (complete)
    tasks._cancel_reminder         -> SCHEDULER.cancel               (ticker kept nagging)
    notion._cancel_task_reminder   -> SCHEDULER.cancel               (ticker kept nagging)

So finishing a to-do or a Notion task silenced the local job while the ticker still reminded the
owner about a deadline already met. This asserts all three now go through
`scheduler.cancel_everywhere`, by watching what each one actually CALLS.

Hermetic: the scheduler and the ticker HTTP post are both stubbed; nothing is scheduled and no
request leaves the process.

    uv run python bench/test_reminder_cancel_contract.py
"""

from __future__ import annotations

import asyncio
import sys
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
    from afon.brain import scheduler as sched_mod
    from afon.brain.tools import notion as notion_mod
    from afon.brain.tools import tasks as tasks_mod
    from afon.config import settings

    settings.ticker_url = "https://ticker.example/"     # so the ticker leg is live, not skipped
    settings.ticker_token = ""

    cancelled: list[str] = []
    ticked: list[str] = []

    real_cancel = sched_mod.SCHEDULER.cancel
    real_ticker = sched_mod._cancel_on_ticker

    sched_mod.SCHEDULER.cancel = lambda jid: (cancelled.append(jid), True)[1]

    async def fake_ticker(jid: str) -> None:
        ticked.append(jid)

    sched_mod._cancel_on_ticker = fake_ticker

    try:
        print("[1] the shared contract hits BOTH legs")
        cancelled.clear(); ticked.clear()
        ok = asyncio.run(sched_mod.cancel_everywhere("job-1"))
        check("cancel_everywhere cancels the in-process job", cancelled == ["job-1"], str(cancelled))
        check("...and the VPS ticker", ticked == ["job-1"], str(ticked))
        check("...and reports whether the job was found", ok is True, str(ok))

        print("\n[2] tasks: completing a to-do clears the ticker too")
        cancelled.clear(); ticked.clear()

        class _T:
            meta = {"reminder_job": "job-task"}

        asyncio.run(tasks_mod._cancel_reminder(_T()))
        check("tasks._cancel_reminder cancels the job", cancelled == ["job-task"], str(cancelled))
        check("tasks._cancel_reminder clears the TICKER (the bug)", ticked == ["job-task"], str(ticked))

        print("\n[3] notion: completing/deleting a task clears the ticker too")
        cancelled.clear(); ticked.clear()
        real_load, real_save = notion_mod._load_task_reminders, notion_mod._save_task_reminders
        notion_mod._load_task_reminders = lambda: {"page-9": "job-notion"}
        notion_mod._save_task_reminders = lambda m: None
        try:
            asyncio.run(notion_mod._cancel_task_reminder("page-9"))
        finally:
            notion_mod._load_task_reminders, notion_mod._save_task_reminders = real_load, real_save
        check("notion._cancel_task_reminder cancels the job", cancelled == ["job-notion"], str(cancelled))
        check("notion._cancel_task_reminder clears the TICKER (the bug)",
              ticked == ["job-notion"], str(ticked))

        print("\n[4] nothing to cancel stays a no-op (no phantom ticker calls)")
        cancelled.clear(); ticked.clear()

        class _NoJob:
            meta: dict = {}

        asyncio.run(tasks_mod._cancel_reminder(_NoJob()))
        check("a to-do with no reminder cancels nothing",
              cancelled == [] and ticked == [], f"{cancelled} {ticked}")

        print("\n[5] the ticker leg is best-effort — a dead ticker must not break cancellation")

        async def boom(jid: str) -> None:
            raise RuntimeError("ticker down")

        sched_mod._cancel_on_ticker = boom
        cancelled.clear()
        try:
            asyncio.run(sched_mod.cancel_everywhere("job-2"))
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        # cancel_everywhere awaits the real _cancel_on_ticker, which swallows internally; this stub
        # does not, so a raise here proves the caller has no guard of its own. Either way the LOCAL
        # cancel must already have happened before the ticker is attempted -- order matters, because
        # a ticker outage must never leave the in-process job alive.
        check("the in-process job is cancelled BEFORE the ticker is attempted",
              cancelled == ["job-2"], f"{cancelled} (raised={raised})")
    finally:
        sched_mod.SCHEDULER.cancel = real_cancel
        sched_mod._cancel_on_ticker = real_ticker

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
