"""H2.10 — a brain-side protocol's report reaches the OWNER, not just the brain's disk.

Offline & hermetic: a temp repo root, a stubbed Telegram sender, no protocols actually launched.

``diagnostics``, ``auditpack`` and ``checkpoint`` write into ``backups/`` on whichever host the brain
runs. That was fine while the brain WAS the laptop. Since it moved to the VPS the owner has been told
"diagnostics written" while having no way to read a word of it — the report may as well not exist.

    uv run python bench/test_protocol_reports.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
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


sent: list[dict] = []


async def _fake_send(args: dict) -> str:
    sent.append(args)
    return "Sent, sir."


async def main() -> None:
    import afon.brain.protocols as P
    import afon.brain.tools.telegram as TG

    TG.send_telegram = _fake_send            # _deliver_report imports it at call time

    print("[1] every brain-side protocol that writes a file is registered for delivery")
    reg = P._registry()
    writes_a_file = {"diagnostics", "auditpack", "checkpoint"}
    check("diagnostics/auditpack/checkpoint all covered", writes_a_file <= set(P._REPORTS),
          str(sorted(P._REPORTS)))
    # Routed protocols act on the laptop and produce nothing here — they must NOT be listed.
    routed = {n for n, p in reg.items() if p.get("pc_command")}
    check("no routed protocol is listed for delivery", not (routed & set(P._REPORTS)),
          str(sorted(routed & set(P._REPORTS))))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "backups").mkdir()
        P._REPO_ROOT = root

        print("\n[2] a fresh TEXT report is delivered, with its content inline")
        sent.clear()
        started = time.time()
        rpt = root / "backups" / "afon-diagnostics-20260801-120000.txt"
        rpt.write_text("disk 41% used\nbrain uptime 6d\nall services active", encoding="utf-8")
        await P._deliver_report("diagnostics", started)
        check("exactly one message sent", len(sent) == 1, str(len(sent)))
        check("the file itself is attached", sent and sent[0].get("file") == str(rpt),
              str(sent[0].get("file") if sent else None))
        check("content is IN the message (readable without downloading)",
              sent and "brain uptime 6d" in sent[0]["message"], sent[0]["message"][:80] if sent else "")
        check("names the protocol", sent and "diagnostics" in sent[0]["message"])

        print("\n[2b] a report whose mtime lands a hair BEFORE the run started is still fresh")
        # This is what made this file flaky: green standalone, red in the gate. A file written
        # strictly after a time.time() reading can carry an mtime just before it, because the
        # filesystem's timestamp granularity is coarser than the clock and rounds down — measured
        # at 292 of 3000 writes (~10%) on this machine. The old check called that stale, waited the
        # full 90s and delivered nothing. Forced here rather than left to a 1-in-10 chance.
        sent.clear()
        rpt2 = root / "backups" / "afon-diagnostics-20260801-120500.txt"
        rpt2.write_text("second run", encoding="utf-8")
        await P._deliver_report("diagnostics", rpt2.stat().st_mtime + 0.5)  # start 0.5s AFTER mtime
        check("a report 0.5s 'older' than the run start is still delivered", len(sent) == 1,
              str(len(sent)))

        print("\n[3] a STALE report is never passed off as this run's")
        # The guard that matters: without it, a run that wrote nothing delivers last week's file and
        # looks like it worked.
        sent.clear()
        old = root / "backups" / "afon-audit-20250101-000000.zip"
        old.write_bytes(b"PK\x03\x04old")
        os.utime(old, (time.time() - 86400, time.time() - 86400))   # a day old
        P._REPORT_WAIT_S = 3.0                                       # don't wait 90s to prove a negative
        await P._deliver_report("auditpack", time.time())
        check("nothing delivered when only a stale file exists", not sent, str(len(sent)))

        print("\n[4] a fresh ARCHIVE is delivered as a file, without inlining binary")
        sent.clear()
        fresh = root / "backups" / "afon-checkpoint-20260801-130000.zip"
        fresh.write_bytes(b"PK\x03\x04" + b"x" * 2048)
        await P._deliver_report("checkpoint", time.time() - 5)
        check("archive delivered", len(sent) == 1, str(len(sent)))
        check("sent as an attachment", sent and sent[0].get("file", "").endswith(".zip"))
        check("size reported, contents not inlined",
              sent and "KB" in sent[0]["message"] and "PK" not in sent[0]["message"],
              sent[0]["message"][:80] if sent else "")

        print("\n[5] nothing written at all -> quiet, and never raises")
        # Age the reports written above explicitly. Relying on wall-clock drift between the steps
        # would leave this check ~1s from the _MTIME_SLOP_S boundary — i.e. it would be testing the
        # tolerance by accident, which is how the flake this file just fixed got in.
        sent.clear()
        day_ago = time.time() - 86400
        for stale_txt in (root / "backups").glob("afon-diagnostics-*.txt"):
            os.utime(stale_txt, (day_ago, day_ago))
        await P._deliver_report("diagnostics", time.time())    # no new .txt will appear
        check("no message when the script produced nothing", not sent, str(len(sent)))

        print("\n[6] an unknown/unlisted protocol is a no-op")
        sent.clear()
        await P._deliver_report("ping", time.time())
        check("ping writes no report, so nothing is sent", not sent, str(len(sent)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
