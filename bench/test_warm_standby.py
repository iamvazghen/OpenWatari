"""Phase 0.2 — RemoteBrain warm local standby (kill the brain SPOF).

When the VPS brain is reachable, the utterance goes to it. When it isn't, the SAME turn is answered
by a local in-process agent and spoken — no silence, no dropped turn — and the next reachable turn
returns to the VPS automatically. Hermetic: fake client + fake local agent, no network, no pipeline.

    uv run python bench/test_warm_standby.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.edge.remote_brain import RemoteBrain  # noqa: E402
from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeClient:
    def __init__(self, connected: bool, send_ok: bool) -> None:
        self.connected = connected
        self._send_ok = send_ok
        self.sent: list[str] = []

    async def send_utterance(self, text: str) -> bool:
        self.sent.append(text)
        return self._send_ok


class FakeAgent:
    def __init__(self, sentences: list[str]) -> None:
        self._s = sentences
        self.calls: list[str] = []

    async def respond_stream(self, text, on_progress=None):
        self.calls.append(text)
        for s in self._s:
            yield s


def _mk(connected: bool, send_ok: bool, sentences: list[str]):
    rb = RemoteBrain()
    rb._client = FakeClient(connected, send_ok)
    rb._fallback_agent = FakeAgent(sentences)
    spoken: list[str] = []

    async def rec(frame, *a, **k):
        if isinstance(frame, TTSSpeakFrame):
            spoken.append(frame.text)

    rb.push_frame = rec  # type: ignore[assignment]
    return rb, spoken


async def main() -> None:
    print("[1] VPS reachable -> route remote, local agent untouched")
    rb, spoken = _mk(connected=True, send_ok=True, sentences=["should not speak"])
    route = await rb._route_utterance("what time is it")
    check("routed to remote", route == "remote", route)
    check("utterance sent to the VPS", rb._client.sent == ["what time is it"])
    check("nothing spoken locally", spoken == [], repr(spoken))
    check("local standby agent never invoked", rb._fallback_agent.calls == [])

    print("\n[2] VPS unreachable (disconnected) -> local standby answers + speaks")
    rb, spoken = _mk(connected=False, send_ok=False, sentences=["It's ten past three, sir.", "Anything else?"])
    route = await rb._route_utterance("what time is it")
    check("routed to local", route == "local", route)
    check("did NOT try to send on a dead link", rb._client.sent == [], repr(rb._client.sent))
    check("both local sentences spoken in order",
          spoken == ["It's ten past three, sir.", "Anything else?"], repr(spoken))
    check("local agent got the transcript", rb._fallback_agent.calls == ["what time is it"])

    print("\n[3] Link 'connected' but the send fails -> still falls back to local")
    rb, spoken = _mk(connected=True, send_ok=False, sentences=["Local reply, sir."])
    route = await rb._route_utterance("hello")
    check("send attempted then fell back to local", route == "local" and rb._client.sent == ["hello"],
          f"route={route} sent={rb._client.sent}")
    check("local reply spoken", spoken == ["Local reply, sir."], repr(spoken))

    print("\n[4] Local agent produces nothing -> honest spoken degradation, never silent")
    rb, spoken = _mk(connected=False, send_ok=False, sentences=[])
    await rb._route_utterance("hello")
    check("spoke a degradation line instead of going mute", len(spoken) == 1 and "unreachable" in spoken[0].lower(),
          repr(spoken))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
