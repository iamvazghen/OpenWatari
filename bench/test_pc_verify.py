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


# ---- 04.F4: a dead link is unreachable within one turn, never a success and never a long wait --
# The executor can verify what it did. What it could not do is notice it was never asked. A laptop
# that closes its lid leaves the websocket open for over a minute, so every device command was
# accepted, waited the full ninety-second result window, and only then failed — several turns after
# the owner asked, having said nothing in the turn where he did.
async def run_linkdown():
    import time as _t

    from afon.brain.pc_link import STALE_AFTER_S, PcLink

    class DeadWS:
        """A socket whose peer is gone: the send succeeds, the ping is never answered."""

        def __init__(self):
            self.sent = []

        async def send(self, data):
            self.sent.append(data)

        def ping(self):
            return asyncio.get_event_loop().create_future()   # never resolves

    class LiveWS(DeadWS):
        def ping(self):
            fut = asyncio.get_event_loop().create_future()
            fut.set_result(b"")
            return fut

    link = PcLink()
    check(link.active is False, "with nothing registered, the link is not active")
    raised = ""
    try:
        await link.forward("file_op", {})
    except Exception as e:
        raised = type(e).__name__
    check(raised == "ConnectionError", f"an unregistered link refuses immediately (got {raised!r})")

    dead = DeadWS()
    link.register(dead, "laptop")
    check(link.active is True, "a registered socket reads as active")
    started = _t.monotonic()
    raised = ""
    try:
        await link.forward("file_op", {}, timeout=90.0)
    except Exception as e:
        raised = type(e).__name__
    took = _t.monotonic() - started
    check(raised == "ConnectionError", f"a dead peer is reported as unreachable (got {raised!r})")
    check(took < 10.0, f"...within one turn, not the 90s result window (took {took:.1f}s)")
    check(not dead.sent, "and the command was never sent into the void")

    live = LiveWS()
    link.register(live, "laptop")
    check(await link.reachable() is True, "a live peer answers the ping")
    # A ping that comes back is proof of life, so the staleness clock resets on it.
    link._seen = _t.monotonic() - (STALE_AFTER_S + 60)
    check(link.stale is True, "a long-silent link reads as stale")
    await link.reachable()
    check(link.stale is False, "...and a successful ping clears that")

    # Health used to call `PC_LINK.active()` — a property, so it raised TypeError, was swallowed,
    # and the laptop was reported down with a made-up reason even while it was connected.
    from afon.brain import health

    from afon.brain import pc_link as pl

    real = pl.PC_LINK
    pl.PC_LINK = link
    try:
        ok, detail = await health._check_pc_link()
        check(ok is True, f"health reports a live laptop as healthy (got {detail!r})")
        check("TypeError" not in detail, "...without a swallowed error standing in for the answer")
        pl.PC_LINK = PcLink()
        pl.PC_LINK.register(DeadWS(), "laptop")
        ok2, detail2 = await health._check_pc_link()
        check(ok2 is False, "a registered-but-silent laptop is NOT reported as connected")
        check("not answering" in detail2, f"...and the reason says so (got {detail2!r})")
    finally:
        pl.PC_LINK = real


asyncio.run(run_linkdown())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
