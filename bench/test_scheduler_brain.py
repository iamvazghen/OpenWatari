"""P0 #1 — the always-on brain owns the reminder scheduler.

Proves the fix for the gap where SCHEDULER.start() ran only on the laptop edge: now the brain
server starts the scheduler, a scheduled job actually FIRES while the brain runs, and a fired
reminder is spoken to connected clients (with phone-push owned by scheduler._fire, not duplicated).

Hermetic: a temp SQLite jobstore, monkeypatched ntfy push (no network), short real timer.

    uv run python bench/test_scheduler_brain.py
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


class FakeWS:
    """Minimal stand-in for a connected client websocket — records what the brain sends it."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


async def main() -> None:
    from afon.config import settings

    # Isolate the jobstore so the test never touches the real afon_jobs.sqlite.
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # sqlite file may linger on Win
    settings.scheduler_db_path = str(Path(tmp.name) / "jobs.sqlite")

    import afon.brain.scheduler as sch
    from afon.brain.scheduler import SCHEDULER
    from afon.brain.server import BrainServer

    print("[1] brain speaks a fired reminder to connected clients (no push here)")
    server = BrainServer()
    fake = FakeWS()
    server._conns["s1"] = fake
    server.speak_reminder("drink water")
    await asyncio.sleep(0.05)  # let the scheduled broadcast task run
    check("reminder reached the client", any("drink water" in m for m in fake.sent), str(fake.sent))
    check("spoken as a reminder", any("Reminder, sir" in m for m in fake.sent))

    print("\n[2] no client connected -> safe no-op (ntfy still delivers via _fire)")
    server2 = BrainServer()
    server2.speak_reminder("nobody home")   # must not raise
    await asyncio.sleep(0.02)
    check("speak_reminder with no clients is a safe no-op", True)

    print("\n[3] the scheduler actually RUNS while the brain is up (the core fix)")
    SCHEDULER.start(on_speak=server.speak_reminder)
    check("scheduler is running after start()", bool(SCHEDULER._sched and SCHEDULER._sched.running))

    # Monkeypatch the phone push so a real fire stays offline; record speak + push separately.
    spoke: list[str] = []
    pushed: list[str] = []
    sch._LIVE_SPEAK = lambda m: spoke.append(m)

    async def _fake_push(message: str, title: str = "Afon") -> bool:
        pushed.append(message)
        return True

    import afon.brain.tools.notify as notify
    notify.push = _fake_push  # _fire imports push lazily, so this patch is picked up

    print("\n[4] a real scheduled job fires within seconds (add_reminder -> _fire)")
    # Schedule ~1.2 s out via the public API; force the in-process push path (push_phone=True)
    # by disabling ntfy server-side scheduling for this check.
    saved_topic = settings.ntfy_topic
    settings.ntfy_topic = None
    job_id, when, ntfy_epoch = SCHEDULER.add_reminder(message="take a break", in_minutes=0.02)
    settings.ntfy_topic = saved_topic
    check("add_reminder returned a job id", bool(job_id), when)
    try:
        for _ in range(80):                 # up to ~8 s (slack for a starved scheduler under gate load)
            if spoke and pushed:
                break
            await asyncio.sleep(0.1)
        check("fired reminder was SPOKEN via on_speak", "take a break" in spoke, str(spoke))
        check("fired reminder was PUSHED once via _fire", pushed.count("take a break") == 1, str(pushed))
    finally:
        if SCHEDULER._sched and SCHEDULER._sched.running:
            SCHEDULER._sched.shutdown(wait=False)
            SCHEDULER._sched = None
        try:
            tmp.cleanup()
        except Exception:  # noqa: BLE001 — Windows may still hold the sqlite handle; harmless
            pass

    print("\n[3] daily digest briefing builds the catch-up and delivers it via proactive emit")
    captured: dict = {}

    async def fake_emit(msg, urgency, speak):
        captured.update(msg=msg, urgency=urgency, speak=speak)
        return "voice"

    import afon.brain.daily_digest as dd
    SCHEDULER.set_briefing_emit(fake_emit)

    # Isolate the digest delivery-state file so this test never touches real state / re-fires.
    dd._state_path = lambda: Path(tmp.name) / "digest_state.json"  # type: ignore[assignment]

    _orig_build = dd.build_body

    async def fake_body():
        return "2 past-due tasks: rent (2d overdue); call the bank (1d overdue). And 3 important emails unread, the latest from Jane"
    dd.build_body = fake_body
    try:
        await sch._fire_briefing()
        check("briefing spoken (speak=True)", captured.get("speak") is True)
        check("briefing opens with a greeting", str(captured.get("msg", "")).startswith("Good morning"))
        check("briefing includes past-due tasks", "rent (2d overdue)" in captured.get("msg", ""))
        check("briefing includes important emails", "important emails" in captured.get("msg", ""))
        check("push channel marked delivered", not dd.due("push"))
        # 'nothing due' path
        async def empty_body():
            return ""
        dd.build_body = empty_body
        captured.clear()
        # a fresh day so the push channel is due again
        dd._save({})
        await sch._fire_briefing()
        check("clear-day briefing still sent", "clear" in captured.get("msg", "").lower())
    finally:
        dd.build_body = _orig_build
        SCHEDULER.set_briefing_emit(None)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
