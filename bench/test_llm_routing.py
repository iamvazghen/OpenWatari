"""LLM routing + latency-failover tests (offline, deterministic).

Covers the 2026-06-17 latency work:
  * per-model provider routing — a `groq:` chain entry hits the Groq client; unprefixed -> default.
  * optional fast tier — llm_fast_model is prepended to the chain.
  * first-token deadline — a model that doesn't deliver a first token in time is failed over fast
    (not after the full request timeout).

Run: uv run python bench/test_llm_routing.py
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from afon.brain.llm import LLMClient
from afon.config import settings

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  XX  {name}")


def test_resolve() -> None:
    llm = LLMClient()
    c_default, m = llm._resolve("llama-3.3-70b-versatile")
    check("unprefixed entry -> default client, name unchanged",
          c_default is llm._default and m == "llama-3.3-70b-versatile")
    c_groq, mg = llm._resolve("groq:llama-3.3-70b-versatile")
    check("groq: entry -> separate client, prefix stripped",
          c_groq is not llm._default and mg == "llama-3.3-70b-versatile")
    c_groq2, _ = llm._resolve("groq:other")
    check("groq client is cached/reused", c_groq2 is c_groq)
    c_oll, mo = llm._resolve("ollama:llama3.2")
    check("ollama: entry -> separate local client, prefix stripped",
          c_oll is not llm._default and c_oll is not c_groq and mo == "llama3.2")
    c_oll2, _ = llm._resolve("ollama:other")
    check("ollama client is cached/reused", c_oll2 is c_oll)


def test_fast_tier() -> None:
    old = settings.llm_fast_model
    try:
        settings.llm_fast_model = "llama-3.1-8b-instant"
        chain = settings.llm_chain
        check("fast model is prepended first", chain[0] == "llama-3.1-8b-instant")
        check("fast model not duplicated", chain.count("llama-3.1-8b-instant") == 1)
        settings.llm_fast_model = None
        check("no fast model -> chain starts at primary", settings.llm_chain[0] == settings.llm_primary_model)
    finally:
        settings.llm_fast_model = old


async def test_first_token_deadline() -> None:
    # A fake stream that never delivers a token within the deadline -> must fail over fast.
    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(5)   # would block 5s; the deadline must cut it off first
            raise StopAsyncIteration

    class _FakeCompletions:
        async def create(self, **_kw):
            return _FakeStream()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    llm = LLMClient()
    llm._default = _FakeClient()           # type: ignore[assignment]
    llm._clients = {}
    llm._chain = ["m1", "m2"]
    llm._first_token_timeout = 0.1

    t0 = time.perf_counter()
    raised = False
    try:
        async for _ in llm.stream_with_tools([{"role": "user", "content": "hi"}]):
            pass
    except RuntimeError:
        raised = True
    dt = time.perf_counter() - t0
    check("no-first-token streams fail over (RuntimeError after all models)", raised)
    check("failover is FAST (deadline, not the 5s block)", dt < 2.0)


class _CountingClient:
    """Stand-in OpenAI client that records every chat.completions.create call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            async def create(self, **kw):
                outer.calls.append(kw)

                class _R:
                    choices = []

                return _R()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


async def test_warmup_primes_every_distinct_provider() -> None:
    llm = LLMClient()
    default = _CountingClient()
    groq = _CountingClient()
    llm._default = default            # type: ignore[assignment]
    llm._clients = {"groq": groq}     # pre-seed so _resolve reuses, never constructs a real client
    # Two distinct providers across three entries (the second groq entry must NOT re-ping).
    llm._chain = ["groq:fast-model", "freellm-model", "groq:other-model"]

    await llm.warmup()

    check("warmup pinged the default provider exactly once", len(default.calls) == 1)
    check("warmup pinged the groq provider exactly once (deduped)", len(groq.calls) == 1)
    check("warmup ping is a 1-token probe (cheap)", default.calls[0].get("max_tokens") == 1)
    check("warmup primed the groq entry's model name", groq.calls[0].get("model") == "fast-model")
    check("warmup primed the unprefixed entry's model name",
          default.calls[0].get("model") == "freellm-model")


async def test_warmup_never_raises_when_a_provider_is_down() -> None:
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**_kw):
                    raise RuntimeError("provider down")

    llm = LLMClient()
    llm._default = _Boom()            # type: ignore[assignment]
    llm._clients = {}
    llm._chain = ["only-model"]
    ok = True
    try:
        await llm.warmup()            # best-effort: a dead provider must never block startup
    except Exception:  # noqa: BLE001
        ok = False
    check("warmup swallows a provider error (never blocks startup)", ok)


async def test_health_cooldown_skips_recent_failure() -> None:
    class _ModelAwareClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            outer = self

            class _Completions:
                async def create(self, **kw):
                    model = kw["model"]
                    outer.calls.append(model)
                    if model == "m1":
                        return SimpleNamespace(choices=[])
                    msg = SimpleNamespace(content="ok", tool_calls=None)
                    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    llm = LLMClient()
    fake = _ModelAwareClient()
    llm._default = fake             # type: ignore[assignment]
    llm._clients = {}
    llm._chain = ["m1", "m2"]
    llm._cooldown = 60.0
    llm._unhealthy_until = {}

    msg = await llm.complete([{"role": "user", "content": "hi"}])
    check("first call fails over to m2", msg.content == "ok" and fake.calls == ["m1", "m2"])
    check("route telemetry records fallback", llm.last_route.get("answered_by") == "m2")
    check("route telemetry counts one failed model", llm.last_route.get("failed_over_count") == 1)

    fake.calls.clear()
    await llm.complete([{"role": "user", "content": "hi again"}])
    check("recently failed m1 is skipped on next turn", fake.calls == ["m2"])


def main() -> None:
    test_resolve()
    test_fast_tier()
    asyncio.run(test_first_token_deadline())
    asyncio.run(test_warmup_primes_every_distinct_provider())
    asyncio.run(test_warmup_never_raises_when_a_provider_is_down())
    asyncio.run(test_health_cooldown_skips_recent_failure())
    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
