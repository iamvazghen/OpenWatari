"""Phase 5.2 — device handoff: unprompted speech follows the owner to his active device.

Before this, a proactive nudge / fired reminder was broadcast to EVERY connected client at once (or a
phone in a pocket). Handoff makes the brain track the device the owner last spoke to and deliver there,
falling back to a full broadcast only if that device has dropped (so reach is never reduced). This locks:
an utterance marks its device active, a nudge then targets only that device, a later utterance on another
device hands off, and a vanished active device falls back to everyone.

    uv run python bench/test_device_handoff.py
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


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

        class _Req:
            headers: dict = {}

        self.request = _Req()

    async def send(self, data) -> None:
        import json

        self.sent.append(json.loads(data))

    def spoke(self) -> list[str]:
        return [f["delta"] for f in self.sent if f.get("kind") == "assistant"]


class StubAgent:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def warmup(self) -> None:
        pass

    def note_proactive(self, msg: str) -> None:
        pass

    async def respond_stream(self, text: str, on_progress=None):
        self.seen.append(text)
        yield "Okay."


async def main() -> None:
    from afon.brain.server import BrainServer
    from afon.shared.protocol import Hello, Utterance

    # A first-of-day digest addendum would add a stray chunk; this test is about routing, so pin it off.
    from afon.brain import daily_digest
    daily_digest.due = lambda channel, now=None: False

    server = BrainServer(agent=StubAgent())
    ws_laptop, ws_phone = FakeWS(), FakeWS()
    await server.handle_message(ws_laptop, Hello(session_id="laptop", device_id="laptop", headphones_connected=False))
    await server.handle_message(ws_phone, Hello(session_id="phone", device_id="iphone", headphones_connected=False))
    ws_laptop.sent.clear()
    ws_phone.sent.clear()

    print("[1] no active device yet -> a nudge broadcasts to every connected device")
    n = await server._broadcast_assistant("standup in five, sir")
    check("reached both devices", n == 2, str(n))
    check("laptop heard it", any("standup" in d for d in ws_laptop.spoke()))
    check("phone heard it", any("standup" in d for d in ws_phone.spoke()))

    print("\n[2] an utterance marks its device ACTIVE (real handle_message path)")
    await server.handle_message(ws_phone, Utterance(session_id="phone", text="what's my day", ts_user_stop_ms=1))
    check("active device set immediately", server._active_sid == "phone")
    await server._turns["phone"]  # let the turn finish cleanly
    ws_laptop.sent.clear()
    ws_phone.sent.clear()

    print("\n[3] with the phone active, a nudge follows to the phone ONLY (handoff)")
    n = await server._broadcast_assistant("your payout cleared, sir")
    check("delivered to exactly one device", n == 1, str(n))
    check("the phone got it", any("payout" in d for d in ws_phone.spoke()))
    check("the laptop was NOT interrupted", ws_laptop.spoke() == [])

    print("\n[4] a laptop utterance hands off -> the nudge now targets the laptop")
    await server.handle_message(ws_laptop, Utterance(session_id="laptop", text="draft the email", ts_user_stop_ms=1))
    check("active handed off to the laptop", server._active_sid == "laptop")
    await server._turns["laptop"]
    ws_laptop.sent.clear()
    ws_phone.sent.clear()
    n = await server._broadcast_assistant("the build finished, sir")
    check("delivered to one device", n == 1, str(n))
    check("the laptop got it", any("build" in d for d in ws_laptop.spoke()))
    check("the phone stayed quiet", ws_phone.spoke() == [])

    print("\n[5] the active device drops -> fall back to everyone still connected (reach preserved)")
    # Simulate the socket-close cleanup the handler's finally runs for the active device.
    server._conns.pop("laptop", None)
    if server._active_sid == "laptop":
        server._active_sid = None
    targets = server._speak_targets()
    check("active cleared on drop", server._active_sid is None)
    check("falls back to the remaining device", [sid for sid, _ in targets] == ["phone"])
    ws_phone.sent.clear()
    n = await server._broadcast_assistant("still here, sir")
    check("nudge still reaches the surviving device", n == 1 and any("still here" in d for d in ws_phone.spoke()))

    print("\n[6] _speak_targets: an active id that isn't connected never wins")
    server._active_sid = "ghost-device"
    tids = {sid for sid, _ in server._speak_targets()}
    check("ghost active -> broadcast to real conns", tids == {"phone"} and "ghost-device" not in tids)

    print("\n[double-wake] one sentence, two microphones, ONE answer  [12.F3]")
    srv = BrainServer(agent=StubAgent())
    # Direct arbitration checks first: they are the decision, and the message path below only
    # proves it is wired to it.
    check("the first device to claim an utterance answers",
          srv._claim_wake("what's the weather", "laptop", 0.0, now=1000.0) is None)
    check("a second device hearing the SAME sentence stays silent",
          srv._claim_wake("what's the weather", "phone", 0.0, now=1000.3) == "laptop")
    check("...and case and spacing do not let it through",
          srv._claim_wake("  What's   the WEATHER ", "glasses", 0.0, now=1000.4) == "laptop")
    check("a DIFFERENT sentence from the second device is answered normally",
          srv._claim_wake("turn the lights off", "phone", 0.0, now=1000.5) is None)
    check("the same device repeating itself is never blocked by its own claim",
          srv._claim_wake("what's the weather", "laptop", 0.0, now=1000.6) is None)

    # Louder wins. Without a score every device reports 0.0 and arrival order decides, which is why
    # the checks above hold for an older edge that knows nothing about this.
    srv2 = BrainServer(agent=StubAgent())
    check("a quiet device claims first", srv2._claim_wake("play some music", "phone", 0.2,
                                                          now=2000.0) is None)
    check("a louder device takes the turn from it",
          srv2._claim_wake("play some music", "laptop", 0.9, now=2000.2) is None)
    check("...and the quieter one is now the loser on record",
          srv2._claim_wake("play some music", "phone", 0.2, now=2000.3) == "laptop")

    # Outside the window it is a genuine repeat, not an echo of one sentence.
    srv3 = BrainServer(agent=StubAgent())
    from afon.brain.server import DOUBLE_WAKE_S
    check("an old claim does not silence a later ask",
          srv3._claim_wake("what time is it", "laptop", 0.0, now=3000.0) is None
          and srv3._claim_wake("what time is it", "phone", 0.0,
                               now=3000.0 + DOUBLE_WAKE_S + 1) is None)
    check("stale claims are pruned rather than accumulating forever",
          len(srv3._wake_claims) == 1, str(srv3._wake_claims))

    # And the wire: the losing device is told the turn is over so it stops listening, and it
    # speaks nothing.
    srv4 = BrainServer(agent=StubAgent())
    ws_a, ws_b = FakeWS(), FakeWS()
    await srv4.handle_message(ws_a, Hello(session_id="a", device_id="laptop",
                                          headphones_connected=False))
    await srv4.handle_message(ws_b, Hello(session_id="b", device_id="iphone",
                                          headphones_connected=False))
    ws_a.sent.clear(); ws_b.sent.clear()
    await srv4.handle_message(ws_a, Utterance(session_id="a", text="remind me at six",
                                              ts_user_stop_ms=1))
    await srv4.handle_message(ws_b, Utterance(session_id="b", text="remind me at six",
                                              ts_user_stop_ms=1))
    check("only one device is given the turn", "b" not in srv4._turns, str(list(srv4._turns)))
    check("the losing device says nothing at all", ws_b.spoke() == [], str(ws_b.spoke()))
    check("...but is told the turn is over, so it isn't left listening",
          any(f.get("kind") == "lifecycle" and f.get("final") for f in ws_b.sent),
          str(ws_b.sent))
    for t in srv4._turns.values():
        t.cancel()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
