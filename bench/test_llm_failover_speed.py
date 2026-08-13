"""Failover must be FAST on every path, and a merely-slow model must not be benched like a broken one.

Two defects this locks down, both found by measuring the live chain on 2026-08-11:

1. **`stream()` had no first-token deadline at all** — a bare `async for chunk in stream`. That is
   the PURE-CHAT path, the most common conversational turn, so a primary that accepted the
   connection and then went quiet was waited on until the 60s request timeout while a fallback with
   a measured 0.18s TTFT sat idle. The deadline existed only on `stream_with_tools`, the rarer path.

2. **A slow first token benched the model for the full 45s unhealthy cooldown**, identically to a
   4xx or a dead key. Measured primary TTFT was 0.61-0.92s for 9 of 10 turns with one 3.69s
   outlier, so a tight deadline trips on ~1 turn in 10 — all of them healthy. At 45s each that
   quietly migrates a tenth of the day's traffic onto the quota-capped fallback (groq, 100k
   tokens/day, which is why it cannot be the primary).

Hermetic: every client is a stub. No network, no keys, no sleeping for real deadlines — the
deadline is set to a few milliseconds so the test is fast and deterministic.

    uv run python bench/test_llm_failover_speed.py
"""

from __future__ import annotations

import asyncio
import sys
import time
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


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)
        self.finish_reason = None


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _HungStream:
    """Connects, streams, but never produces a first token."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(60)
        raise StopAsyncIteration


class _GoodStream:
    def __init__(self, words=("ready",)):
        self._left = list(words)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._left:
            raise StopAsyncIteration
        return _Chunk(self._left.pop(0))


def _client(stream_factory):
    class _C:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    return stream_factory()
    return _C()


async def main() -> None:
    from afon.brain import llm as L
    from afon.config import settings

    settings.llm_first_token_timeout_seconds = 0.05   # keep the test fast; the logic is what matters

    def wire(client: L.LLMClient) -> None:
        """primary hangs, first fallback answers."""
        def resolve(model):
            if model == client._chain[0]:
                return _client(_HungStream), "hung-primary"
            return _client(_GoodStream), "good-fallback"
        client._resolve = resolve

    print("[1] stream() — the PURE-CHAT path — enforces the first-token deadline")
    c = L.LLMClient()
    wire(c)
    t = time.perf_counter()
    got = ""
    async for piece in c.stream([{"role": "user", "content": "hi"}]):
        got += piece or ""
    elapsed = time.perf_counter() - t
    check("a hung primary does not stall pure chat", "ready" in got, repr(got))
    check(f"...and hands off within ~the deadline ({elapsed * 1000:.0f}ms)", elapsed < 1.0,
          f"{elapsed:.2f}s — no deadline on this path?")

    print("\n[2] a SLOW first token is benched briefly, not like a broken model")
    now = asyncio.get_running_loop().time()
    benched = {m: u - now for m, u in c._unhealthy_until.items()}
    check("the slow primary was benched at all", bool(benched), str(benched))
    worst = max(benched.values()) if benched else 0
    check(f"...for ~{L._SLOW_FIRST_TOKEN_COOLDOWN_S:.0f}s, not the {settings.llm_unhealthy_cooldown_seconds:.0f}s "
          "a real failure gets",
          worst <= L._SLOW_FIRST_TOKEN_COOLDOWN_S + 1.0, f"{worst:.1f}s")

    print("\n[3] a REAL failure still gets the full cooldown (the distinction is the point)")
    c2 = L.LLMClient()

    def boom_resolve(model):
        if model == c2._chain[0]:
            class _Boom:
                class chat:
                    class completions:
                        @staticmethod
                        async def create(**kw):
                            raise L.APITimeoutError(request=None)   # a genuine transport failure
            return _Boom(), "broken-primary"
        return _client(_GoodStream), "good-fallback"

    c2._resolve = boom_resolve
    out = ""
    async for piece in c2.stream([{"role": "user", "content": "hi"}]):
        out += piece or ""
    now2 = asyncio.get_running_loop().time()
    real_bench = max((u - now2 for u in c2._unhealthy_until.values()), default=0)
    check("a broken primary still fails over", "ready" in out, repr(out))
    check(f"...and is benched for the FULL cooldown ({real_bench:.0f}s)",
          real_bench > L._SLOW_FIRST_TOKEN_COOLDOWN_S + 1.0,
          f"{real_bench:.1f}s — a real failure must outlast a slow one")

    print("\n[4] both streaming paths share the deadline (they must not drift)")
    import inspect

    src_stream = inspect.getsource(L.LLMClient.stream)
    src_tools = inspect.getsource(L.LLMClient.stream_with_tools)
    for name, src in (("stream", src_stream), ("stream_with_tools", src_tools)):
        check(f"{name}() applies _first_token_timeout",
              "_first_token_timeout" in src and "wait_for" in src,
              "this path can hang until the request timeout")
        check(f"{name}() raises _SlowFirstToken (so the short bench applies)",
              "_SlowFirstToken" in src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
