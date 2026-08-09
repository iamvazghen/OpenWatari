"""Failover latency bench (MASTER 5.6) — a dead primary must cost almost nothing.

The failover chain's promise is that a broken/rate-limited primary doesn't stall a voice turn: the
router catches the failure and moves to the next model. This measures the OVERHEAD that failover
adds — not real network time (stubs stand in for the providers), but the router's own cost of
catching the error, marking the model unhealthy, and dispatching the fallback.

Two regimes, both asserted:
  * cold failover — primary raises on the FIRST call: overhead = catch + retry the fallback.
  * warm failover — after one failure the primary is in COOLDOWN, so ``_candidate_chain`` SKIPS it
    entirely and goes straight to the fallback (near-zero overhead).

Both P50s must clear a generous 2000ms budget (typical is <1ms in-process; the budget is loose so
only a real regression — the 15.7s proxy incident scale — trips it, never dev-box scheduling noise).
The RELATIVE guard (warm skip <= cold retry) is the real assertion. Hermetic: no providers, no network.

    uv run python bench/test_failover_latency.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openai import APIError  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [type("C", (), {"message": _Msg(content)})()]


def _client(behavior):
    """Build a stub OpenAI-ish client whose chat.completions.create runs `behavior(**kw)`."""
    class _Completions:
        async def create(self, **kw):
            return await behavior(**kw)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


async def _dead(**_kw):
    # A rate-limited/broken primary. Instant raise (no network) — we measure ROUTER overhead only.
    raise APIError("primary is down", request=None, body=None)  # type: ignore[arg-type]


async def _alive(**_kw):
    return _Resp("Pong, sir.")


async def main() -> None:
    from afon.brain.llm import LLMClient

    print("[1] cold failover — primary raises on the first call")
    lat_ms: list[float] = []
    for _ in range(21):
        llm = LLMClient()
        llm._cooldown = 45.0
        llm._unhealthy_until = {}
        llm._chain = ["primary", "fallback"]
        llm._clients = {}
        # Route "primary" -> dead stub, "fallback" -> alive stub. Patch _resolve directly.
        dead, alive = _client(_dead), _client(_alive)
        llm._resolve = lambda entry, d=dead, a=alive: (d if entry == "primary" else a, entry)  # type: ignore[assignment]
        t0 = time.perf_counter()
        msg = await llm.complete([{"role": "user", "content": "hi"}])
        lat_ms.append((time.perf_counter() - t0) * 1000)
        assert msg.content == "Pong, sir."
    p50 = statistics.median(lat_ms)
    p95 = sorted(lat_ms)[int(0.95 * (len(lat_ms) - 1))]
    print(f"    cold failover P50={p50:.2f}ms  P95={p95:.2f}ms  (n={len(lat_ms)})")
    check("cold failover answered via the fallback", True)
    check(f"cold failover P50 < 2000ms (got {p50:.1f}ms)", p50 < 2000, f"{p50:.1f}ms")

    print("\n[2] warm failover — primary in cooldown is SKIPPED, not retried")
    llm = LLMClient()
    llm._cooldown = 45.0
    llm._unhealthy_until = {}
    llm._chain = ["primary", "fallback"]
    llm._clients = {}
    calls = {"primary": 0, "fallback": 0}

    async def _dead_counted(**_kw):
        calls["primary"] += 1
        raise APIError("down", request=None, body=None)  # type: ignore[arg-type]

    async def _alive_counted(**_kw):
        calls["fallback"] += 1
        return _Resp("Pong.")

    dead, alive = _client(_dead_counted), _client(_alive_counted)
    llm._resolve = lambda entry, d=dead, a=alive: (d if entry == "primary" else a, entry)  # type: ignore[assignment]

    await llm.complete([{"role": "user", "content": "1"}])   # first: primary fails -> marked unhealthy
    first_primary_calls = calls["primary"]
    warm: list[float] = []
    for _ in range(21):
        t0 = time.perf_counter()
        await llm.complete([{"role": "user", "content": "2"}])
        warm.append((time.perf_counter() - t0) * 1000)
    wp50 = statistics.median(warm)
    print(f"    warm failover P50={wp50:.2f}ms  (primary called {calls['primary']}x total)")
    check("primary was tried once, then skipped while cooling down",
          calls["primary"] == first_primary_calls, f"primary calls={calls['primary']}")
    check(f"warm failover P50 < 2000ms (got {wp50:.1f}ms)", wp50 < 2000, f"{wp50:.1f}ms")
    check("warm failover is faster than cold (skip beats catch+retry)", wp50 <= p50 + 1.0,
          f"warm {wp50:.2f} vs cold {p50:.2f}")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
