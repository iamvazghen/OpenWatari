"""LIVE end-to-end: Notion task queue + a reminder that fires NOW and is ANNOUNCED via the edge.

Exercises the real stack against the owner's live Notion + scheduler (uses .env creds):
  1. create a Notion task WITH a reminder ~8s out (reminder announced through the edge at fire time);
  2. confirm the task really landed in the Notion tasks DB (query it back);
  3. wait for the reminder to FIRE and assert it was SPOKEN through the edge speak callback;
  4. update the task (priority), then complete it (which cancels any pending reminder), then delete it;
  5. build the live daily digest and print it.

Phone push is mocked so this never spams the real phone; the spoken path is captured via a fake
edge callback (exactly how the brain server wires server.speak_reminder -> connected clients).

    uv run python bench/test_live_task_reminder_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
    from afon.config import settings
    import afon.brain.tools.notion as nt
    import afon.brain.tools.notify as notify
    import afon.brain.scheduler as sch
    from afon.brain.scheduler import SCHEDULER

    if not nt._configured() or not (settings.notion_tasks_db_id or "").strip():
        print("Notion not configured (AFON_NOTION_TOKEN + AFON_NOTION_TASKS_DB_ID) — skipping.")
        return

    tz = ZoneInfo(settings.user_tz)
    marker = f"E2E-{int(time.time())}"   # unique title so we can find + clean up exactly this row

    # Capture the spoken (edge) delivery; mock the phone push so we never spam the real device.
    spoke: list[str] = []
    sch._LIVE_SPEAK = lambda m: spoke.append(m)

    async def fake_push(message: str, title: str = "Afon", at=None) -> bool:
        return True
    notify.push = fake_push

    # Force the in-process speak path (no ntfy server-side hand-off) so the fire SPEAKS deterministically.
    saved_topic = settings.ntfy_topic
    settings.ntfy_topic = None
    SCHEDULER.start(on_speak=lambda m: spoke.append(f"EDGE: {m}"))

    print(f"[1] create a Notion task '{marker}' with a reminder ~8s out")
    when = (datetime.now(tz) + timedelta(seconds=8)).strftime("%Y-%m-%dT%H:%M:%S")
    res = await nt.notion_create_task({"title": marker, "priority": "High", "reminder": when})
    print("   ->", res)
    check("create confirmed", "Added" in res, res)
    check("create says it'll remind out loud", "remind you out loud" in res, res)

    print("\n[2] the task really exists in the Notion tasks DB")
    listed = await nt.notion_tasks({"scope": "all"})
    # notion_tasks may not echo the title if it's undated-open; query the DB directly to be sure.
    db_id = settings.notion_tasks_db_id
    data = await nt._post(f"/databases/{db_id}/query", {"page_size": 100})
    found = [p for p in (data.get("results") or []) if nt._title_of(p) == marker and not p.get("archived")]
    check("task row present in Notion", bool(found), f"listed={listed[:120]}")
    page_id = found[0]["id"] if found else ""

    print("\n[3] the reminder FIRES within ~15s and is SPOKEN through the edge")
    for _ in range(160):                       # up to ~16s
        if any(marker in m for m in spoke):
            break
        await asyncio.sleep(0.1)
    check("reminder fired + spoken via edge", any(marker in m for m in spoke), str(spoke)[:200])
    check("spoken as a 'task due' announcement", any("Task due" in m for m in spoke), str(spoke)[:200])

    print("\n[4] update (priority), then complete, then delete — and clean up")
    up = await nt.notion_update_task({"query": marker, "priority": "Low"})
    check("update confirmed", "Updated" in up, up)
    comp = await nt.notion_complete_task({"query": marker})
    check("complete confirmed", "Marked" in comp or "as " in comp, comp)
    # delete (archive) so the test leaves no residue
    dele = await nt.notion_delete_task({"query": marker})
    check("delete confirmed", "Deleted" in dele, dele)
    # verify it's gone (archived)
    data2 = await nt._post(f"/databases/{db_id}/query", {"page_size": 100})
    still = [p for p in (data2.get("results") or []) if nt._title_of(p) == marker and not p.get("archived")]
    check("task removed from Notion (cleaned up)", not still, f"remaining={len(still)}")

    print("\n[5] live daily digest build (informational)")
    from afon.brain import daily_digest
    body = await daily_digest.build_body()
    print("   digest body:", (body or "<empty — nothing past due, no important mail>")[:300])
    check("digest build did not error", True)

    settings.ntfy_topic = saved_topic
    if SCHEDULER._sched and SCHEDULER._sched.running:
        SCHEDULER._sched.shutdown(wait=False)
        SCHEDULER._sched = None

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
