"""J7.2 — the most consequential state in the system was carried as English inside a RuntimeError.

"Every model in the failover chain refused" is not a generic runtime error. It is the one condition
where Afon has lost the ability to think at all, and three different consumers need to tell it apart
from an ordinary call failure:

  * the brain, which must apologise rather than retry into the same wall;
  * the error journal, which records it as its own operation (`llm_chain_exhausted`) with every
    model's error attached, not just the last one;
  * **the test harness**, which uses it to decide whether an offline run is a SKIP or a real FAIL —
    and did so by grepping the child's stdout for the literal words *"all LLM models failed"*.

That last one is the bite. A harness in one file matches a sentence written in another, and nothing
connects them: reword the message and every offline suite run silently flips from SKIP to FAIL, or
worse, a genuine failure starts being excused. Exactly the prose-matching class J7.3 and J3.6 each
removed elsewhere.

The fix keeps the sentence (a subprocess can only communicate in text) but stops it being a
coincidence: the marker is a module constant, the condition has a type, and this test asserts the
harness and the code still agree on the words.

`ChainExhausted` subclasses `RuntimeError` for the same reason `ToolResult` subclasses `str` in
J7.3: every existing caller already catches `RuntimeError` or `Exception`, so a domain type can be
introduced without a migration and without changing a single call site's behaviour.

Hermetic: no network — every model in the chain is stubbed to fail.

    uv run python bench/test_llm_chain_exhausted.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import afon.brain.llm as llm_mod  # noqa: E402
from afon.config import settings  # noqa: E402

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _DeadClient:
    """Every provider call fails outright — the vision path's version of exhaustion."""

    def __init__(self, *_a, **_k):
        self.chat = self
        self.completions = self

    async def create(self, *_a, **_k):
        raise ConnectionError("no route to host")


class _EmptyClient:
    """Every provider returns 200 with no usable choice.

    This is the failure the chat chain actually fails over on (`_EmptyResponse`) — some proxies wrap
    their errors in a 200 — so it exercises the real loop rather than a shape that escapes it.
    """

    def __init__(self, *_a, **_k):
        self.chat = self
        self.completions = self

    async def create(self, *_a, **_k):
        from types import SimpleNamespace

        msg = SimpleNamespace(content=None, tool_calls=None, role="assistant")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


async def main() -> None:
    saved_client = llm_mod.AsyncOpenAI
    saved_primary = settings.llm_primary_model
    saved_fallbacks = settings.llm_fallback_models
    saved_vision = settings.vision_models
    try:
        print("[1] the condition has a type, and the type is additive")
        check(hasattr(llm_mod, "ChainExhausted"), "llm.ChainExhausted exists")
        check(issubclass(llm_mod.ChainExhausted, RuntimeError),
              "…and is a RuntimeError subclass, so no existing caller changes",
              str(llm_mod.ChainExhausted.__mro__))
        check(isinstance(getattr(llm_mod, "CHAIN_EXHAUSTED", None), str)
              and llm_mod.CHAIN_EXHAUSTED.strip() != "",
              "the wire marker is a named constant, not a literal buried in a raise")

        print("\n[2] the harness and the code still agree on the words")
        # The coupling is real and cannot be removed (subprocess stdout is text). It can be made
        # loud: if someone rewords the message, this fails here instead of quietly reclassifying
        # every offline suite run.
        harness = (ROOT / "bench" / "run_all_tests.py").read_text(encoding="utf-8")
        check(llm_mod.CHAIN_EXHAUSTED in harness,
              f"run_all_tests.py matches on '{llm_mod.CHAIN_EXHAUSTED}'",
              "the classifier greps a sentence this module no longer prints")
        src = (ROOT / "src" / "afon" / "brain" / "llm.py").read_text(encoding="utf-8")
        check(src.count(f'"{llm_mod.CHAIN_EXHAUSTED}"') == 1,
              "…and llm.py spells it exactly once — at the constant, not at a raise site",
              f"{src.count(chr(34) + llm_mod.CHAIN_EXHAUSTED + chr(34))} literal spellings; "
              "a second one is how the two drift apart again")

        print("\n[3] an exhausted chain raises it, with the marker in the message")
        llm_mod.AsyncOpenAI = _EmptyClient
        settings.llm_primary_model = "p"
        settings.llm_fallback_models = "f1,f2"
        client = llm_mod.LLMClient()
        try:
            await client.complete([{"role": "user", "content": "hello"}])
            check(False, "complete() raises when every model fails")
        except llm_mod.ChainExhausted as e:
            check(True, "complete() raises ChainExhausted")
            check(llm_mod.CHAIN_EXHAUSTED in str(e), "…and the message carries the marker", str(e))
        except Exception as e:  # noqa: BLE001
            check(False, "complete() raises ChainExhausted", f"{type(e).__name__}: {e}")

        print("\n[4] the vision chain is the same condition, separately worded")
        llm_mod.AsyncOpenAI = _DeadClient
        settings.vision_models = "v1,v2"
        client = llm_mod.LLMClient()
        try:
            await client.see("aGVsbG8=", "what is this")
            check(False, "see() raises when every vision model fails")
        except llm_mod.ChainExhausted as e:
            check(True, "see() raises ChainExhausted")
            check(llm_mod.VISION_CHAIN_EXHAUSTED in str(e),
                  "…with the vision marker, so the two are distinguishable", str(e))
        except Exception as e:  # noqa: BLE001
            check(False, "see() raises ChainExhausted", f"{type(e).__name__}: {e}")

        print("\n[5] no vision models configured is a different failure, not exhaustion")
        settings.vision_models = ""
        try:
            await client.see("aGVsbG8=", "what is this")
            check(False, "see() with no models configured still raises")
        except llm_mod.ChainExhausted as e:
            check(False, "unconfigured is NOT chain exhaustion", str(e))
        except RuntimeError as e:
            check("no vision models configured" in str(e),
                  "unconfigured raises its own error, not ChainExhausted", str(e))
    finally:
        llm_mod.AsyncOpenAI = saved_client
        settings.llm_primary_model = saved_primary
        settings.llm_fallback_models = saved_fallbacks
        settings.vision_models = saved_vision

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
