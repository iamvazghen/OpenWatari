"""The park switch, whose failure mode is that Afon comes back on his own.

Taking Afon out of production on 2026-08-13 exposed that neither launcher could express it: the
laptop's scheduled tasks repeat every 5 minutes and need elevation to disable, and the VPS ticker is
a system unit with Restart=always under a user with no passwordless sudo. `shared/maintenance.py` is
therefore the only place "stay stopped" is expressible — which makes it exactly the kind of switch
that must not rot quietly. If this file goes green while the guard is gone, the symptom is the
assistant answering after being told to stop, which is how the whole problem was noticed.

Three things are checked, and the third is the one that decays:

  * the predicate: absent lock -> run, present lock -> exit 0 with the reason on stderr;
  * exit code 0, not 1 — a parked process is obeying, and a launcher that logs failures would
    otherwise fill the journal with alarms about a state somebody chose;
  * every entry point still CALLS it. A new entry point, or a refactor that drops the call, is the
    realistic way this stops working — nothing else in the suite would notice.

Hermetic: the lock path is monkeypatched to a temp dir, so it never touches the real ~/.afon.

    uv run python bench/test_maintenance_lock.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.shared import maintenance  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: entry point -> the role string it must park under. `deploy/vps/afon_ticker.py` inlines an
#: equivalent guard instead of importing the package (it ships standalone), so it is matched on the
#: lock path rather than the call.
ENTRY_POINTS = {
    "src/afon/edge/assistant.py": "edge",
    "src/afon/edge/pc_agent.py": "pc_agent",
    "src/afon/brain/server.py": "brain",
}

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    saved = maintenance.LOCK
    tmp = Path(tempfile.mkdtemp()) / "MAINTENANCE"
    try:
        maintenance.LOCK = tmp

        print("[1] no lock -> nothing happens")
        check(maintenance.parked() is None, "parked() is None with no lock file")
        maintenance.halt_if_parked("edge")   # must simply return
        check(True, "halt_if_parked() returns and the process continues")

        print("\n[2] lock present -> exit 0, with the reason")
        tmp.write_text("out of production for the TODO list", encoding="utf-8")
        check(maintenance.parked() == "out of production for the TODO list",
              "parked() reports the reason written in the file")
        try:
            maintenance.halt_if_parked("edge")
            check(False, "halt_if_parked() exits when parked", "it returned instead")
        except SystemExit as e:
            check(e.code == 0, "halt_if_parked() exits 0 (obeying, not failing)", f"code={e.code}")

        print("\n[3] an empty or unreadable lock still parks")
        # The reason is a courtesy; the file's EXISTENCE is the instruction. A lock that only works
        # when someone remembered to write prose in it is a lock that fails open.
        tmp.write_text("", encoding="utf-8")
        check(maintenance.parked() == "no reason recorded", "empty lock still parks")

        print("\n[4] every entry point calls it")
        for rel, role in ENTRY_POINTS.items():
            body = (ROOT / rel).read_text(encoding="utf-8")
            called = re.search(rf"halt_if_parked\(\s*[\"']{role}[\"']\s*\)", body)
            check(bool(called), f"{rel} parks as '{role}'", "no halt_if_parked call found")
        # The edge's call must sit ABOVE the pipecat import, or a parked laptop pays a ~20s audio
        # stack load on every one of the launcher's 5-minute relaunches.
        edge = (ROOT / "src/afon/edge/assistant.py").read_text(encoding="utf-8")
        check(edge.index("halt_if_parked") < edge.index("loading audio stack"),
              "the edge parks BEFORE loading the audio stack")
        ticker = (ROOT / "deploy/vps/afon_ticker.py").read_text(encoding="utf-8")
        check('".afon" / "MAINTENANCE"' in ticker.replace("'", '"'),
              "the standalone VPS ticker carries the same lock check")
        # ...and it must come after __future__, which is what broke the deployed copy the first time.
        check(ticker.index("from __future__") < ticker.index("MAINTENANCE"),
              "the ticker's guard sits after `from __future__` (a SyntaxError otherwise)")
    finally:
        maintenance.LOCK = saved

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
