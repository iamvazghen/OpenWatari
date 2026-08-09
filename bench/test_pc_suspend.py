"""pc_agent pause/continue: process_op can SUSPEND then RESUME an app without killing it — the action
the proactivity engine uses to pause/continue what the owner is doing on the laptop.

Schema + guard checks run everywhere; the live suspend/resume/kill round-trip runs only on Windows
(where NtSuspendProcess exists), against a throwaway hidden process.
"""
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os
os.environ["AFON_SYSTEM_TOOLS_ENABLED"] = "true"

from afon.brain.tools.system import SCHEMAS, _process_op_local  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# 1) schema advertises suspend/resume so the model + proactive engine can call them
proc = next(s for s in SCHEMAS if s["function"]["name"] == "process_op")
enum = proc["function"]["parameters"]["properties"]["action"]["enum"]
check("suspend" in enum and "resume" in enum, f"process_op advertises suspend/resume (got {enum})")


async def _run():
    # 2) missing name/pid -> a helpful guard, not a crash
    g = await _process_op_local({"action": "suspend"})
    check("name or pid" in g.lower(), f"suspend without a target asks for one (got {g!r})")

    # 3) live round-trip on Windows only
    if sys.platform == "win32":
        p = subprocess.Popen([sys.executable.replace("python.exe", "pythonw.exe"),
                              "-c", "import time; time.sleep(120)"])
        await asyncio.sleep(0.5)
        s = await _process_op_local({"action": "suspend", "pid": p.pid})
        check(s.startswith("Paused"), f"suspend pauses the process (got {s!r})")
        r = await _process_op_local({"action": "resume", "pid": p.pid})
        check(r.startswith("Resumed"), f"resume continues the process (got {r!r})")
        k = await _process_op_local({"action": "kill", "pid": p.pid})
        check("Killed" in k, f"kill cleans up (got {k!r})")
        await asyncio.sleep(0.3)
        check(p.poll() is not None, "process is actually gone after kill")
    else:
        print("  (skipping live suspend/resume — not win32)")


asyncio.run(_run())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
