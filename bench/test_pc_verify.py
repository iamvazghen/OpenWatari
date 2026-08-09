"""C5 hermetic test — PC-agent see→act→VERIFY loop (no network, temp files only).

Asserts that after a PC op runs, the executor confirms the effect actually landed (file exists/gone)
and flags a mismatch when it didn't — the "verify" step that turns blind execution into see→act→verify.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import afon.edge.pc_agent as pc  # noqa: E402
from afon.edge.pc_agent import _run_op, _verify_effect  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


tmp = Path(tempfile.mkdtemp())
f = tmp / "made.txt"

# 1) pure verify: reflects real filesystem state
f.write_text("hi")
check(_verify_effect("file_op", {"action": "create_file", "path": str(f)}) == "verified — it exists now",
      "create verify: existing file confirmed")
f.unlink()
check(_verify_effect("file_op", {"action": "create_file", "path": str(f)}).startswith("WARNING"),
      "create verify: missing file flagged")
check(_verify_effect("file_op", {"action": "delete_file", "path": str(f)}) == "verified — it's gone",
      "delete verify: absent file confirmed")
check(_verify_effect("open_url", {"url": "x"}) is None, "non-verifiable op -> no note")

# 2) see→act→verify through _run_op: the handler ACTS, then _run_op VERIFIES the effect
order: list[str] = []


async def fake_create(args):
    order.append("act")
    Path(args["path"]).write_text("made")
    return "Created the file, sir."


async def fake_create_noop(args):
    order.append("act")   # claims success but does nothing -> verify must catch it
    return "Created the file, sir."


async def run():
    pc.LOCAL_HANDLERS["file_op"] = fake_create
    ok, out = await _run_op("file_op", {"action": "create_file", "path": str(f)})
    check(ok and "verified — it exists now" in out, f"act then verify: success confirmed (got {out!r})")
    check(order == ["act"], "handler (act) ran before the verify step")

    f.unlink()
    pc.LOCAL_HANDLERS["file_op"] = fake_create_noop
    ok2, out2 = await _run_op("file_op", {"action": "create_file", "path": str(f)})
    check(ok2 and "WARNING" in out2, f"a handler that lied about creating is caught by verify (got {out2!r})")


asyncio.run(run())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
