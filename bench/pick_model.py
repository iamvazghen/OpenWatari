"""Pick the voice brain by REAL first-sentence latency under the full agent load.

The bare-prompt bench (old version) was misleading — gpt-oss-120b looked fast (0.6s) but under
the agent's ~1.8k-tok system prompt + 40 tool schemas it "thinks" for several seconds before the
first token. For voice the only metric that matters is time-to-first-SPOKEN-sentence. This runs
agent.respond_stream with each model forced as the sole chain entry and times it. Run:

    uv run python bench/pick_model.py
"""

from __future__ import annotations

import asyncio
import time

from afon.brain.agent import AfonAgent

CANDIDATES = [
    "groq/compound",
    "gemini-3.5-flash",
    "mistral-small-latest",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "mistral-medium-latest",
]

PROMPTS = ["How are you, Afon?", "Give me a two sentence summary of why the sky is blue."]


async def time_model(model: str) -> dict:
    out: dict = {"model": model}
    try:
        agent = AfonAgent()
        agent._llm._chain = [model]  # force just this model
        await agent.warmup()
        firsts, totals, replies = [], [], []
        for p in PROMPTS:
            t0 = time.perf_counter()
            first = None
            parts = []
            async for s in agent.respond_stream(p):
                if first is None:
                    first = time.perf_counter() - t0
                parts.append(s)
            firsts.append(first or 0)
            totals.append(time.perf_counter() - t0)
            replies.append(" ".join(parts)[:50])
        out["first_avg"] = round(sum(firsts) / len(firsts), 2)
        out["worst_first"] = round(max(firsts), 2)
        out["sample"] = replies[0]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:50]}"
    return out


async def main() -> None:
    print(f"{'model':<42} {'avg1st':>7} {'worst1st':>9}  sample")
    print("-" * 100)
    for m in CANDIDATES:
        r = await time_model(m)
        if "error" in r:
            print(f"{m:<42} {'—':>7} {'—':>9}  ERROR {r['error']}")
        else:
            print(f"{m:<42} {r['first_avg']:>7} {r['worst_first']:>9}  {r['sample']!r}")


if __name__ == "__main__":
    asyncio.run(main())
