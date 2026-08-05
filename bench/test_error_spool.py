"""H2.9 — errors logged while the brain link is DOWN still reach the brain (spool + replay).

Offline & hermetic: no sockets, no brain. A fake websocket stands in for the link.

The blind spot this closes: ``ship_error`` used to drop the entry when ``_ws is None``. That is
exactly the case that matters, because an error raised BY brain_client describes the very socket it
would have shipped over — 13 of 45 edge entries never reached the VPS journal, so ``diagnose`` could
not see brain-connection failures, which is precisely what the owner asks about when the link is bad.

    uv run python bench/test_error_spool.py
"""

from __future__ import annotations

import asyncio
import json
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


class FakeWS:
    """Records what was sent. ``fail_after`` makes it start raising, like a link dying mid-drain."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.sent: list[dict] = []
        self.fail_after = fail_after

    async def send(self, raw: str) -> None:
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionError("socket closed")
        self.sent.append(json.loads(raw))


def _client():
    from jarvis.edge.brain_client import BrainClient

    return BrainClient(session_id="s1", device_id="d1", url="ws://127.0.0.1:1/ws", token="")


async def main() -> None:
    from jarvis.edge.brain_client import _SPOOL_MAX

    print("[1] link DOWN — entries are spooled, not dropped")
    c = _client()
    c._ws = None
    for i in range(3):
        c.ship_error({"level": "ERROR", "msg": f"boom {i}", "ts": f"t{i}"})
    check("3 entries spooled while disconnected", len(c._spool) == 3, str(len(c._spool)))
    check("nothing lost", [e["msg"] for e in c._spool] == ["boom 0", "boom 1", "boom 2"])

    print("\n[2] the spool is BOUNDED (a long outage must not grow it forever)")
    c2 = _client()
    c2._ws = None
    for i in range(_SPOOL_MAX + 50):
        c2.ship_error({"msg": f"e{i}"})
    check(f"capped at {_SPOOL_MAX}", len(c2._spool) == _SPOOL_MAX, str(len(c2._spool)))
    # The NEWEST are kept: the entries that explain a failure sit at the end, not the start.
    check("keeps the newest, drops the oldest",
          c2._spool[-1]["msg"] == f"e{_SPOOL_MAX + 49}" and c2._spool[0]["msg"] == "e50",
          f"{c2._spool[0]['msg']}..{c2._spool[-1]['msg']}")

    print("\n[3] on reconnect the spool is REPLAYED, then emptied")
    ws = FakeWS()
    c._ws = ws
    await c._drain_spool()
    check("all 3 replayed to the brain", len(ws.sent) == 3, str(len(ws.sent)))
    check("sent as error reports", all(m.get("type") == "error" for m in ws.sent))
    check("original order preserved",
          [m["entry"]["msg"] for m in ws.sent] == ["boom 0", "boom 1", "boom 2"])
    check("spool emptied after a clean drain", not c._spool, str(len(c._spool)))

    print("\n[4] a replayed entry says so, and keeps its ORIGINAL timestamp")
    # Otherwise the brain journal reads as though the failure happened at reconnect time, and anyone
    # comparing entry order to arrival order is quietly misled.
    check("marked replayed", all(m["entry"].get("replayed") is True for m in ws.sent))
    check("original ts intact", [m["entry"]["ts"] for m in ws.sent] == ["t0", "t1", "t2"])

    print("\n[5] link dies MID-drain — the remainder is kept, not dropped")
    c3 = _client()
    c3._ws = None
    for i in range(5):
        c3.ship_error({"msg": f"x{i}"})
    c3._ws = FakeWS(fail_after=2)          # two land, then the socket goes
    await c3._drain_spool()
    check("the un-sent remainder is re-spooled", len(c3._spool) == 3, str(len(c3._spool)))
    check("and it is the RIGHT remainder (no gap, no dupes)",
          [e["msg"] for e in c3._spool] == ["x2", "x3", "x4"], str([e["msg"] for e in c3._spool]))

    print("\n[6] draining an empty spool is a no-op, and never raises")
    c4 = _client()
    c4._ws = FakeWS()
    await c4._drain_spool()
    check("nothing sent for an empty spool", not c4._ws.sent)

    print("\n[7] with the link UP, ship_error still sends immediately (no regression)")
    c5 = _client()
    c5._ws = FakeWS()
    c5.ship_error({"msg": "live"})
    await asyncio.sleep(0.05)              # it schedules a task rather than awaiting
    check("shipped straight through, not spooled", not c5._spool, str(len(c5._spool)))
    check("and it reached the fake socket", len(c5._ws.sent) == 1, str(len(c5._ws.sent)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
