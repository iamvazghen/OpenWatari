"""A slow first handshake must not demote the edge to the LOCAL brain for the whole session.

Production bug (2026-07-28): build_brain() gave RemoteBrain.start() a 3s gate, and on miss called
rb.stop() and built an in-process JarvisBrain instead. A cold edge start contends with model
loading, so that gate was missed on EVERY restart — the laptop then answered from its own brain
(weaker LLM chain, separate memory) until the next restart, even though the VPS was healthy a
second later. RemoteBrain already routes PER TURN over a supervised link with a warm local standby,
so the fix is simply to keep it.

    uv run python bench/test_brain_mode_selection.py
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


class _FakeRemote:
    """Stands in for RemoteBrain: records whether it was started and abandoned."""

    instances: list["_FakeRemote"] = []

    def __init__(self, *a, **kw) -> None:
        self.started = False
        self.stopped = False
        self.connect_ok = _FakeRemote.connect_ok
        _FakeRemote.instances.append(self)

    async def start(self, connect_timeout_s: float = 3.0) -> bool:
        self.started = True
        return self.connect_ok

    async def stop(self) -> None:
        self.stopped = True


class _FakeLocal:
    def __init__(self, *a, **kw) -> None:
        pass

    async def warmup(self) -> None:
        return None


async def main() -> None:
    from jarvis.config import settings
    import jarvis.edge.assistant as A
    import jarvis.edge.remote_brain as RB

    orig_remote, orig_local = RB.RemoteBrain, A.JarvisBrain
    orig_mode = settings.brain_mode
    RB.RemoteBrain, A.JarvisBrain = _FakeRemote, _FakeLocal
    try:
        # 1) mode=remote, VPS answers in time -> remote, obviously.
        _FakeRemote.instances.clear(); _FakeRemote.connect_ok = True
        settings.brain_mode = "remote"
        brain = await A.build_brain()
        check("link up -> RemoteBrain", isinstance(brain, _FakeRemote))
        check("link up -> not stopped", not brain.stopped)

        # 2) THE REGRESSION: mode=remote, first handshake misses the gate. The edge must KEEP the
        #    supervised remote link (which answers on its warm standby), not swap in a local brain.
        _FakeRemote.instances.clear(); _FakeRemote.connect_ok = False
        settings.brain_mode = "remote"
        brain = await A.build_brain()
        check("slow handshake -> STILL RemoteBrain (not demoted for the session)",
              isinstance(brain, _FakeRemote), type(brain).__name__)
        check("slow handshake -> link NOT stopped (can promote later)",
              isinstance(brain, _FakeRemote) and not brain.stopped)
        check("slow handshake -> no local brain built",
              not isinstance(brain, _FakeLocal))

        # 3) mode=auto is opportunistic by design: it may fall through to the local brain.
        _FakeRemote.instances.clear(); _FakeRemote.connect_ok = False
        settings.brain_mode = "auto"
        brain = await A.build_brain()
        check("auto + no VPS -> local brain", isinstance(brain, _FakeLocal), type(brain).__name__)
        check("auto + no VPS -> remote link released",
              _FakeRemote.instances and _FakeRemote.instances[0].stopped)
    finally:
        RB.RemoteBrain, A.JarvisBrain = orig_remote, orig_local
        settings.brain_mode = orig_mode

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
