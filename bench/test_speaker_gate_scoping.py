"""The speaker gate must identify the UTTERANCE, not the six seconds around it.

Found 2026-08-11 by reading the live gate log (742 decisions, logs/edge.log):

  * the score distribution is ONE smooth hump — mode 0.20-0.25, decaying to 0.64 — with the 0.30
    threshold sitting on its slope. Owner and not-owner are not two populations there, they are one.
  * 186 of the 742 were accepted (25%), and the accepted transcripts include a film playing in the
    room ("Spider Man who watched a certain version", "a scene from two films"), Italian, French and
    German media audio. The gate was forwarding a television as though the owner had said it.

The cause is that the gate embedded its whole 6s rolling window. A 2s utterance was scored together
with 4s of room tone, television and other people, so what the score mostly measured was the noise
floor of the window rather than who spoke. It also cost ~1.1s of ECAPA per turn ON THE CRITICAL PATH
(the transcript is not forwarded until verify() returns) against ~0.6s for a 2s utterance — more,
per turn, than the entire LLM first-token budget.

So: delimit the utterance with the VAD frames the pipeline already emits, keep a little pre-roll
because VAD fires after speech onset, and fall back to the old whole-window behaviour if no VAD
frame ever arrives.

    uv run python bench/test_speaker_gate_scoping.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def main() -> None:
    from pipecat.frames.frames import (InputAudioRawFrame, TranscriptionFrame,
                                       UserStartedSpeakingFrame)
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.utils.time import time_now_iso8601

    from afon.config import settings
    from afon.edge.speaker_gate import SpeakerGate
    from afon.edge.speaker_id import SpeakerVerifier

    SR = 16000
    ref = np.array([1.0, 0.0, 0.0] + [0.0] * 189, dtype=np.float32)

    with tempfile.TemporaryDirectory() as d:
        settings.speaker_profile_path = str(Path(d) / "vp.json")
        SpeakerVerifier.save_profile(ref)
        settings.speaker_id_enabled = True
        settings.speaker_threshold = 0.5
        settings.room_check_on_suspicion = False        # never open a real camera in the bench

        seen: list[int] = []                            # samples handed to ECAPA, per call

        def spy(wav):
            seen.append(len(wav))
            return ref.copy()

        def new_gate():
            gate = SpeakerGate(verifier=SpeakerVerifier(embedder=spy))
            pushed: list = []

            async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
                pushed.append(frame)

            gate.push_frame = _capture                  # type: ignore[assignment]
            return gate, pushed

        async def audio(gate, seconds: float):
            frame = InputAudioRawFrame(audio=b"\x01\x02" * int(SR * seconds),
                                       sample_rate=SR, num_channels=1)
            await gate.process_frame(frame, FrameDirection.DOWNSTREAM)

        async def speech_start(gate):
            await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        async def transcript(gate, text="turn on the lights"):
            await gate.process_frame(TranscriptionFrame(text, "u", time_now_iso8601()),
                                     FrameDirection.DOWNSTREAM)

        print("[1] with VAD: only the utterance is scored, not the window around it")
        gate, pushed = new_gate()
        seen.clear()
        await audio(gate, 4.0)          # four seconds of room, television, someone else
        await speech_start(gate)
        await audio(gate, 1.5)          # the owner speaks
        await transcript(gate)
        check(len(seen) == 1, "the gate ran one verification", str(seen))
        secs = seen[0] / SR
        check(1.4 <= secs <= 2.2, f"...on ~the utterance ({secs:.2f}s), not the 5.5s window",
              f"{secs:.2f}s — the preceding room audio is still being scored as the owner")
        check(secs > 1.5, "...including a little pre-roll (VAD fires after speech onset)",
              f"{secs:.2f}s — the start of his first word is being cut off")
        check(any(isinstance(f, TranscriptionFrame) for f in pushed),
              "the owner's transcript is forwarded")

        print("\n[2] the fallback survives: no VAD frame -> the old whole-window behaviour")
        gate, pushed = new_gate()
        seen.clear()
        await audio(gate, 3.0)
        await transcript(gate)
        check(len(seen) == 1 and abs(seen[0] / SR - 3.0) < 0.2,
              "a pipeline with no VAD still scores the rolling window",
              f"{seen[0] / SR if seen else 0:.2f}s — speaker-id must not silently switch off")

        print("\n[3] a second final transcript in one utterance is not a second ECAPA pass")
        # Deepgram emits several finals per utterance. Re-embedding identical audio costs another
        # ~1s on the turn and buys nothing.
        gate, pushed = new_gate()
        seen.clear()
        await speech_start(gate)
        await audio(gate, 2.0)
        await transcript(gate, "turn on")
        await transcript(gate, "turn on the lights")
        check(len(seen) == 1, "the second final reuses the first verdict", f"{len(seen)} passes")
        check(sum(isinstance(f, TranscriptionFrame) for f in pushed) == 2,
              "...and both transcripts are still forwarded")

        print("\n[4] ...and it re-scores the WHOLE utterance, never the sliver since the last final")
        # A sliver would fall under embed()'s 0.5s floor, and a None embedding makes verify() fail
        # OPEN — a stranger would be admitted by the second half of his own sentence.
        gate, pushed = new_gate()
        seen.clear()
        await speech_start(gate)
        await audio(gate, 2.0)
        await transcript(gate, "turn on")
        await audio(gate, 0.2)                  # a fraction more speech, then another final
        await transcript(gate, "turn on the lights")
        check(len(seen) == 2, "more audio arrived, so it verified again", f"{len(seen)}")
        check(min(seen) / SR > 1.9, "both passes saw the whole utterance",
              f"{[round(s / SR, 2) for s in seen]} — a sliver falls under the 0.5s floor and fails OPEN")

        print("\n[5] a new utterance starts clean (yesterday's audio cannot vouch for today's)")
        gate, pushed = new_gate()
        seen.clear()
        await speech_start(gate)
        await audio(gate, 3.0)
        await transcript(gate, "first")
        await speech_start(gate)
        await audio(gate, 1.0)
        await transcript(gate, "second")
        check(len(seen) == 2, "two utterances, two verifications", str(len(seen)))
        check(seen[1] / SR < 1.6, "the second is scored on its own audio",
              f"{seen[1] / SR:.2f}s — the previous utterance is bleeding into this one")

        print("\n[6] the utterance buffer stays bounded (a monologue must not grow ECAPA cost)")
        gate, pushed = new_gate()
        seen.clear()
        await speech_start(gate)
        for _ in range(6):
            await audio(gate, 3.0)              # 18s of continuous speech
        await transcript(gate)
        check(seen[0] / SR <= 6.5, "capped at the window length",
              f"{seen[0] / SR:.2f}s — cost grows without limit on a long turn")

        print("\n[7] a one-word turn is still checked — 'yes' is what confirms a privileged action")
        # Under ECAPA's 0.5s floor embed() returns None and verify() fails OPEN. Scoping to the
        # utterance must not turn short confirmations into an unchecked path.
        gate, pushed = new_gate()
        seen.clear()
        await audio(gate, 4.0)
        await speech_start(gate)
        await audio(gate, 0.15)                 # "yes"
        await transcript(gate, "yes")
        check(bool(seen) and seen[0] / SR >= 0.5,
              "it widens back to the window rather than skipping the check",
              f"{(seen[0] / SR) if seen else 0:.2f}s — under the floor, verify() fails OPEN")

        print("\n[8] the stranger is still dropped (the point of the whole processor)")
        other = np.array([0.0, 1.0, 0.0] + [0.0] * 189, dtype=np.float32)
        gate = SpeakerGate(verifier=SpeakerVerifier(embedder=lambda wav: other.copy()))
        pushed = []

        async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        gate.push_frame = _capture              # type: ignore[assignment]
        await speech_start(gate)
        await audio(gate, 2.0)
        await transcript(gate, "delete everything")
        check(not any(isinstance(f, TranscriptionFrame) for f in pushed),
              "a voice that is not the owner's never reaches the brain")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
