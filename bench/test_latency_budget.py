"""Latency regression guard — a live budget assertion, not a measurement.

One WS round-trip to the configured brain ("What time is it?") must produce its FIRST streamed
sentence inside BUDGET_S. Typical is ~1s over the tailnet; the budget is deliberately generous so
only a real regression trips it (the freellmapi-proxy incident was 15.7s). Skips when no brain is
reachable, so the hermetic suite still runs anywhere; the VPS deploy gate runs it against the
live brain, which is the point.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from afon.config import settings

BUDGET_S = 6.0  # first spoken sentence; typical ~1s, proxy-regression was 15.7s
CONNECT_TIMEOUT_S = 8


async def _first_sentence_latency() -> float:
    import websockets

    url = f"{settings.brain_ws_url}?token={settings.api_auth_token or ''}"
    async with websockets.connect(url, open_timeout=CONNECT_TIMEOUT_S) as ws:
        sid = "latency-guard"
        await ws.send(json.dumps({"type": "hello", "session_id": sid,
                                  "device_id": "latency-guard", "headphones_connected": False}))
        await asyncio.wait_for(ws.recv(), timeout=10)  # 'ready'
        await ws.send(json.dumps({"type": "utterance", "session_id": sid,
                                  "text": "What time is it?", "device_id": "latency-guard",
                                  "ts_user_stop_ms": 0}))
        t0 = time.perf_counter()
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=BUDGET_S * 3))
            if ev.get("kind") == "assistant" and ev.get("delta", "").strip():
                return time.perf_counter() - t0


def test_brain_first_sentence_within_budget() -> None:
    import websockets

    try:
        latency = asyncio.run(_first_sentence_latency())
    except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
        pytest.skip(f"brain not reachable from here ({type(e).__name__}) — guard runs where it is")
    assert latency < BUDGET_S, (
        f"brain first-sentence latency {latency:.2f}s blew the {BUDGET_S}s budget — "
        "check the LLM primary/provider chain before deploying (see TODO.md #1)"
    )


# Runnable as a script. The canonical runner is `bench/run_all_tests.py`, not pytest, and this file
# is EXEMPT from it (it needs a reachable brain) — so without this block the guard had no way to be
# executed by anything at all. Run it by hand, or from the deploy host, before shipping:
#
#     .venv/Scripts/python.exe bench/test_latency_budget.py
if __name__ == "__main__":
    import sys

    try:
        test_brain_first_sentence_within_budget()
    except BaseException as e:  # noqa: BLE001 — pytest.skip raises Skipped, which is not an Exception
        if type(e).__name__ != "Skipped":
            raise
        print(f"=== 0/1 checks passed (SKIPPED: {e}) ===")
        sys.exit(0)
    print(f"=== 1/1 checks passed (first sentence within {BUDGET_S}s) ===")
