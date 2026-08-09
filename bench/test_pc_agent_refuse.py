"""PC-agent refuse-list — catastrophic ops die at the elevated executor, everything else passes."""

from __future__ import annotations

from afon.edge.pc_agent import _refused


def test_refuse_list() -> None:
    assert _refused("run_powershell", {"command": "Format-Volume -DriveLetter C"})
    assert _refused("run_powershell", {"command": "rd /s /q C:\\"})
    assert _refused("run_powershell", {"command": "vssadmin delete shadows /all"})
    assert _refused("run_powershell", {"command": "reg delete HKLM\\SYSTEM /f"})
    # Normal ops must NOT be refused.
    assert _refused("run_powershell", {"command": "Get-Process | Select -First 3"}) is None
    assert _refused("file_op", {"action": "create", "path": "C:/tmp/x.txt"}) is None
    assert _refused("open_app", {"name": "spotify"}) is None


# The registry runs bench files as SCRIPTS. Without this block the module was imported, the test
# function was never called, and the runner recorded exit 0 — a green tick for zero assertions
# (TODO J6.5, where this was logged as "passes silently"; it was worse than silent).
if __name__ == "__main__":
    test_refuse_list()
    print("=== 7/7 checks passed ===")
