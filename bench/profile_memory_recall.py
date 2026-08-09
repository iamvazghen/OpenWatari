"""Profile the memory recall paths — including whether they block the event loop.

TODO J3.3 records "seven memory stores, five with their own SQLite connection, no unified
recall" and flags the L5 semantic hop as UNMEASURED. This measures it.

What it checks:

  1. **The per-turn path** (`layers=("L1","L2")`) — what every single turn pays for auto-recall.
  2. **The tool path** (default layers, includes L5) — what an explicit `recall` costs.
  3. **Event-loop blocking.** `MemoryStore.recall()` is SYNCHRONOUS and `fused_recall()` is
     async and calls it directly, with no `to_thread` anywhere in memory.py or semantic.py.
     When L5 is active the embedder is a synchronous `httpx.post` to api.jina.ai with
     `timeout=10`. If that runs on the loop, one recall stalls EVERYTHING the brain is doing —
     other turns, the WebSocket, the scheduler — for up to ten seconds.

     A concurrent 10ms ticker runs alongside each recall. If the loop is healthy the ticker
     keeps its rhythm; if the call blocks, the ticker's largest gap is the stall.

    uv run python bench/profile_memory_recall.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.memory import STORE  # noqa: E402
from afon.config import settings  # noqa: E402

QUERY = "what do you know about my travel plans and flights"


async def _ticker(stop: asyncio.Event, gaps: list[float]) -> None:
    """Tick every 10ms and record the actual interval. A blocked loop shows up as one big gap."""
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0.01)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


async def _timed(label: str, coro_factory) -> tuple[float, float]:
    """Run a recall with a ticker beside it. Returns (elapsed, worst_loop_gap)."""
    gaps: list[float] = []
    stop = asyncio.Event()
    t = asyncio.create_task(_ticker(stop, gaps))
    await asyncio.sleep(0.05)          # let the ticker settle
    started = time.perf_counter()
    try:
        n = len(await coro_factory())
    except Exception as e:  # noqa: BLE001
        n = -1
        print(f"    ({label}: raised {type(e).__name__}: {e})")
    elapsed = time.perf_counter() - started
    stop.set()
    await t
    worst = max(gaps[1:], default=0.0)
    print(f"  {label:<34} {elapsed * 1000:8.1f} ms   hits={n:<3} "
          f"worst loop gap {worst * 1000:7.1f} ms")
    return elapsed, worst


async def main() -> int:
    print("memory recall profile")
    print(f"  semantic enabled : {getattr(settings, 'memory_semantic_enabled', 'n/a')}")
    print(f"  jina key present : {bool(getattr(settings, 'jina_api_key', None))}")
    print()

    # Warm any lazy imports/caches so the first measurement is not import time.
    await STORE.fused_recall(QUERY, limit=4, layers=("L1",))

    print("per-turn path (what EVERY turn pays):")
    turn_ms, turn_gap = await _timed(
        "L1+L2 (auto-recall)",
        lambda: STORE.fused_recall(QUERY, limit=4, layers=("L1", "L2")))

    print()
    print("tool path (explicit `recall`):")
    tool_ms, tool_gap = await _timed(
        "L1+L2+L3+L5+L5b (default)",
        lambda: STORE.fused_recall(QUERY, limit=8))
    # Again, warm. The L3 body cache lives in the PROCESS, so a fresh profile run always pays the
    # cold read — but the brain is long-lived, and this second number is the one it actually sees.
    warm_ms, warm_gap = await _timed(
        "L1+L2+L3+L5+L5b (warm cache)",
        lambda: STORE.fused_recall(QUERY, limit=8))
    l5_ms, l5_gap = await _timed(
        "L1+L5 only (isolates the embedder)",
        lambda: STORE.fused_recall(QUERY, limit=8, layers=("L1", "L5")))

    print()
    print("verdict:")
    # A healthy loop ticks at ~10ms. Anything past 100ms means the loop was held.
    # Two tiers, because "not instant" and "the brain is frozen" are different problems.
    # Before the to_thread fixes the worst gap here was 287 SECONDS on a cold vault.
    FREEZE, NOTICEABLE = 1.000, 0.100
    worst = max(turn_gap, tool_gap, warm_gap, l5_gap)
    if worst > FREEZE:
        print(f"  BLOCKING: the event loop stalled {worst * 1000:.0f} ms during a recall.")
        print("  While stalled the brain answers nothing else — other turns, the WebSocket and")
        print("  the scheduler are all frozen. recall() is sync and fused_recall() awaits it")
        print("  directly; the fix is asyncio.to_thread, not a faster embedder.")
    elif worst > NOTICEABLE:
        print(f"  loop stayed RESPONSIVE (worst gap {worst * 1000:.0f} ms) — no freeze, but the")
        print("  vault scan still holds the GIL in bursts while reading files. Acceptable; the")
        print("  cure is an index rather than a full rglob (J3.3a).")
    else:
        print(f"  loop stayed responsive (worst gap {worst * 1000:.0f} ms)")

    if turn_ms > 0.050:
        print(f"  NOTE: the per-turn path costs {turn_ms * 1000:.0f} ms on EVERY turn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
