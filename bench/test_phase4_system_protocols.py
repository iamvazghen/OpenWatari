"""Phase 4 + system/browser/protocols verification — all SAFE (no real kills/shutdowns).

Covers:
  [1] system tools: file_op on a temp dir, protected-path refusal, run_powershell echo,
      process_op list.
  [2] browser tool: disabled-flag path (no real browser launched here).
  [3] protocols: unknown name lists options, missing/wrong password refuses, CORRECT password
      passes the gate — the actual launch is STUBBED so nothing is killed/restarted.
  [4] scheduler/reminders: set -> list -> cancel against a temp SQLite jobstore.

    uv run python bench/test_phase4_system_protocols.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import settings  # noqa: E402

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def main() -> None:
    from jarvis.brain.tools import system, browser
    import jarvis.brain.protocols as P
    from jarvis.brain.tools import protocols as protocols_tool

    print("[1] system tools")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        folder = base / "jarvis_test_dir"
        f = folder / "note.txt"
        r = await system.file_op({"action": "create_folder", "path": str(folder)})
        check("create_folder", folder.is_dir(), r)
        r = await system.file_op({"action": "create_file", "path": str(f), "content": "hello sir"})
        check("create_file writes content", f.read_text(encoding="utf-8") == "hello sir", r)
        r = await system.file_op({"action": "list", "path": str(folder)})
        check("list shows the file", "note.txt" in r, r)
        r = await system.file_op({"action": "delete_file", "path": str(f)})
        check("delete_file", not f.exists(), r)
        r = await system.file_op({"action": "delete_folder", "path": str(folder)})
        check("delete_folder", not folder.exists(), r)
    r = await system.file_op({"action": "delete_folder", "path": "C:\\Windows"})
    check("delete refuses protected path", "protected" in r, r)

    r = await system.run_powershell({"command": "Write-Output JARVIS_OK"})
    check("run_powershell returns output", "JARVIS_OK" in r, r)
    r = await system.process_op({"action": "list", "name": "python"})
    check("process_op list works", "process" in r.lower(), r)
    r = await system.process_op({"action": "kill"})
    check("process_op kill without target asks", "name or pid" in r, r)

    print("\n[2] browser tool (disabled-flag path, no real launch)")
    saved_browser = settings.browser_tools_enabled
    settings.browser_tools_enabled = False
    try:
        r = await browser.browser({"action": "open", "url": "example.com"})
        check("browser respects disabled flag", "disabled" in r, r)
    finally:
        settings.browser_tools_enabled = saved_browser

    print("\n[3] protocols (password-gated; launch STUBBED)")
    expected_protocols = {
        "goodnight", "phoenix", "ragnarok", "backup", "ping",
        "diagnostics", "auditpack", "checkpoint",
    }
    check("eight protocols registered", set(P.protocol_names()) == expected_protocols)
    r = await protocols_tool.run_protocol({"name": "nope", "password": "x"})
    check("unknown protocol lists options", "goodnight" in r and "no protocol" in r.lower(), r)
    r = await protocols_tool.run_protocol({"name": "ragnarok", "password": ""})
    check("missing password refuses", "password" in r.lower(), r)
    r = await protocols_tool.run_protocol({"name": "goodnight", "password": "WRONG"})
    check("wrong password does NOT run", "incorrect" in r.lower() and "not run" in r.lower(), r)

    # Stub the launcher so the CORRECT-password path is provable without killing anything.
    launched = {}

    class _FakePopen:
        def __init__(self, argv, **kw):
            launched["argv"] = argv

    real_popen = P.subprocess.Popen
    P.subprocess.Popen = _FakePopen  # type: ignore[assignment]
    # goodnight acts on the OWNER'S MACHINE, so with a laptop connected it travels over PC_LINK
    # instead of launching a script on whatever host the brain happens to be running on (on the VPS
    # that was a silent no-op). A brain-side protocol still proves the local launch path.
    from jarvis.brain import pc_link

    forwarded: list = []

    class _FakeLink:
        active = True

        async def forward(self, op, args):
            forwarded.append((op, args))
            return "ok"

    real_link = pc_link.PC_LINK
    pc_link.PC_LINK = _FakeLink()
    try:
        r = await protocols_tool.run_protocol(
            {"name": "goodnight", "password": settings.protocol_goodnight_password}
        )
        check("correct password sends goodnight to the laptop, not the brain host",
              len(forwarded) == 1 and "argv" not in launched, f"{forwarded} {launched}")
        check("correct password speaks a confirmation", "sir" in r.lower(), r)

        launched.clear()
        r2 = await protocols_tool.run_protocol(
            {"name": "backup", "password": settings.protocol_backup_password}
        )
        ok = "argv" in launched and str(launched["argv"][1]).endswith("backup.py")
        check("a brain-side protocol still launches its script (stubbed)", ok,
              str(launched.get("argv")))
    finally:
        pc_link.PC_LINK = real_link
        P.subprocess.Popen = real_popen  # type: ignore[assignment]

    print("\n[4] scheduler & reminders (temp SQLite)")
    # ignore_cleanup_errors: on Windows the SQLite jobstore file can stay briefly locked
    # after shutdown; the assertions below are what matter, not the temp-dir teardown.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        settings.scheduler_db_path = str(Path(d) / "jobs.sqlite")
        from jarvis.brain.scheduler import SCHEDULER
        from jarvis.brain.tools import notify, reminders

        # Keep this block hermetic: no real ntfy POST to the phone. (Phase 4b's ntfy path gets
        # its own monkeypatched check below.)
        real_topic = settings.ntfy_topic
        settings.ntfy_topic = ""
        try:
            SCHEDULER.start()  # running loop (asyncio.run) is active here
            r = await reminders.set_reminder({"message": "test ping", "in_minutes": 120})
            check("set_reminder schedules", "remind you" in r, r)
            r = await reminders.list_reminders({})
            check("list_reminders shows it", "test ping" in r, r)
            jid = next((j[0] for j in SCHEDULER.list_reminders()), "")
            r = await reminders.cancel_reminder({"id": jid[:8]})
            check("cancel_reminder removes it", "cancelled" in r.lower(), r)
            check("reminder list now empty", SCHEDULER.list_reminders() == [])

            print("\n[5] Phase 4b — ntfy server-side scheduled delivery (true 24/7, no real POST)")
            import time as _time

            # Window logic: in-window one-shot eligible; too-soon and too-far rejected; no-topic rejected.
            settings.ntfy_topic = "test-topic"
            check("ntfy schedules a one-shot in-window", notify.ntfy_can_schedule(_time.time() + 3600))
            check("ntfy rejects too-soon (<10s)", not notify.ntfy_can_schedule(_time.time() + 2))
            check("ntfy rejects too-far (>3d)", not notify.ntfy_can_schedule(_time.time() + 4 * 86400))
            settings.ntfy_topic = ""
            check("ntfy rejects when no topic set", not notify.ntfy_can_schedule(_time.time() + 3600))

            # Capture the push call instead of hitting the network; verify one-shot hands the
            # phone delivery to ntfy WITH an `at`, and the in-process job goes push_phone=False.
            settings.ntfy_topic = "test-topic"
            captured: dict = {}

            async def _fake_push(message, title="Jarvis", at=None):
                captured["message"], captured["at"] = message, at
                return True

            real_push = notify.push
            notify.push = _fake_push  # reminders.py imports push lazily, so this is seen
            try:
                r = await reminders.set_reminder({"message": "drink water", "in_minutes": 30})
                check("one-shot reminder is queued to ntfy (has `at`)", captured.get("at") is not None, captured)
                check("one-shot reply mentions PC-off delivery", "even if this PC is off" in r, r)
                jid2 = next((j[0] for j in SCHEDULER.list_reminders() if "drink water" in (j[1] or "")), "")
                job2 = SCHEDULER._sched.get_job(jid2)
                check("in-process job set push_phone=False (ntfy owns the push)", job2.args[1] is False, job2.args)

                # A DAILY (recurring) reminder is NOT ntfy-eligible -> push_phone stays True.
                captured.clear()
                r = await reminders.set_reminder({"message": "morning brief", "daily": "08:00"})
                check("daily reminder NOT handed to ntfy", captured.get("at") is None, captured)
                jid3 = next((j[0] for j in SCHEDULER.list_reminders() if "morning brief" in (j[1] or "")), "")
                job3 = SCHEDULER._sched.get_job(jid3)
                check("daily job keeps push_phone=True (live edge / VPS ticker)", job3.args[1] is True, job3.args)
            finally:
                notify.push = real_push
        finally:
            settings.ntfy_topic = real_topic
            if SCHEDULER._sched and SCHEDULER._sched.running:
                SCHEDULER._sched.shutdown(wait=False)
                SCHEDULER._sched = None

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
