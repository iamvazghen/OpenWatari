"""Android (Termux) edge-lite (TODO 8.5) — the push-to-talk loop, verified off-device.

Runtime acceptance needs a phone (see deploy/termux/README.md). This proves the CODE is correct with
injected record/transcribe/speak + a fake brain client: it registers as device_id='android', runs a
full turn (record → STT → send → speak), accumulates streamed deltas and speaks only when the turn is
final, and degrades cleanly when the mic or STT yields nothing.

    uv run python bench/test_edge_lite.py
"""

from __future__ import annotations

import asyncio
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


class _FakeClient:
    """Stands in for BrainClient: records the Hello device_id and the sent text; scriptable reply."""

    def __init__(self, device_id: str, reply_chunks, *, link_up: bool = True) -> None:
        self.device_id = device_id
        self._reply = reply_chunks
        self._link_up = link_up
        self.sent: list[str] = []
        self._on_event = None

    def bind(self, on_event) -> None:
        self._on_event = on_event

    async def send_utterance(self, text: str, ts_user_stop_ms: int = 0) -> bool:
        self.sent.append(text)
        if not self._link_up:
            return False
        # Emit the scripted StreamEvents as if from the brain.
        from afon.shared.protocol import StreamEvent, StreamKind
        for i, (delta, final) in enumerate(self._reply):
            self._on_event(StreamEvent(session_id="s", kind=StreamKind.assistant, delta=delta, final=final))
        return True

    async def run(self) -> None:
        await asyncio.sleep(3600)

    async def stop(self) -> None:
        pass


async def main() -> None:
    from afon.edge.edge_lite import EdgeLite

    tmp_wav = Path(sys.argv[0]).parent / "_fake.wav"
    tmp_wav.write_bytes(b"RIFFfake")

    async def rec_ok(_secs):
        return tmp_wav

    async def rec_empty(_secs):
        return None

    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    print("[1] a full turn: record -> transcribe -> send -> speak; device_id='android'")
    client = _FakeClient("android", [("Half past ", False), ("two, sir.", True)])
    lite = EdgeLite(record=rec_ok, transcribe=lambda w: _say("what time is it"),
                    speak=speak, client=client)
    client.bind(lite._on_event)
    reply = await lite.one_turn()
    check("client registered as android", client.device_id == "android")
    check("transcript was sent to the brain", client.sent == ["what time is it"], str(client.sent))
    check("streamed deltas assembled into one reply", reply == "Half past two, sir.", reply)
    check("reply spoken exactly once (whole utterance)", spoken == ["Half past two, sir."], str(spoken))

    print("\n[2] non-final stream: buffer still speaks after the reply timeout (no hang)")
    spoken.clear()
    client2 = _FakeClient("android", [("One ", False), ("moment", False)])  # never final
    lite2 = EdgeLite(record=rec_ok, transcribe=lambda w: _say("hello"), speak=speak, client=client2,
                     reply_timeout=0.2)
    client2.bind(lite2._on_event)
    reply2 = await lite2.one_turn()
    check("non-final stream still eventually speaks the buffer", spoken == ["One moment"], str(spoken))
    check("reply returned is the buffered text", reply2 == "One moment", reply2)

    print("\n[3] empty mic capture -> no send, no speak (clean no-op)")
    spoken.clear()
    client3 = _FakeClient("android", [])
    lite3 = EdgeLite(record=rec_empty, transcribe=lambda w: _say("x"), speak=speak, client=client3)
    client3.bind(lite3._on_event)
    r3 = await lite3.one_turn()
    check("nothing sent when mic is empty", client3.sent == [], str(client3.sent))
    check("nothing spoken when mic is empty", spoken == [] and r3 == "")

    print("\n[4] empty transcript -> no send (STT produced nothing)")
    client4 = _FakeClient("android", [])
    lite4 = EdgeLite(record=rec_ok, transcribe=lambda w: _say("   "), speak=speak, client=client4)
    client4.bind(lite4._on_event)
    await lite4.one_turn()
    check("empty STT does not send to the brain", client4.sent == [])

    print("\n[5] link down mid-send -> spoken 'lost the link', no crash")
    spoken.clear()
    client5 = _FakeClient("android", [], link_up=False)
    lite5 = EdgeLite(record=rec_ok, transcribe=lambda w: _say("ping"), speak=speak, client=client5)
    client5.bind(lite5._on_event)
    await lite5.one_turn()
    check("a downed link is announced, not silent", spoken and "lost the link" in spoken[0], str(spoken))

    tmp_wav.unlink(missing_ok=True)
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


async def _say(text: str) -> str:
    return text


if __name__ == "__main__":
    asyncio.run(main())
