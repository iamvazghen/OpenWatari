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
    from afon.config import settings

    print("[1] speaker gate decision logic (pure)")
    from afon.edge.speaker_id import cosine, should_accept

    check("accepts when feature OFF", should_accept(0.0, 0.25, True, False) is True)
    check("accepts when no profile", should_accept(0.0, 0.25, False, True) is True)
    check("accepts above threshold", should_accept(0.40, 0.25, True, True) is True)
    check("rejects below threshold", should_accept(0.10, 0.25, True, True) is False)
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    check("cosine identical = 1.0", abs(cosine(v, v) - 1.0) < 1e-6)
    check("cosine orthogonal = 0.0", abs(cosine(v, np.array([0.0, 1.0, 0.0], dtype=np.float32))) < 1e-6)

    print("\n[2] SpeakerVerifier degradation (no backend / no profile)")
    from afon.edge.speaker_id import SpeakerVerifier

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

    from afon.edge.speaker_gate import SpeakerGate

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

    derived_threshold()

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


# -- 10.F3: the accept bar is DERIVED from this profile's measured band, not set by hand -----------
# The 0.30 default was itself a derivation — owner 0.34-0.45 against television and guests 0.16-0.31,
# measured live on 2026-07-25 — done once, by hand, and then left in place while the profile it
# described was replaced twice. Cosine scores are not comparable across profiles (different mic,
# different room, different acoustic conditions covered), so one number for all of them is a number
# that is right for at most one of them.
def derived_threshold() -> None:
    from afon.config import settings
    from afon.edge.speaker_id import (MIN_DERIVED, MIN_SEPARATION, SpeakerVerifier,
                                      derive_threshold)

    print("\n[10.F3] the bar is computed, and lands inside the measured band")
    saved_path, saved_thr, saved_on = (settings.speaker_profile_path, settings.speaker_threshold,
                                       settings.speaker_id_enabled)
    # The positive case first. A derivation that refuses every band is passed by `return fallback`,
    # and then this task has changed nothing at all.
    bar, why = derive_threshold(0.62, 0.20, 0.30)
    check(f"a clean band yields a bar inside it ({bar:.2f})", 0.20 < bar < 0.62, why)
    check("...and it is the midpoint, the only placement two point measurements support",
          abs(bar - 0.41) < 1e-6, f"{bar}")
    check("...and it says where the number came from", "derived" in why and "0.62" in why, why)

    # A better owner score must never LOWER the bar, and a louder impostor must never raise it above
    # the owner. Property checks: they catch a sign error that any single fixture would sail past.
    bars = [derive_threshold(o, 0.20, 0.30)[0] for o in (0.40, 0.50, 0.60, 0.70, 0.80)]
    check("a stronger profile never lowers the bar", bars == sorted(bars), str(bars))
    check("the bar always stays under the owner's own score",
          all(b < o for b, o in zip(bars, (0.40, 0.50, 0.60, 0.70, 0.80))), str(bars))

    print("\n[10.F3] and it refuses, rather than inventing a number the data cannot carry")
    narrow, why_n = derive_threshold(0.47, 0.42, 0.30)
    check(f"a band narrower than {MIN_SEPARATION} keeps the setting", narrow == 0.30, why_n)
    check("...and says to re-enrol, not to move the bar",
          "re-enrol" in why_n.lower() and "No bar splits" in why_n, why_n)
    low, why_l = derive_threshold(0.22, 0.02, 0.30)
    check("a band entirely below the floor keeps the setting too", low == 0.30, why_l)
    check("...because any safe bar there would lock the owner out", "lock you out" in why_l, why_l)
    check("the two refusals do not share a message", why_n != why_l)
    clamped, why_c = derive_threshold(0.44, 0.10, 0.30)   # midpoint 0.27, under the floor
    check(f"a midpoint under the {MIN_DERIVED} floor sits on the floor instead",
          clamped == MIN_DERIVED, why_c)
    check("...and reports the headroom that is left", "headroom" in why_c, why_c)

    print("\n[10.F3] the recorded band survives the disk, and DECIDES")
    with tempfile.TemporaryDirectory() as d:
        settings.speaker_profile_path = str(Path(d) / "vp.json")
        settings.speaker_id_enabled = True
        settings.speaker_threshold = 0.30          # the constant this replaces
        ref = np.array([1.0] + [0.0] * 191, dtype=np.float32)
        SpeakerVerifier.save_profile(ref)

        fresh = SpeakerVerifier(embedder=lambda wav: ref.copy())
        check("a profile with no measured band falls back to the setting",
              fresh.accept_bar()[0] == 0.30, str(fresh.accept_bar()))
        check("...and says so, with what to run to fix it",
              "no measured separation" in fresh.accept_bar()[1]
              and "enroll_voice" in fresh.accept_bar()[1], fresh.accept_bar()[1])

        # A voice at 0.35: ACCEPTED under the 0.30 constant, REJECTED under a bar derived from a
        # 0.20-0.62 band (0.41). If the derivation cannot flip a verdict it is decoration.
        marginal = np.array([0.35, float(np.sqrt(1 - 0.35 ** 2))] + [0.0] * 190, dtype=np.float32)
        v_const = SpeakerVerifier(embedder=lambda wav: marginal.copy())
        accept_const, score = v_const.verify(b"\x01\x02" * 16000)
        check(f"a {score:.2f} voice passes the hand-set 0.30 constant", accept_const, f"{score:.2f}")

        SpeakerVerifier.record_separation(0.62, 0.20)
        v_derived = SpeakerVerifier(embedder=lambda wav: marginal.copy())
        check("the band round-trips through the profile file", v_derived.separation == (0.62, 0.20),
              str(v_derived.separation))
        check("the bar moved to the measured midpoint", abs(v_derived.accept_bar()[0] - 0.41) < 1e-6,
              str(v_derived.accept_bar()))
        accept_derived, _ = v_derived.verify(b"\x01\x02" * 16000)
        check("...and the SAME voice is now refused — the derivation actually decides",
              not accept_derived, "the bar is computed but nothing consults it")

        # The owner himself must still get in, on the same profile and the same bar.
        v_owner = SpeakerVerifier(embedder=lambda wav: ref.copy())
        check("the owner still passes the derived bar", v_owner.verify(b"\x01\x02" * 16000)[0])

        # Re-enrolling replaces the vectors, which makes the old band a measurement of something that
        # no longer exists. Reusing it is the same class of bug as the stale constant, one file down.
        SpeakerVerifier.save_profile(np.stack([ref, marginal]), append=True)
        after = SpeakerVerifier(embedder=lambda wav: ref.copy())
        check("re-enrolling DROPS the old band instead of reusing it against new vectors",
              after.separation is None, str(after.separation))
        check("...and falls back to the setting until the new band is measured",
              after.accept_bar()[0] == 0.30, str(after.accept_bar()))

        print("\n[10.F3] the borderline face-check margin follows the same band")
        from afon.edge.speaker_gate import _BORDERLINE_MARGIN, _borderline_margin
        wide = SpeakerVerifier(embedder=lambda wav: ref.copy())
        check("no band -> the original constant margin", _borderline_margin(wide)
              == _BORDERLINE_MARGIN, str(_borderline_margin(wide)))
        SpeakerVerifier.record_separation(0.47, 0.42)      # a 0.05 band, as narrow as the margin
        tight = SpeakerVerifier(embedder=lambda wav: ref.copy())
        m = _borderline_margin(tight)
        check(f"a band as narrow as the margin shrinks it ({m:.3f})", m < _BORDERLINE_MARGIN,
              "a margin wider than a third of the band makes EVERY accept borderline, and then the "
              "camera opens on every turn")
        check("...but never to zero, which would disable the second factor silently", m >= 0.01,
              f"{m}")

    print("\n[10.F3] the owner number is his WORST short turn, not his best six seconds")
    # The bar is only as honest as the two numbers it is derived from. The enrolment clip is the owner
    # reading deliberately, seconds after enrolling; a live turn is one word. Deriving from the 6s
    # score puts the bar above his typical turn, which is precisely the false-reject TODO I1 records
    # (0.29 rejected, the same phrase accepted at 0.36 seconds later).
    from afon.edge.speaker_id import FLOOR_WINDOW_S
    with tempfile.TemporaryDirectory() as d:
        settings.speaker_profile_path = str(Path(d) / "vp.json")
        settings.speaker_id_enabled = True
        ref = np.array([1.0] + [0.0] * 191, dtype=np.float32)
        SpeakerVerifier.save_profile(ref)

        # A clip that starts strong and degrades: window 1 is the owner, the last is much weaker.
        # A stub keyed on the audio itself, so window order — not call order — decides the score.
        strong, weak = ref.copy(), np.array([0.45, float(np.sqrt(1 - 0.45 ** 2))] + [0.0] * 190,
                                           dtype=np.float32)
        sr = 16000
        half = int(3.0 * sr) * 2
        clip = b"\x00\x40" * (half // 2) + b"\x01\x00" * (half // 2)   # loud half, then near-silent

        def _by_content(wav):
            return weak.copy() if abs(float(np.mean(np.abs(wav)))) < 0.001 else strong.copy()

        v = SpeakerVerifier(embedder=_by_content)
        wins = v.window_scores(clip, sr)
        # Exactly five: 6s of audio, 2s windows, 1s hop, starting at 0,1,2,3,4s. Stated as a number
        # rather than recomputed from the constants — a check that re-derives the implementation
        # agrees with the implementation by construction and catches nothing.
        check(f"a 6s clip yields exactly 5 overlapping {FLOOR_WINDOW_S:.0f}s windows ({len(wins)})",
              len(wins) == 5, str(wins))
        check("the worst window is well below the best", min(wins) < max(wins) - 0.5,
              f"{min(wins):.2f}..{max(wins):.2f}")
        whole = v.score(clip, sr)
        check("...and below the score of the whole clip, which is what used to be recorded",
              min(wins) < whole, f"windows {min(wins):.2f}, whole clip {whole:.2f}")

        # The load-bearing consequence: the two choices give DIFFERENT bars. If they did not, the
        # asymmetry would be a comment rather than a mechanism.
        best_bar = derive_threshold(max(wins), 0.20, 0.30)[0]
        worst_bar = derive_threshold(min(wins), 0.20, 0.30)[0]
        check("deriving from his best window sets a higher bar than from his worst",
              best_bar > worst_bar, f"best->{best_bar}, worst->{worst_bar}")
        check("...and the worst-window bar is the one below his weakest turn",
              worst_bar < min(wins), f"bar {worst_bar} vs weakest turn {min(wins):.2f}")

        short = v.window_scores(b"\x00\x40" * 800, sr)   # 0.1s: shorter than one window
        check("a clip shorter than one window yields no windows rather than a bogus one",
              short == [], str(short))
        # The trailing remainder is the case that needs the bound: 0.5s is exactly embed()'s minimum,
        # so a half-second tail IS scoreable — and a score off half a second of audio is noise that
        # would drag the owner's floor down and, through it, the bar. Only whole windows count.
        ragged = v.window_scores(clip + b"\x00\x40" * (sr // 4), sr)   # 6.5s: a 0.5s remainder
        check("a partial trailing window is dropped, not scored short",
              len(ragged) == 5, f"{len(ragged)} windows from 6.5s — the 0.5s remainder was scored")
        check("an empty clip does not crash the sampler", v.window_scores(b"", sr) == [])
        no_backend = SpeakerVerifier(embedder=lambda wav: None)
        check("no backend -> no windows, and score() says None not zero",
              no_backend.window_scores(clip, sr) == [] and no_backend.score(clip, sr) is None)

    # Structural: enrolment must pass the MIN of the owner's windows and the MAX of the impostor's.
    # Swapping either one silently biases every future bar, and no mic-free test can catch it live.
    enroll_src = (Path(__file__).resolve().parents[1] / "bench" / "enroll_voice.py").read_text("utf-8")
    check("enrolment records the owner's worst window", "min(own_w) if own_w else score" in enroll_src)
    check("...and the impostor's best", "max(imp_w) if imp_w else imp" in enroll_src)
    check("...and hands exactly those two to the derivation",
          "record_separation(owner_floor, impostor_ceiling)" in enroll_src
          and "derive_threshold(owner_floor, impostor_ceiling" in enroll_src)

    print("\n[10.F3] one module owns the number")
    # The constant was read in two modules independently, which is why raising it in one place on
    # 2026-07-25 left the gate's borderline check comparing against the other. Same lesson as 30.F1.
    src = Path(__file__).resolve().parents[1] / "src" / "afon"
    mentions = {p.name for p in src.rglob("*.py") if "speaker_threshold" in p.read_text("utf-8")}
    check("only speaker_id.py reads the threshold setting (config.py declares it)",
          mentions == {"config.py", "speaker_id.py"}, str(sorted(mentions)))

    settings.speaker_profile_path, settings.speaker_threshold = saved_path, saved_thr
    settings.speaker_id_enabled = saved_on


if __name__ == "__main__":
    asyncio.run(main())
