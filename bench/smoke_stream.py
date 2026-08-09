"""Smoke test: real agent.respond_stream against the live proxy + new model.

Prints each spoken sentence as it arrives with a timestamp, so we can see time-to-first-sentence
(the latency the user actually feels) vs the old wait-for-the-whole-reply behaviour. Run:

    uv run python bench/smoke_stream.py
"""

from __future__ import annotations

import asyncio
import time

from afon.brain.agent import AfonAgent


async def run(prompt: str) -> None:
    agent = AfonAgent()
    await agent.warmup()
    print(f"\n>>> {prompt!r}")
    t0 = time.perf_counter()
    first = None
    n = 0
    async for sentence in agent.respond_stream(prompt):
        dt = time.perf_counter() - t0
        if first is None:
            first = dt
        n += 1
        print(f"  [{dt:5.2f}s] {sentence}")
    total = time.perf_counter() - t0
    print(f"  -- first sentence @ {first:.2f}s | {n} sentences | total {total:.2f}s")


async def main() -> None:
    for p in ["How are you, Afon?", "What time is it?", "Give me a two sentence summary of why the sky is blue."]:
        await run(p)


if __name__ == "__main__":
    asyncio.run(main())
