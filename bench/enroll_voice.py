"""Enroll Vazghen's voice for speaker biometrics (Phase 5).

Records a few short clips from the mic, averages their ECAPA embeddings into one voiceprint, and
saves it to JARVIS_SPEAKER_PROFILE (default <repo>/voiceprint.json). After this, set
JARVIS_SPEAKER_ID_ENABLED=true and Jarvis will obey only your voice.

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

from jarvis.config import settings
from jarvis.edge.speaker_id import SpeakerVerifier

SR = 16000
CLIP_S = 4
N_CLIPS = 3
PROMPTS = [
    "Say: 'Hey Jarvis, this is Vazghen.'",
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
        hint = re.search(r"≈\s*(\d+)\s*s", header)
        if hint:
            sec = int(hint.group(1)) + 3  # small buffer so the tail isn't clipped
        segs.append((f"Read aloud: '{header}'", sec))
    return segs


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
    for i, (prompt, clip_s) in enumerate(clips):
        print(f"\n[{i + 1}/{len(clips)}] {prompt}")
        for c in (3, 2, 1):
            print(f"  recording in {c}…", end="\r", flush=True)
            time.sleep(1)
        print(f"  ● recording {clip_s}s — speak now")
        pcm = _record(clip_s)
        emb = verifier.embed(pcm, SR)
        if emb is None:
            print("  (clip too short / failed, retrying)")
            continue
        embeddings.append(emb / (np.linalg.norm(emb) or 1.0))
        print("  captured ✓")

    if not embeddings:
        print("No usable clips captured.")
        sys.exit(1)

    # One vector PER CLIP (max-cosine at verify time), not a blurred mean — a mean profile could
    # not cover the mic's two acoustic modes (Bluetooth audio active vs not) and locked the owner
    # out at 0.05-0.28 in production (2026-07-29). --append keeps prior conditions' vectors.
    path = SpeakerVerifier.save_profile(np.stack(embeddings), append=args.append)
    mode = "appended to" if args.append else "saved to"
    print(f"\nVoiceprint {mode} {path} ({len(embeddings)} vector(s) from this session).")

    # Verification pass: prove the profile matches the LIVE mic before calling it done. Without
    # this, a bad enrollment is only discovered when Watari goes deaf to the owner.
    print("\n--- verification: say naturally, e.g. 'Hey Watari, how are you today?'")
    for c in (3, 2, 1):
        print(f"  recording in {c}…", end="\r", flush=True)
        time.sleep(1)
    print("  ● recording 6s — speak now")
    pcm = _record(6)
    fresh = SpeakerVerifier()          # reload from disk — verifies what was actually saved
    from jarvis.config import settings as _s
    _s.speaker_id_enabled = True       # force a real score even if the .env flag is off
    accept, score = fresh.verify(pcm, SR)
    print(f"  live score: {score:.2f} (threshold {_s.speaker_threshold})")
    if score >= max(_s.speaker_threshold + 0.1, 0.45):
        print("  ✓ strong match — enrollment good. Restart the edge to load it.")
    elif score >= _s.speaker_threshold:
        print("  ~ passes, but thin margin. Consider re-running; check mic distance / Bluetooth.")
    else:
        print("  ✗ WEAK MATCH — this profile would NOT reliably recognise you. The previous "
              "profile is in voiceprint.json.bak. Re-run in the conditions you normally speak "
              "(same mic, AirPods state as usual).")


if __name__ == "__main__":
    main()
