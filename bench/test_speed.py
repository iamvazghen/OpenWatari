"""C4 hermetic test — streaming TTFW budget (no network).

The speed win that matters for a voice assistant is time-to-first-WORD: the owner should hear the first
spoken sentence while the rest is still generating, not after. With a stream whose tail is deliberately
slow, assert respond_stream emits the first sentence in a tiny fraction of the whole turn. Relative +
absolute guards (like test_failover_latency) so only a real regression trips it, not scheduling noise.

(The rest of C4 — fast-tier chain + first-token-deadline failover + tool turns routed to the reliable
caller — is already covered by test_llm_routing and test_intent_router; this fills the TTFW gap.)
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


class _SlowTailLLM:
    """First sentence lands immediately; the rest stalls — models real token-generation time."""

    def __init__(self, gap: float) -> None:
        self.gap = gap

    async def stream_with_tools(self, messages, tools=None, temperature=0.6,
                                tool_choice="auto", skip_primary=False):
        yield ("text", "First sentence. ")
        await asyncio.sleep(self.gap)      # the slow tail
        yield ("text", "Second sentence. Third sentence.")


async def main() -> None:
    from afon.brain.agent import AfonAgent

    agent = AfonAgent()
    agent._self_improve = False
    agent._llm = _SlowTailLLM(gap=0.6)

    gen = agent.respond_stream("tell me a short story")   # conversational: no forced tool, streams
    t0 = time.perf_counter()
    first = await gen.__anext__()
    ttfw = time.perf_counter() - t0
    async for _ in gen:                                   # drain the slow tail
        pass
    total = time.perf_counter() - t0

    check("first spoken chunk is the opening sentence", first == "First sentence.")
    check(f"first word streams before the tail (ttfw {ttfw*1000:.0f}ms << total {total*1000:.0f}ms)",
          ttfw < total * 0.5)
    check(f"first word is near-instant, not blocked on the tail (ttfw {ttfw*1000:.0f}ms < 500ms)",
          ttfw < 0.5)


asyncio.run(main())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
