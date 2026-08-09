"""Structured observability (TODO 8.8) — the metrics registry + /metrics wiring.

Verifies the in-process metrics: counters increment, latency samples summarise (p50/p95/mean),
the snapshot is JSON-serialisable, and the agent/LLM chokepoints actually feed it (a turn bumps
'turns', a tool call bumps 'tool_calls', a tool error bumps 'tool_errors'). Offline — a stub agent
runs one real turn through the metric-instrumented tool path with no LLM.

    uv run python bench/test_metrics.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def main() -> None:
    from afon.brain.metrics import Metrics

    print("[1] counters + latency summary + JSON snapshot")
    m = Metrics()
    m.incr("turns")
    m.incr("turns")
    m.incr("tool_calls", 3)
    for v in (100.0, 200.0, 300.0, 400.0, 500.0):
        m.observe("llm_route_ms", v)
    snap = m.snapshot()
    check("counter increments", snap["counters"]["turns"] == 2, str(snap["counters"]))
    check("counter incr by N", snap["counters"]["tool_calls"] == 3)
    lat = snap["latency_ms"]["llm_route_ms"]
    check("latency count", lat["count"] == 5, str(lat))
    check("latency p50 correct", lat["p50"] == 300.0, str(lat))
    check("latency mean correct", lat["mean"] == 300.0, str(lat))
    check("last value recorded", snap["last"]["llm_route_ms"] == 500.0)
    check("uptime present", "uptime_s" in snap)
    check("snapshot is JSON-serialisable", isinstance(json.dumps(snap), str))
    check("empty metric summarises to zeros", m._summary([]) == {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0})

    print("\n[2] the agent tool path feeds the SHARED metrics singleton")
    from afon.brain.metrics import METRICS
    before = METRICS.snapshot()["counters"]

    from afon.brain.agent import AfonAgent
    agent = AfonAgent()

    async def _one_tool() -> None:
        # get_time is a real, no-network tool — exercises _run_one_tool's metric increments.
        await agent._run_one_tool("get_time", {}, None)
        # An unknown tool exercises the error path.
        await agent._run_one_tool("nonexistent_tool_zzz", {}, None)

    asyncio.run(_one_tool())
    after = METRICS.snapshot()["counters"]
    check("tool_calls incremented by the tool path",
          after.get("tool_calls", 0) >= before.get("tool_calls", 0) + 2,
          f"{before.get('tool_calls')} -> {after.get('tool_calls')}")
    check("tool_errors incremented on the unknown tool",
          after.get("tool_errors", 0) >= before.get("tool_errors", 0) + 1)
    check("per-tool counter tracked", after.get("tool.get_time", 0) >= 1)

    print("\n[3] /metrics route is auth-gated in the server")
    import inspect

    from afon.brain import server
    src = inspect.getsource(server._serve_client_http)
    check("server defines a /metrics route", '"/metrics"' in src)
    check("/metrics checks authorization", "_post_authorized" in src and "/metrics" in src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
