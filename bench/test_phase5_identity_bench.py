"""Phase 5 — speaker biometrics + TTFW/VAQI benchmarks (offline, no torch/mic needed).

Verifies the gate DECISION logic, the verifier's graceful degradation, the SpeakerGate processor
(drop a stranger / pass Vazghen using a stub embedder), and the TTFW/VAQI math.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


async def main() -> None:
    from jarvis.config import settings

    print("[1] speaker gate decision logic (pure)")
    from jarvis.edge.speaker_id import cosine, should_accept

    check("accepts when feature OFF", should_accept(0.0, 0.25, True, False) is True)
    check("accepts when no profile", should_accept(0.0, 0.25, False, True) is True)
    check("accepts above threshold", should_accept(0.40, 0.25, True, True) is True)
    check("rejects below threshold", should_accept(0.10, 0.25, True, True) is False)
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    check("cosine identical = 1.0", abs(cosine(v, v) - 1.0) < 1e-6)
    check("cosine orthogonal = 0.0", abs(cosine(v, np.array([0.0, 1.0, 0.0], dtype=np.float32))) < 1e-6)

    print("\n[2] SpeakerVerifier degradation (no backend / no profile)")
    from jarvis.edge.speaker_id import SpeakerVerifier

    # No profile enrolled, feature off -> always accept, score 1.0.
    settings.speaker_id_enabled = False
    settings.speaker_profile_path = str(Path(tempfile.gettempdir()) / "no_such_voiceprint.json")
    sv = SpeakerVerifier(embedder=lambda wav: np.zeros(192, dtype=np.float32))
    accept, score = sv.verify(b"\x00\x00" * 16000)
    check("no-profile -> accept", accept is True and score == 1.0)
    check("has_profile is False", sv.has_profile is False)

    # With a profile + enabled + a stub embedder, the score decides.
    with tempfile.TemporaryDirectory() as d:
        settings.speaker_profile_path = str(Path(d) / "vp.json")
        ref = np.array([1.0, 0.0, 0.0] + [0.0] * 189, dtype=np.float32)
        SpeakerVerifier.save_profile(ref)
        settings.speaker_id_enabled = True
        settings.speaker_threshold = 0.5
        settings.room_check_on_suspicion = False  # hermetic: never open a real camera in the bench

        # Stub embedder returns Vazghen's vector -> match.
        sv_match = SpeakerVerifier(embedder=lambda wav: ref.copy())
        a, s = sv_match.verify(b"\x01\x02" * 16000)
        check("enrolled voice accepted", a is True and s > 0.99, f"score={s}")

        # Stub embedder returns an orthogonal vector -> stranger rejected.
        other = np.array([0.0, 1.0, 0.0] + [0.0] * 189, dtype=np.float32)
        sv_other = SpeakerVerifier(embedder=lambda wav: other.copy())
        a, s = sv_other.verify(b"\x01\x02" * 16000)
        check("stranger rejected", a is False, f"score={s}")

        # Resample regression: a 48 kHz mic must reach ECAPA at 16 kHz (else the embedding is garbage
        # and the OWNER is wrongly rejected — the real bug). The stub records the wav length it gets.
        seen: dict = {}

        def _spy(wav):
            seen["n"] = len(wav)
            return ref.copy()

        sv_rs = SpeakerVerifier(embedder=_spy)
        sv_rs.embed(b"\x01\x02" * 48000, sample_rate=48000)  # 1s @ 48k -> must arrive ~16k samples
        check("48k resampled to ~16k", abs(seen.get("n", 0) - 16000) < 800, f"got {seen.get('n')}")
        sv_rs.embed(b"\x01\x02" * 16000, sample_rate=16000)  # 16k passes through unchanged
        check("16k passes through", seen["n"] == 16000, f"got {seen['n']}")

    print("\n[3] SpeakerGate processor (drop stranger / pass Vazghen)")
    from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.utils.time import time_now_iso8601

    from jarvis.edge.speaker_gate import SpeakerGate

    with tempfile.TemporaryDirectory() as d:
        settings.speaker_profile_path = str(Path(d) / "vp.json")
        ref = np.array([1.0, 0.0, 0.0] + [0.0] * 189, dtype=np.float32)
        SpeakerVerifier.save_profile(ref)
        settings.speaker_id_enabled = True
        settings.speaker_threshold = 0.5
        settings.room_check_on_suspicion = False  # hermetic: never open a real camera in the bench

        async def run_gate(embedder) -> list:
            gate = SpeakerGate(verifier=SpeakerVerifier(embedder=embedder))
            pushed: list = []

            async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
                pushed.append(frame)

            gate.push_frame = _capture  # type: ignore[assignment]
            # Feed ~1s of audio then a transcript.
            audio = InputAudioRawFrame(audio=b"\x01\x02" * 16000, sample_rate=16000, num_channels=1)
            await gate.process_frame(audio, FrameDirection.DOWNSTREAM)
            tf = TranscriptionFrame("turn on the lights", "u", time_now_iso8601())
            await gate.process_frame(tf, FrameDirection.DOWNSTREAM)
            return pushed

        pushed_match = await run_gate(lambda wav: ref.copy())
        check("Vazghen's transcript passes", any(isinstance(f, TranscriptionFrame) for f in pushed_match))

        other = np.array([0.0, 1.0, 0.0] + [0.0] * 189, dtype=np.float32)
        pushed_stranger = await run_gate(lambda wav: other.copy())
        check("stranger's transcript dropped",
              not any(isinstance(f, TranscriptionFrame) for f in pushed_stranger))
        check("audio still passes through (not gated)",
              any(isinstance(f, InputAudioRawFrame) for f in pushed_stranger))

        # Room-check policy: a stranger's voice triggers exactly ONE camera look per cooldown.
        settings.room_check_on_suspicion = True
        looks: list = []
        gate2 = SpeakerGate(verifier=SpeakerVerifier(embedder=lambda wav: other.copy()))

        async def _spy_look(reason):
            looks.append(reason)

        gate2._room_check = _spy_look  # type: ignore[assignment]

        async def _swallow(frame, direction=FrameDirection.DOWNSTREAM):
            pass

        gate2.push_frame = _swallow  # type: ignore[assignment]
        audio = InputAudioRawFrame(audio=b"\x01\x02" * 16000, sample_rate=16000, num_channels=1)
        await gate2.process_frame(audio, FrameDirection.DOWNSTREAM)
        for _ in range(3):  # three stranger utterances in quick succession
            tf = TranscriptionFrame("who are you", "u", time_now_iso8601())
            await gate2.process_frame(tf, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)  # let the fire-and-forget task run
        check("stranger voice triggers a camera look", looks == ["unrecognized voice in the room"],
              str(looks))
        check("cooldown: repeated strangers do NOT strobe the camera", len(looks) == 1, str(looks))

        # A BARELY-passing accept (within 0.05 of the threshold) earns a face check too — the
        # second factor for the owner-floor == impostor-ceiling overlap seen in production.
        borderline = np.array([0.52, float(np.sqrt(1 - 0.52 ** 2)), 0.0] + [0.0] * 189,
                              dtype=np.float32)
        looks3: list = []
        gate3 = SpeakerGate(verifier=SpeakerVerifier(embedder=lambda wav: borderline.copy()))

        async def _spy3(reason):
            looks3.append(reason)

        gate3._room_check = _spy3  # type: ignore[assignment]
        gate3.push_frame = _swallow  # type: ignore[assignment]
        await gate3.process_frame(audio, FrameDirection.DOWNSTREAM)
        await gate3.process_frame(TranscriptionFrame("hello", "u", time_now_iso8601()),
                                  FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        check("borderline accept triggers a face check", any("borderline" in r for r in looks3),
              str(looks3))
        settings.room_check_on_suspicion = False

        # Multi-condition profile: vectors for two acoustic modes, verify() takes the BEST match —
        # the 2026-07-29 fix for "AirPods connected degrades the array and locks the owner out".
        cond_a = np.array([1.0, 0.0, 0.0] + [0.0] * 189, dtype=np.float32)
        cond_b = np.array([0.0, 1.0, 0.0] + [0.0] * 189, dtype=np.float32)
        SpeakerVerifier.save_profile(np.stack([cond_a, cond_b]))
        v_multi = SpeakerVerifier(embedder=lambda wav: cond_b.copy())  # speaks in condition B
        ok_b, score_b = v_multi.verify(b"\x01\x02" * 16000, 16000)
        check("multi-vector profile: condition-B voice matches via MAX cosine",
              ok_b and score_b > 0.99, f"{score_b:.2f}")
        legacy = SpeakerVerifier.save_profile(cond_a)  # 1-vector legacy save still works
        v_one = SpeakerVerifier(embedder=lambda wav: cond_a.copy())
        ok_a, _ = v_one.verify(b"\x01\x02" * 16000, 16000)
        check("single-vector (legacy) profile still verifies", ok_a)
        # append mode: a second enrollment ADDS coverage instead of clobbering
        SpeakerVerifier.save_profile(cond_b, append=True)
        v_both = SpeakerVerifier(embedder=lambda wav: cond_b.copy())
        ok_ap, _ = v_both.verify(b"\x01\x02" * 16000, 16000)
        check("append-mode enrollment keeps the old condition AND adds the new", ok_ap)
        check("previous profile backed up (.bak) before overwrite",
              (Path(settings.speaker_profile_path).with_suffix(".json.bak")).exists())

    settings.speaker_id_enabled = False  # restore

    print("\n[4] TTFW + VAQI benchmark math")
    from bench.benchmarks import TTFW, Turn, format_report, vaqi

    t = TTFW()
    for ms in (800, 1000, 1200, 900, 1100):
        t.record(ms)
    s = t.summary()
    check("TTFW count", s["count"] == 5)
    check("TTFW mean correct", abs(s["mean"] - 1000.0) < 1e-6, str(s["mean"]))
    check("TTFW p95 <= max", s["p95"] <= s["max"])

    # Perfect battery: fast, all responded, no false interruptions -> high VAQI.
    good = [Turn(ttfw_ms=900) for _ in range(10)]
    q_good = vaqi(good, target_ms=1200)
    check("good battery responsiveness=1", q_good["responsiveness"] == 1.0)
    check("good battery latency saturates at 1.0", q_good["latency"] == 1.0)
    check("good battery VAQI == 100", q_good["vaqi"] == 100.0, str(q_good["vaqi"]))

    # Degraded: 2 misses + 1 false interruption + slow -> lower VAQI.
    bad = [Turn(ttfw_ms=3000) for _ in range(7)]
    bad += [Turn(ttfw_ms=None, responded=False) for _ in range(2)]
    bad += [Turn(ttfw_ms=3000, false_interruption=True)]
    q_bad = vaqi(bad, target_ms=1200)
    check("bad battery VAQI lower than good", q_bad["vaqi"] < q_good["vaqi"], str(q_bad["vaqi"]))
    check("bad battery missed_rate=0.2", abs(q_bad["missed_rate"] - 0.2) < 1e-6, str(q_bad["missed_rate"]))
    check("empty battery VAQI=0", vaqi([], 1200)["vaqi"] == 0.0)

    rep = format_report(t, good, 1200)
    check("report renders TTFW + VAQI", "TTFW" in rep and "VAQI" in rep)
    print("\n" + rep)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
