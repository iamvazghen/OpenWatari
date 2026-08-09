"""Efficiency report — measure Afon's hot paths and compare them to efficient-operation targets.

For a voice companion, *perceived* speed is everything: how long after you stop speaking before he
starts. This script measures the parts the brain controls and grades each against a target, so we
know what (if anything) to fine-tune. It is informational — it never fails the build.

Measured:
  * system-prompt size (tokens) + tool count   — context the model re-reads every turn
  * L1 memory recall + digest latency          — local, should be sub-millisecond
  * L4 cache hit speedup                        — a repeat lookup should be ~free
  * utility cold vs warm (weather)             — proves the cache helps a real call
  * brain TTFT + full direct turn               — the dominant slice of TTFW (network)

Run: uv run python bench/efficiency_report.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
rows: list[tuple[str, str, str, str]] = []  # (metric, measured, target, verdict)


def grade(value: float, good: float, ok: float, lower_is_better: bool = True) -> str:
    if lower_is_better:
        return "GOOD" if value <= good else ("OK" if value <= ok else "TUNE")
    return "GOOD" if value >= good else ("OK" if value >= ok else "TUNE")


def add(metric: str, measured: str, target: str, verdict: str) -> None:
    rows.append((metric, measured, target, verdict))


async def main() -> None:
    from afon.brain.cache import Cache
    from afon.brain.context import build_system_prompt
    from afon.brain.memory import STORE
    from afon.brain.tools import core_tool_schemas, tool_names
    from afon.config import settings

    print("Measuring… (the brain-latency rows need the freellmapi tunnel up)\n")

    # --- 1. system prompt + tool surface ----------------------------------------------
    sp = build_system_prompt()
    approx_tokens = len(sp) // 4
    add("System prompt size", f"{len(sp)} chars (~{approx_tokens} tok)", "<= 2000 tok",
        grade(approx_tokens, 1500, 2000))
    # What the model actually sees on a typical turn = core schemas + get_time + delegate_to_fleet.
    # Lazy groups (coding/office/home) add their tools only on turns that need them (see agent.py).
    n_core = len(core_tool_schemas()) + 2
    n_full = len(tool_names()) + 2
    add("Tool surface (per turn)", f"{n_core} core ({n_full} in registry)", "<= 48 (schema bloat)",
        grade(n_core, 40, 48))

    # --- 2. L1 memory recall + digest --------------------------------------------------
    t0 = time.perf_counter()
    for _ in range(50):
        STORE.recall("rabbit farm", limit=5)
    recall_ms = (time.perf_counter() - t0) / 50 * 1000
    add("L1 recall (keyword)", f"{recall_ms:.2f} ms", "<= 5 ms", grade(recall_ms, 2, 5))
    t0 = time.perf_counter()
    for _ in range(50):
        STORE.recent_digest(settings.memory_digest_max)
    digest_ms = (time.perf_counter() - t0) / 50 * 1000
    add("L1 digest (startup)", f"{digest_ms:.2f} ms", "<= 5 ms", grade(digest_ms, 2, 5))

    # --- 3. L4 cache hit speedup -------------------------------------------------------
    cache = Cache(redis_url=None)

    async def slow() -> str:
        await asyncio.sleep(0.05)   # 50 ms "provider"
        return "value"

    t0 = time.perf_counter()
    await cache.cached("bench", "k", ttl=60, factory=slow)
    miss_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    for _ in range(100):
        await cache.cached("bench", "k", ttl=60, factory=slow)
    hit_ms = (time.perf_counter() - t0) / 100 * 1000
    speedup = miss_ms / hit_ms if hit_ms else 0
    add("L4 cache miss vs hit", f"{miss_ms:.1f} ms -> {hit_ms:.3f} ms", "hit << miss",
        grade(speedup, 50, 10, lower_is_better=False))

    # --- 4. utility cold vs warm (real network; weather) -------------------------------
    try:
        from afon.brain.tools.utility import weather

        t0 = time.perf_counter()
        await weather({"location": "Yerevan"})
        cold = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        await weather({"location": "Yerevan"})
        warm = (time.perf_counter() - t0) * 1000
        add("Utility weather cold/warm", f"{cold:.0f} ms -> {warm:.1f} ms", "warm ~0 ms",
            grade(warm, 5, 50))
    except Exception as e:  # noqa: BLE001
        add("Utility weather cold/warm", f"network down ({type(e).__name__})", "warm ~0 ms", "SKIP")

    # --- 5. brain TTFT + a full direct turn (real network) -----------------------------
    try:
        from afon.brain.llm import LLMClient

        llm = LLMClient()
        # Production warms every provider connection at startup (agent.warmup -> llm.warmup), so a
        # real first turn never pays cold TLS/DNS setup. Warm here too, else we'd measure a cold-
        # connect artifact (~1.5s) that the live assistant never sees.
        await llm.warmup()
        ttfts: list[float] = []
        for _ in range(3):
            t0 = time.perf_counter()
            got = False
            async for _delta in llm.stream(
                [{"role": "system", "content": "Answer in one short sentence."},
                 {"role": "user", "content": "Say hello, Afon."}],
            ):
                if not got:
                    ttfts.append((time.perf_counter() - t0) * 1000)
                    got = True
        if ttfts:
            mean_ttft = statistics.mean(ttfts)
            add("Brain TTFT (stream)", f"{mean_ttft:.0f} ms (n={len(ttfts)})", "<= 1200 ms",
                grade(mean_ttft, 800, 1200))
    except Exception as e:  # noqa: BLE001
        add("Brain TTFT (stream)", f"network down ({type(e).__name__})", "<= 1200 ms", "SKIP")

    try:
        from afon.brain.agent import AfonAgent

        agent = AfonAgent()
        await agent.warmup()        # match production: connections primed before the first turn
        t0 = time.perf_counter()
        await agent.respond("What is two plus two?")
        turn_ms = (time.perf_counter() - t0) * 1000
        add("Brain full turn (direct)", f"{turn_ms:.0f} ms", "<= 2500 ms", grade(turn_ms, 1800, 2500))
    except Exception as e:  # noqa: BLE001
        add("Brain full turn (direct)", f"network down ({type(e).__name__})", "<= 2500 ms", "SKIP")

    # --- render ------------------------------------------------------------------------
    print(f"{'metric':<28}{'measured':<28}{'target':<22}verdict")
    print("-" * 90)
    for metric, measured, target, verdict in rows:
        color = {"GOOD": GREEN, "OK": YELLOW, "TUNE": RED, "SKIP": YELLOW}.get(verdict, "")
        print(f"{metric:<28}{measured:<28}{target:<22}{color}{verdict}{RESET}")
    tune = [m for m, _, _, v in rows if v == "TUNE"]
    print("\nFine-tune candidates: " + (", ".join(tune) if tune else "none — all within target."))


if __name__ == "__main__":
    asyncio.run(main())
