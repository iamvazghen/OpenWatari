"""H1.1 — audio plays where the speakers are, not where the brain is.

The bug this pins: the VPS brain has ffplay installed, so `play_file` started a player successfully,
logged "local playback started" and reported "Playing … out loud now, sir" — into a headless server.
No error, no warning, just silence in the room. Nothing short of a test that asserts *which host*
runs the player can catch that class of failure.

Covers: all three primitives dispatch; the payload rides with the play op; the size ceiling matches
what the transport can actually carry; oversize degrades honestly; the executor plays for real and
cleans up after itself; and pc_agent actually wires the handlers up.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path


def _tmpfile(data: bytes, suffix: str = ".mp3") -> Path:
    """A closed temp file holding `data`. mkstemp hands back an OPEN fd, and on Windows leaving it
    open makes the later unlink fail with 'used by another process'."""
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    p = Path(name)
    p.write_bytes(data)
    return p

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> None:
    import jarvis.brain.tools.localplay as lp
    from jarvis.brain.pc_link import PC_LINK
    from jarvis.brain.tools import tool_names

    print("[1] all three primitives go to the laptop when one is linked")
    seen: list[tuple[str, dict]] = []

    async def fake_forward(op, args, timeout=None):
        seen.append((op, args))
        return json.dumps({"ok": True, "out": "Some Track"})

    real_active = type(PC_LINK).active
    type(PC_LINK).active = property(lambda self: True)
    PC_LINK.forward = fake_forward  # type: ignore[method-assign]
    try:
        src = _tmpfile(b"ID3" + b"\x00" * 2048)
        err = asyncio.run(lp.play_file(str(src), "Some Track"))
        check("play_file dispatches instead of playing here", err is None and seen, str(err))
        check("...as the audio_play op", seen and seen[0][0] == "audio_play", str(seen[:1]))
        payload = seen[0][1] if seen else {}
        check("...carrying the audio bytes", base64.b64decode(payload.get("b64", "")) == src.read_bytes())
        check("...and the spoken label", payload.get("label") == "Some Track", str(payload.get("label")))

        asyncio.run(lp.stop_music({}))
        check("stop_music dispatches", any(o == "audio_stop" for o, _ in seen), str([o for o, _ in seen]))
        asyncio.run(lp.now_playing({}))
        check("now_playing dispatches", any(o == "audio_now" for o, _ in seen), str([o for o, _ in seen]))
        src.unlink(missing_ok=True)

        print("\n[2] the size ceiling matches what the transport can actually carry")
        # pc_agent connects with max_size=8MB and base64 inflates by 4/3, so the raw cap must leave
        # room for the encoded frame — otherwise the transport drops it as an unexplained failure.
        check("cap leaves room after base64", lp.MAX_STREAM_BYTES * 4 / 3 < 8 * 1024 * 1024,
              f"{lp.MAX_STREAM_BYTES}")
        big = _tmpfile(b"\x00" * (lp.MAX_STREAM_BYTES + 1))
        before = len(seen)
        msg = asyncio.run(lp.play_file(str(big), "Huge Track"))
        check("oversize is refused, not truncated", msg is not None and "too large" in msg, str(msg))
        check("...and nothing was sent", len(seen) == before, str(len(seen) - before))
        check("...and it says what to do instead", "phone" in (msg or "").lower(), str(msg))
        big.unlink(missing_ok=True)

        print("\n[3] a missing file is reported, not crashed on")
        gone = asyncio.run(lp.play_file(str(Path(tempfile.gettempdir()) / "nope-not-here.mp3"), "x"))
        check("missing file degrades in prose", gone is not None and "couldn't open" in gone, str(gone))
    finally:
        type(PC_LINK).active = real_active

    print("\n[4] the executor is what actually plays — and tidies up")
    # Runs on this machine, which is the laptop; ffplay may or may not be installed here.
    payload = {"b64": base64.b64encode(b"ID3" + b"\x00" * 4096).decode(), "label": "Exec Track",
               "suffix": ".mp3"}
    got = json.loads(asyncio.run(lp._pc_audio_play(payload)))
    if got.get("ok"):
        check("executor starts a player", got["out"] == "Exec Track", str(got))
        tmp_used = lp._TMP
        check("...writing a real temp file", bool(tmp_used) and Path(tmp_used).exists())
        now = json.loads(asyncio.run(lp._pc_audio_now({})))
        check("...reports what's playing", now["out"] == "Exec Track", str(now))
        stopped = json.loads(asyncio.run(lp._pc_audio_stop({})))
        check("...stop returns the label", stopped["out"] == "Exec Track", str(stopped))
        check("...and the temp file is cleaned up", not Path(tmp_used).exists(), str(tmp_used))
        check("...leaving nothing playing",
              json.loads(asyncio.run(lp._pc_audio_now({})))["out"] == "")
    else:
        check("executor degrades without ffplay", "ffplay" in str(got.get("out", "")), str(got))

    bad = json.loads(asyncio.run(lp._pc_audio_play({"b64": "", "label": "x"})))
    check("empty payload refused at the executor", bad["ok"] is False, str(bad))

    print("\n[5] pc_agent wires the audio ops up")
    import jarvis.edge.pc_agent as agent
    for op in ("audio_play", "audio_stop", "audio_now"):
        check(f"pc_agent executes '{op}'", op in agent.LOCAL_HANDLERS, str(sorted(agent.LOCAL_HANDLERS)))
    check("executor never re-dispatches (would loop brain->laptop->brain)",
          all(lp.LOCAL_HANDLERS[o] is not lp.HANDLERS.get(o) for o in lp.LOCAL_HANDLERS))

    print("\n[6] now_playing is reachable as a tool — the state was tracked but unaskable")
    check("now_playing registered", "now_playing" in tool_names())
    check("now_playing has a handler", "now_playing" in lp.HANDLERS)
    check("stop_music still registered", "stop_music" in tool_names())

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
