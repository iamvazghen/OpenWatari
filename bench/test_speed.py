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


# ── 03.F3: the presented catalogue has a measured ceiling ────────────────────────────────────
# "Catalogue narrowing is measured, not assumed." The tool schemas are the dominant per-turn
# prefill cost and nothing was counting them, so "58 tools ≈10k tokens" was an estimate in a
# document rather than a number anything could regress against.
#
# The count is taken from the turn's own trace row, not recomputed here: a test that
# re-implements the narrowing path measures its own copy, and would keep passing while the real
# path drifted. So each case runs a real turn through `respond` and reads what was presented.
#
# The ceiling is a RATCHET at today's measured worst, not the budget. The budget is ≤20 tools /
# ≤2.5k tokens and 03.R1's two-stage selection is what reaches it; until then this stops the
# number growing, which is the failure that actually happened (58 → 91 tools once lazy groups
# arm). The gap is printed on every run so it stays visible rather than settling in.
CATALOGUE_TOKEN_CEILING = 13_000     # measured worst today: 12,437 (coding group armed)
CATALOGUE_TOOL_CEILING = 95          # measured worst today: 91
CATALOGUE_TOKEN_TARGET = 2_500       # S03 budget — 03.R1, not asserted yet


class _NoToolLLM:
    """Answers in prose, never calls a tool — so the turn ends after exactly one presented
    catalogue and the row describes that one pass."""

    async def complete(self, messages, tools=None, tool_choice="auto", **kw):
        class _M:
            tool_calls = None
            content = "Certainly, sir."
        return _M()


async def catalogue_ceiling() -> None:
    from afon.brain.agent import AfonAgent
    from afon.brain import turn_trace as T

    import os
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        os.environ["AFON_TRACE_DIR"] = td
        try:
            agent = AfonAgent()
            agent._self_improve = False
            agent._llm = _NoToolLLM()

            cases = [
                ("hello there", "chatter"),
                ("what's the weather in Berlin", "a routed read"),
                ("check my email and remember to call mum", "a compound request"),
                ("what do you think about the rabbit farm", "an open question"),
                ("fix the failing test in the repo and commit it", "a coding turn"),
            ]
            worst_tokens = worst_tools = 0
            worst_case = ""
            for text, label in cases:
                agent.reset_session(reason="bench")
                await agent.respond(text)
                row = T.recent(1)[0]
                tokens, tools = row["catalogue_tokens"], row["tools_considered"]
                print(f"    {label:22s} {tools:3d} tools  {tokens:6,d} tokens  [{row['intent']}]")
                check(tokens <= CATALOGUE_TOKEN_CEILING,
                      f"{label}: {tokens} presented tokens over the {CATALOGUE_TOKEN_CEILING} ceiling")
                check(tools <= CATALOGUE_TOOL_CEILING,
                      f"{label}: {tools} presented tools over the {CATALOGUE_TOOL_CEILING} ceiling")
                if tokens > worst_tokens:
                    worst_tokens, worst_tools, worst_case = tokens, tools, label
                # The point of the trace: the cost is recorded, not inferred.
                check(row["prefill_tokens"] >= tokens,
                      f"{label}: prefill must include the catalogue it presented")
                # A ceiling passes trivially against a zero, so the two halves of the measurement
                # have to agree: a catalogue that costs tokens is a catalogue with tools in it.
                # Found by planting `tools_considered = 0`, which the ceilings waved through.
                check((tokens > 0) == (tools > 0),
                      f"{label}: {tools} tools but {tokens} tokens — the trace is counting one "
                      f"half of the catalogue and not the other")

            check(worst_tokens > 0 and worst_tools > 0,
                  f"at least one turn presented a catalogue at all ({worst_tools} tools / "
                  f"{worst_tokens} tokens — all zero means the measurement is reading nothing)")
            # Chatter carries no tools — the fast path S01 depends on. If this ever costs tokens,
            # the ~1.7s of first-word latency that path exists to skip has come back.
            agent.reset_session(reason="bench")
            await agent.respond("hello there")
            check(T.recent(1)[0]["catalogue_tokens"] == 0,
                  "chatter must present no catalogue at all")

            over = worst_tokens - CATALOGUE_TOKEN_TARGET
            print(f"    worst: {worst_case} at {worst_tokens:,} tokens / {worst_tools} tools "
                  f"— {over:,} over the {CATALOGUE_TOKEN_TARGET:,} budget (03.R1)")
        finally:
            os.environ.pop("AFON_TRACE_DIR", None)


asyncio.run(main())
print("\n[2] presented-catalogue ceiling (03.F3)")
asyncio.run(catalogue_ceiling())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
