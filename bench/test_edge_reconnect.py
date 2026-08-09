"""P0 #3 — the edge auto-reconnects to the brain after a restart.

Stands up a REAL BrainServer (with a fake, network-free agent), connects a BrainClient, exchanges a
turn, then kills the server and brings it back — asserting the client reconnects on its own and the
next turn works. Proves the "silent death after a brain restart" gap is closed.

Hermetic: loopback WebSocket, fake agent (no LLM), tight backoff so the test is fast.

    uv run python bench/test_edge_reconnect.py
"""

from __future__ import annotations

import asyncio
import sys
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


class FakeAgent:
    """Stand-in AfonAgent: no LLM, deterministic reply, satisfies the server's contract."""

    async def warmup(self) -> None:
        return None

    async def respond(self, text: str, on_progress=None) -> str:
        return f"You said {text}."

    async def respond_stream(self, text: str, on_progress=None):
        yield f"You said {text}."


async def _serve_once(server, host: str, port: int):
    """Open a websockets server bound to the BrainServer handler; returns the server object."""
    import websockets

    return await websockets.serve(server.handler, host, port, max_size=4 * 1024 * 1024)


async def main() -> None:
    from afon.brain.server import BrainServer
    from afon.config import settings
    from afon.edge.brain_client import BrainClient

    host, port = "127.0.0.1", 8794
    settings.brain_ws_url = f"ws://{host}:{port}/voice"
    settings.api_auth_token = None  # loopback, no auth

    server = BrainServer(agent=FakeAgent())

    events: list = []

    def on_event(ev) -> None:
        events.append(ev)

    states: list[str] = []
    client = BrainClient(
        session_id="t1", on_event=on_event, on_state=states.append,
        backoff_initial=0.2, backoff_max=1.0, heartbeat_s=5.0,
    )

    print("[1] connect + one turn against a live brain")
    ws_server = await _serve_once(server, host, port)
    client_task = asyncio.create_task(client.run())

    async def wait_for(pred, timeout=5.0) -> bool:
        for _ in range(int(timeout / 0.05)):
            if pred():
                return True
            await asyncio.sleep(0.05)
        return False

    check("client reaches 'connected'", await wait_for(lambda: client.connected))
    check("got the lifecycle 'ready'", await wait_for(
        lambda: any(e.kind.value == "lifecycle" and e.delta == "ready" for e in events)))
    events.clear()
    sent = await client.send_utterance("hello one")
    check("utterance sent while connected", sent)
    check("brain replied over the link", await wait_for(
        lambda: any("hello one" in e.delta for e in events)))

    print("\n[2] brain goes down -> client notices and tries to reconnect")
    ws_server.close()
    await ws_server.wait_closed()
    check("client leaves 'connected' after the brain dies", await wait_for(
        lambda: not client.connected, timeout=6.0))
    check("client entered a reconnecting/connecting state", await wait_for(
        lambda: any(s in ("reconnecting", "connecting") for s in states), timeout=6.0))

    print("\n[3] brain comes back -> client reconnects on its own and the next turn works")
    server2 = BrainServer(agent=FakeAgent())
    ws_server2 = await _serve_once(server2, host, port)
    check("client reconnects automatically", await wait_for(lambda: client.connected, timeout=8.0))
    events.clear()
    # The link may take a beat to re-register; retry the send until it lands.
    ok_turn = False
    for _ in range(40):
        await client.send_utterance("hello two")
        if await wait_for(lambda: any("hello two" in e.delta for e in events), timeout=0.3):
            ok_turn = True
            break
    check("a turn works after auto-reconnect", ok_turn)

    print("\n[4] stop() ends the supervised loop cleanly")
    await client.stop()
    try:
        await asyncio.wait_for(client_task, timeout=3.0)
        check("run() returns after stop()", True)
    except asyncio.TimeoutError:
        check("run() returns after stop()", False, "run() did not exit")
    ws_server2.close()
    await ws_server2.wait_closed()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
