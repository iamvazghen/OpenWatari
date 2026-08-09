"""Phase 9b — L4 hot-cache (in-process TTL + graceful Redis), offline + hermetic.

Verifies: a value is cached and returned without re-running the factory; TTL expiry; LRU eviction;
blank results aren't cached; the cache fails open (a factory error isn't masked); and the backend
reports 'memory' when no Redis is configured. No network, no Redis needed.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> None:
    from afon.brain.cache import Cache, _InProcessTTL

    print("[1] cached() returns the factory value, then serves it without re-running")
    cache = Cache(redis_url=None)
    calls = {"n": 0}

    async def factory() -> str:
        calls["n"] += 1
        return "BTC is 78,199 dollars"

    async def scenario1() -> tuple[str, str]:
        a = await cache.cached("crypto", "btc", ttl=60, factory=factory)
        b = await cache.cached("crypto", "btc", ttl=60, factory=factory)
        return a, b

    a, b = asyncio.run(scenario1())
    check("first call runs the factory", a == "BTC is 78,199 dollars")
    check("second call is a cache hit (same value)", b == a)
    check("factory ran exactly once", calls["n"] == 1, f"ran {calls['n']}x")

    print("\n[2] backend is 'memory' with no Redis configured")
    check("backend reports memory", cache.backend == "memory", cache.backend)

    print("\n[3] TTL expiry forces a refresh")
    tc = _InProcessTTL()
    tc.set("k", "v", ttl=1)
    check("value present before expiry", tc.get("k") == "v")
    # Simulate expiry by rewriting the entry's deadline into the past.
    tc._data["k"] = (time.time() - 1, "v")
    check("value gone after expiry", tc.get("k") is None)

    print("\n[4] LRU eviction past max_items")
    small = _InProcessTTL(max_items=3)
    for i in range(5):
        small.set(f"k{i}", str(i), ttl=60)
    present = [small.get(f"k{i}") for i in range(5)]
    check("only the newest 3 survive", present[:2] == [None, None] and present[2:] == ["2", "3", "4"],
          str(present))

    print("\n[5] blank factory results are not cached")
    blanks = {"n": 0}

    async def blank_factory() -> str:
        blanks["n"] += 1
        return ""

    async def scenario5() -> None:
        await cache.cached("empty", "x", ttl=60, factory=blank_factory)
        await cache.cached("empty", "x", ttl=60, factory=blank_factory)

    asyncio.run(scenario5())
    check("empty result re-runs (not pinned)", blanks["n"] == 2, f"ran {blanks['n']}x")

    print("\n[6] cache fails open — a factory error is surfaced, not swallowed")

    async def boom() -> str:
        raise RuntimeError("provider down")

    async def scenario6() -> bool:
        try:
            await cache.cached("err", "y", ttl=60, factory=boom)
            return False
        except RuntimeError:
            return True

    check("factory error propagates (cache never hides failure)", asyncio.run(scenario6()))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
