"""Verify Afon's STT understands you in any of the six languages — with your REAL voice.

Records a few seconds from your mic and transcribes with faster-whisper (auto-detect), printing
the DETECTED language + the transcript. Say a sentence in English, French, German, Armenian,
Russian, or Ukrainian and confirm it's understood. (The live assistant replies in English; this
script only checks the understand step.)

    uv run python bench/stt_language_check.py            # 5 seconds, base model
    uv run python bench/stt_language_check.py 6 small    # 6 seconds, more accurate model
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LANG_NAMES = {
    "en": "English", "fr": "French", "de": "German",
    "hy": "Armenian", "ru": "Russian", "uk": "Ukrainian",
}
TARGET = set(LANG_NAMES)


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    model_size = sys.argv[2] if len(sys.argv) > 2 else "base"

    import numpy as np
    import pyaudio
    from faster_whisper import WhisperModel

    sr, chunk = 16000, 1024
    print(f"Recording {seconds:.0f}s @ {sr} Hz — speak now (any of: "
          f"{', '.join(LANG_NAMES.values())})…", flush=True)
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=sr, input=True, frames_per_buffer=chunk)
    frames = [stream.read(chunk, exception_on_overflow=False)
              for _ in range(int(sr / chunk * seconds))]
    stream.stop_stream()
    stream.close()
    pa.terminate()
    audio = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
    print("…transcribing (first run downloads the model)…", flush=True)

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio, language=None, beam_size=1)
    text = " ".join(s.text for s in segments).strip()

    lang = info.language
    name = LANG_NAMES.get(lang, lang)
    in_scope = "YES" if lang in TARGET else "no (outside the 6 target languages)"
    print("\n=== STT result ===")
    print(f"detected language : {name} [{lang}]  (p={info.language_probability:.2f})")
    print(f"in target set     : {in_scope}")
    print(f"transcript        : {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
