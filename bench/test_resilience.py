"""P1 #4 — resilience under fault injection.

Unattended, things break: a model rate-limits, Redis dies, the scheduler DB is unwritable, a tool
throws. None of these may wedge or crash the brain — each must yield a *speakable* outcome and a
still-running system. This injects each fault and asserts graceful behaviour.

Hermetic: no real network. Fake LLM/responses; a dead Redis port; an unwritable jobstore path.

    uv run python bench/test_resilience.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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


async def test_llm_failover() -> None:
    print("[1] LLM primary fails -> the chain answers via a fallback (no crash)")
    from afon.brain.llm import LLMClient

    llm = LLMClient()
    # Two models in the chain; the first returns a choiceless 200 (proxy error), the second answers.
    llm._chain = ["primary-broken", "fallback-good"]

    async def fake_create(**kwargs):
        if kwargs["model"] == "primary-broken":
            return SimpleNamespace(choices=[], error="simulated outage")  # -> _EmptyResponse failover
        msg = SimpleNamespace(content="Yerevan, sir.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    llm._default.chat.completions.create = fake_create
    msg = await llm.complete([{"role": "user", "content": "capital of Armenia?"}])
    check("a fallback model answered after the primary failed", msg.content == "Yerevan, sir.")


async def test_cache_without_redis() -> None:
    print("\n[2] Redis unreachable -> cache falls back to in-process, the lookup still works")
    from afon.brain.cache import Cache

    cache = Cache(redis_url="redis://127.0.0.1:6399/0")  # nothing listening on 6399

    async def factory() -> str:
        return "fresh-value"

    val = await cache.cached("ns", "k", ttl=60, factory=factory)
    check("cached() returns the factory value despite dead Redis", val == "fresh-value")
    check("backend degraded to in-process ('memory')", cache.backend == "memory", cache.backend)
    again = await cache.cached("ns", "k", ttl=60, factory=lambda: _should_not_run())
    check("second lookup is an in-process hit", again == "fresh-value")


def _should_not_run():  # pragma: no cover - only called if the cache failed to store
    raise AssertionError("factory should not run on a cache hit")


async def test_scheduler_failure() -> None:
    print("\n[3] Scheduler backend fails -> set_reminder degrades to a spoken error, brain survives")
    import afon.brain.tools.reminders as rem

    class _BrokenScheduler:
        def add_reminder(self, *a, **kw):
            raise OSError("jobstore unwritable")

    saved = rem.SCHEDULER
    rem.SCHEDULER = _BrokenScheduler()  # simulate the jobstore/scheduler being unavailable
    try:
        out = await rem.set_reminder({"message": "drink water", "in_minutes": 5})
        check("set_reminder returned a spoken string (no exception)", isinstance(out, str) and bool(out))
        check("it reads as a graceful failure, not a stack trace",
              any(w in out.lower() for w in ("trouble", "error", "couldn't", "problem")), out)
    except Exception as e:  # noqa: BLE001
        check("set_reminder did not raise", False, f"{type(e).__name__}: {e}")
    finally:
        rem.SCHEDULER = saved


async def test_tool_raises_in_turn() -> None:
    print("\n[4] A tool raises mid-turn -> the turn returns a safe reply (no crash)")
    from afon.brain.agent import AfonAgent

    agent = AfonAgent()

    async def boom(_args: dict) -> str:
        raise RuntimeError("kaboom")

    agent._registry["boom"] = boom

    calls = {"n": 0}

    async def fake_complete(messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        calls["n"] += 1
        if calls["n"] == 1:
            tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="boom", arguments="{}"))
            return SimpleNamespace(content="", tool_calls=[tc])
        # Second pass (after the tool-error result is fed back): a normal spoken reply.
        return SimpleNamespace(content="That one failed, sir, but I'm still here.", tool_calls=None)

    agent._llm = SimpleNamespace(complete=fake_complete)
    try:
        reply = await agent.respond("do the boom thing")
        check("respond() returned a spoken reply instead of raising", bool(reply), repr(reply))
        check("history stayed intact (user + assistant)",
              agent._history[-2]["role"] == "user" and agent._history[-1]["role"] == "assistant")
    except Exception as e:  # noqa: BLE001
        check("respond() did not propagate the tool exception", False, f"{type(e).__name__}: {e}")


async def main() -> None:
    await test_llm_failover()
    await test_cache_without_redis()
    await test_scheduler_failure()
    await test_tool_raises_in_turn()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
