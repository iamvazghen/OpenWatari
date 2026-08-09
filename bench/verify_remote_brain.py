"""Verify the UNIFIED brain path: connect to the VPS brain over WS exactly like a device would,
send an utterance, and confirm the reply STREAMS back sentence-by-sentence. Measures real
time-to-first-sentence over the tailnet. No audio needed. Run:

    uv run python bench/verify_remote_brain.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

# Your brain host. Set AFON_BRAIN_WS_URL (e.g. ws://<your-vps-or-tailscale-host>:8765/voice),
# or it defaults to the local brain.
VPS_WS = os.environ.get("AFON_BRAIN_WS_URL", "ws://127.0.0.1:8765/voice")


async def main() -> None:
    import websockets

    sys.path.insert(0, "src")
    from afon.config import settings

    token = settings.api_auth_token or ""
    url = f"{VPS_WS}?token={token}"
    sid = "verify-1"
    print(f"connecting to {VPS_WS} …")
    async with websockets.connect(url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "hello", "session_id": sid,
                                  "device_id": "verify", "headphones_connected": False}))
        # wait for 'ready'
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print(f"  lifecycle: {ready.get('delta')}")

        for prompt in ["How are you, Afon?", "What time is it?"]:
            await ws.send(json.dumps({"type": "utterance", "session_id": sid,
                                      "text": prompt, "device_id": "verify", "ts_user_stop_ms": 0}))
            print(f"\n>>> {prompt!r}")
            t0 = time.perf_counter()
            first = None
            sentences = 0
            while True:
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                kind, delta, final = ev.get("kind"), ev.get("delta", ""), ev.get("final")
                if kind == "assistant" and delta.strip():
                    dt = time.perf_counter() - t0
                    if first is None:
                        first = dt
                    sentences += 1
                    print(f"  [{dt:5.2f}s] {delta.strip()}")
                elif kind == "tool" and delta.strip():
                    print(f"  (progress: {delta.strip()})")
                if final:
                    break
            print(f"  -- first sentence @ {first}s | {sentences} chunk(s)")


if __name__ == "__main__":
    asyncio.run(main())
