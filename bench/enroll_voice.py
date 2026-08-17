"""Enroll Vazghen's voice for speaker biometrics (Phase 5).

Records a few short clips from the mic, averages their ECAPA embeddings into one voiceprint, and
saves it to AFON_SPEAKER_PROFILE (default <repo>/voiceprint.json). After this, set
AFON_SPEAKER_ID_ENABLED=true and Afon will obey only your voice.

Needs the 'identity' extra:  uv sync --extra identity
Run:  uv run python bench/enroll_voice.py
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

from afon.config import settings
from afon.edge.enroll_quality import score_pcm
from afon.edge.speaker_id import SpeakerVerifier

SR = 16000
CLIP_S = 4
N_CLIPS = 3
PROMPTS = [
    "Say: 'Hey Afon, this is Vazghen.'",
    "Say any sentence in your normal voice.",
    "Say one more sentence, a little longer.",
]
# When reading the 3-minute script, capture one longer clip per '## Segment' block.
SCRIPT_CLIP_S = 40


def _script_segments(path: Path) -> list[tuple[str, int]]:
    """Parse 'to-read-script.md' into (prompt, seconds) per '## Segment' header.

    Pulls the '(≈ N s)' hint from each header to size the recording; falls back to SCRIPT_CLIP_S.
    """
    text = path.read_text(encoding="utf-8")
    segs: list[tuple[str, int]] = []
    for m in re.finditer(r"^##\s+(Segment[^\n]*)", text, re.MULTILINE):
        header = m.group(1).strip()
        sec = SCRIPT_CLIP_S
        # Accept ≈ / ~ / "approx" / nothing before the number. Requiring the non-ASCII ≈ made the
        # whole thing fail SILENTLY: swap it for a '~' (an editor, a lossy paste) and every header
        # still matched, so enrolment still ran — just with default-length clips, producing a
        # weaker voiceprint with no error to notice. See bench/test_enroll_script_parse.py.
        hint = re.search(r"(?:≈|~|approx\.?)?\s*(\d+)\s*s\b", header, re.IGNORECASE)
        if hint:
            sec = int(hint.group(1)) + 3  # small buffer so the tail isn't clipped
        segs.append((f"Read aloud: '{header}'", sec))
    return segs


def separation_verdict(owner: float, impostor: float, threshold: float) -> str:
    """Judge the gate as a SEPARATION rather than as two independent scores.

    A profile can score the owner well and still be useless: what decides whether the gate works is
    the GAP between him and everything else, and whether the threshold sits inside it. The three
    failing shapes are genuinely different problems with different fixes, so they get different
    sentences rather than one "check your setup".
    """
    gap = owner - impostor
    mid = (owner + impostor) / 2
    # Order matters: a too-small GAP and a misplaced THRESHOLD look identical from the accept/reject
    # outcome alone, and the fixes are opposite — one needs a re-enrolment, the other needs one
    # number changed. Diagnose the gap first, then where the bar sits inside it.
    if gap < 0.10:
        return (f"✗ NO USABLE SEPARATION — you scored {owner:.2f} and the impostor {impostor:.2f}, a "
                f"gap of {gap:+.2f}. No threshold can split that, so the gate would forward a "
                "television as though you had said it. Re-enrol on the mic you actually use, in the "
                "room you actually use.")
    if owner < threshold:
        return (f"✗ the threshold is above YOU ({owner:.2f} < {threshold:.2f}) — Afon would ignore "
                f"you. There IS a {gap:.2f} gap, so lower AFON_SPEAKER_THRESHOLD to about {mid:.2f} "
                "rather than re-enrolling.")
    if impostor >= threshold:
        return (f"✗ the threshold is below the IMPOSTOR ({impostor:.2f} >= {threshold:.2f}) — it "
                f"would be let through. There IS a {gap:.2f} gap, so RAISE AFON_SPEAKER_THRESHOLD to "
                f"about {mid:.2f}; the profile is fine.")
    room = "well placed" if abs(threshold - mid) <= gap / 3 else f"better placed near {mid:.2f}"
    return f"✓ separated by {gap:.2f}, and the {threshold:.2f} threshold is {room}."


def _find_input_device(pa, hint: str = "airpods"):
    """Pick the first input device whose name contains `hint` (case-insensitive).
    Falls back to the default input device if no match.
    """
    hint_l = hint.lower()
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) <= 0:
            continue
        name = (info.get("name") or "").lower()
        if hint_l in name:
            return i
    try:
        return pa.get_default_input_device_info()["index"]
    except Exception:
        return None


def _record(seconds: int) -> bytes:
    import pyaudio

    pa = pyaudio.PyAudio()
    # Enroll on the SAME mic the edge runs on (settings.audio_input_name, e.g. "microphone array") —
    # NOT AirPods. The runtime deliberately uses the built-in array (AirPods HFP mic degrades quality),
    # so an AirPods-enrolled voiceprint mismatches the far-field runtime audio and depresses scores.
    device_index = _find_input_device(pa, (settings.audio_input_device_name or "microphone array"))
    kwargs = dict(format=pyaudio.paInt16, channels=1, rate=SR, input=True,
                  frames_per_buffer=1024)
    if device_index is not None:
        kwargs["input_device_index"] = device_index
        name = pa.get_device_info_by_index(device_index).get("name")
        print(f"  (using mic: [{device_index}] {name})")
    stream = pa.open(**kwargs)
    frames = []
    for _ in range(int(SR / 1024 * seconds)):
        frames.append(stream.read(1024, exception_on_overflow=False))
    stream.stop_stream()
    stream.close()
    pa.terminate()
    return b"".join(frames)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enroll Vazghen's voiceprint.")
    ap.add_argument("--script", type=str, default=None,
                    help="Path to to-read-script.md for a longer, stronger 3-minute enrollment.")
    ap.add_argument("--append", action="store_true",
                    help="ADD these clips to the existing profile (e.g. re-enroll with AirPods "
                         "connected to cover that acoustic condition) instead of replacing it.")
    args = ap.parse_args()

    verifier = SpeakerVerifier()
    if verifier._ensure_embedder() is None:  # noqa: SLF001
        print("ECAPA backend not available. Install it:  uv sync --extra identity")
        sys.exit(1)

    if args.script:
        spath = Path(args.script)
        if not spath.is_file():
            spath = Path(__file__).resolve().parents[1] / args.script
        if not spath.is_file():
            print(f"Script not found: {args.script}")
            sys.exit(1)
        clips = _script_segments(spath)
        print(f"Reading {spath.name} — {len(clips)} segments, ~3 minutes total.\n")
    else:
        clips = [(PROMPTS[i], CLIP_S) for i in range(N_CLIPS)]

    embeddings = []
    rejected: list[str] = []
    for i, (prompt, clip_s) in enumerate(clips):
        print(f"\n[{i + 1}/{len(clips)}] {prompt}")
        for c in (3, 2, 1):
            print(f"  recording in {c}…", end="\r", flush=True)
            time.sleep(1)
        print(f"  ● recording {clip_s}s — speak now")
        pcm = _record(clip_s)
        # 08.F2 — judge the clip NOW. The profile scores 0.47 against a 0.60 target and nothing ever
        # said why; a segment read next to a running fan, or with the wrong mic selected, went in
        # looking exactly like a good one. One retry per segment, then keep it with a warning: the
        # owner is sitting at the mic for five minutes and a script that refuses forever is a script
        # that gets abandoned half-enrolled, which is worse than a slightly noisy vector.
        q = score_pcm(pcm, SR)
        print(f"  quality: {q.summary()}")
        if q.reject:
            print(f"  ✗ {q.reason}")
            print("  → re-recording this segment once.")
            for c in (3, 2, 1):
                print(f"  recording in {c}…", end="\r", flush=True)
                time.sleep(1)
            print(f"  ● recording {clip_s}s — speak now")
            pcm = _record(clip_s)
            q2 = score_pcm(pcm, SR)
            print(f"  quality: {q2.summary()}")
            if q2.reject:
                print(f"  ~ still {q2.reason.split(' —')[0]} — keeping it, but this segment is "
                      f"weakening the profile.")
                rejected.append(f"[{i + 1}] {q2.reason}")
        emb = verifier.embed(pcm, SR)
        if emb is None:
            print("  (clip too short / failed, retrying)")
            continue
        embeddings.append(emb / (np.linalg.norm(emb) or 1.0))
        print("  captured ✓")

    if not embeddings:
        print("No usable clips captured.")
        sys.exit(1)

    # Say it once more at the end. A warning printed four minutes ago, above four screens of
    # segments, is a warning nobody acts on — and the whole point of 08.F2 is that a weak profile is
    # discovered at the mic rather than in a threshold nobody can place weeks later.
    if rejected:
        print(f"\n! {len(rejected)} of {len(clips)} segment(s) went in below quality:")
        for line in rejected:
            print(f"    {line}")
        print("  Re-run enrolment in a quieter room, or with the mic closer, before trusting the "
              "separation numbers below.")

    # One vector PER CLIP (max-cosine at verify time), not a blurred mean — a mean profile could
    # not cover the mic's two acoustic modes (Bluetooth audio active vs not) and locked the owner
    # out at 0.05-0.28 in production (2026-07-29). --append keeps prior conditions' vectors.
    path = SpeakerVerifier.save_profile(np.stack(embeddings), append=args.append)
    mode = "appended to" if args.append else "saved to"
    print(f"\nVoiceprint {mode} {path} ({len(embeddings)} vector(s) from this session).")

    # Verification pass: prove the profile matches the LIVE mic before calling it done. Without
    # this, a bad enrollment is only discovered when Afon goes deaf to the owner.
    print("\n--- verification: say naturally, e.g. 'Hey Afon, how are you today?'")
    for c in (3, 2, 1):
        print(f"  recording in {c}…", end="\r", flush=True)
        time.sleep(1)
    print("  ● recording 6s — speak now")
    pcm = _record(6)
    fresh = SpeakerVerifier()          # reload from disk — verifies what was actually saved
    from afon.config import settings as _s
    _s.speaker_id_enabled = True       # force a real score even if the .env flag is off
    accept, score = fresh.verify(pcm, SR)
    print(f"  live score: {score:.2f} (threshold {_s.speaker_threshold})")
    if score >= max(_s.speaker_threshold + 0.1, 0.45):
        print("  ✓ strong match — enrollment good.")
    elif score >= _s.speaker_threshold:
        print("  ~ passes, but thin margin. Consider re-running; check mic distance / Bluetooth.")
    else:
        print("  ✗ WEAK MATCH — this profile would NOT reliably recognise you. The previous "
              "profile is in voiceprint.json.bak. Re-run in the conditions you normally speak "
              "(same mic, AirPods state as usual).")

    # --- the OTHER half of the measurement -------------------------------------------------
    # Everything above measures false-REJECT: does the profile recognise the owner? Nothing has
    # ever measured false-ACCEPT, and that is precisely how a threshold that admits a television
    # survived in production — 742 live gate decisions on 2026-08-11 accepted 186, among them a
    # film playing in the room, in four languages. A gate is a SEPARATION, and a separation cannot
    # be judged from one side of it.
    print("\n--- impostor check: play a video/podcast at your normal volume, or have someone else")
    print("    speak. (Just staying silent also works — silence should score low too.)")
    for c in (3, 2, 1):
        print(f"  recording in {c}…", end="\r", flush=True)
        time.sleep(1)
    print("  ● recording 6s — NOT your voice")
    other = _record(6)
    _, imp = fresh.verify(other, SR)
    print(f"  impostor score: {imp:.2f} (owner {score:.2f}, threshold {_s.speaker_threshold})")
    print("  " + separation_verdict(score, imp, _s.speaker_threshold))
    print("\nRestart the edge to load the new profile.")


if __name__ == "__main__":
    main()
