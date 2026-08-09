"""C3 (edge) hermetic test — AffectTTS pushes a voice-settings update per owner mood (no audio).

Asserts the live-path wire: a stressed utterance emits a TTSUpdateSettingsFrame with the calm profile,
an upbeat one emits the livelier profile, the frame is pushed DOWNSTREAM (toward the TTS), and an
unchanged mood does NOT re-emit (near-silent). The affect→numbers themselves are covered by
test_affect_voice; here we test the processor's behaviour.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipecat.frames.frames import TranscriptionFrame, TTSUpdateSettingsFrame  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from afon.brain.affect import affect_to_voice, infer_affect  # noqa: E402
from afon.edge.affect_tts import AffectTTS  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


def _transcript(text):
    return TranscriptionFrame(text=text, user_id="owner", timestamp="")


async def drive(texts):
    """Run an AffectTTS over a list of transcripts; capture pushed (frame, direction) pairs."""
    p = AffectTTS()
    pushed = []

    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    p.push_frame = fake_push  # type: ignore[assignment]

    async def noop(*_a, **_k):
        return None

    # bypass FrameProcessor.process_frame setup (needs a running pipeline)
    import pipecat.processors.frame_processor as fp
    orig = fp.FrameProcessor.process_frame
    fp.FrameProcessor.process_frame = noop  # type: ignore[assignment]
    try:
        for t in texts:
            await p.process_frame(_transcript(t), FrameDirection.DOWNSTREAM)
    finally:
        fp.FrameProcessor.process_frame = orig  # type: ignore[assignment]
    return pushed


async def main():
    # 1) a stressed utterance -> a settings update with the calm profile, pushed downstream
    pushed = await drive(["this is still broken and I'm so frustrated"])
    updates = [f for f, _ in pushed if isinstance(f, TTSUpdateSettingsFrame)]
    check(len(updates) == 1, f"stressed utterance emits one settings update (got {len(updates)})")
    want = affect_to_voice(infer_affect("this is still broken and I'm so frustrated"))
    check(updates and dict(updates[0].settings) == want, f"settings match the calm profile (got {updates[0].settings if updates else None})")
    dirs = [d for f, d in pushed if isinstance(f, TTSUpdateSettingsFrame)]
    check(dirs and dirs[0] == FrameDirection.DOWNSTREAM, "settings update goes DOWNSTREAM (toward the TTS)")
    # the transcript itself is still forwarded
    check(any(isinstance(f, TranscriptionFrame) for f, _ in pushed), "the transcript still passes through")

    # 2) same mood twice -> only ONE update (change-filtered, near-silent)
    pushed2 = await drive(["ugh not working again", "still not working, seriously"])
    ups2 = [f for f, _ in pushed2 if isinstance(f, TTSUpdateSettingsFrame)]
    check(len(ups2) == 1, f"unchanged mood does not re-emit (got {len(ups2)} updates)")

    # 3) mood change -> a new update with the different (livelier) profile
    pushed3 = await drive(["ugh not working", "amazing, we finally nailed it!"])
    ups3 = [f for f, _ in pushed3 if isinstance(f, TTSUpdateSettingsFrame)]
    check(len(ups3) == 2, f"a mood change emits a fresh update (got {len(ups3)})")
    check(ups3 and dict(ups3[-1].settings) == affect_to_voice(infer_affect("amazing, we finally nailed it!")),
          "the second update carries the upbeat profile")


asyncio.run(main())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
