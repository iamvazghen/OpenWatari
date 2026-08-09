"""LIVE end-to-end test of the brain's voice I/O — the engines behind the phone voice path.

Synthesizes a known phrase in Afon's voice (ElevenLabs), then transcribes that audio back
(Deepgram). If the words survive the TTS->STT round-trip, both engines work for the Telegram /
iPhone voice flow. Needs network + the real keys. Run:

    uv run python bench/test_voice_io.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


async def main() -> None:
    from afon.brain.voice_io import synthesize, transcribe_audio

    phrase = "Afon hears you clearly, sir."
    print(f"TTS: synthesizing {phrase!r} …")
    t0 = time.perf_counter()
    audio = await synthesize(phrase)
    t_tts = time.perf_counter() - t0
    ok_tts = bool(audio) and len(audio) > 1000
    print(f"  TTS: {'OK' if ok_tts else 'FAIL'} — {len(audio or b'')} bytes in {t_tts:.2f}s")

    print("STT: transcribing the synthesized audio back …")
    t0 = time.perf_counter()
    text = await transcribe_audio(audio or b"", content_type="audio/mpeg")
    t_stt = time.perf_counter() - t0
    low = text.lower()
    ok_stt = any(w in low for w in ("afon", "hears", "clearly"))
    print(f"  STT: {'OK' if ok_stt else 'FAIL'} — {text!r} in {t_stt:.2f}s")

    print(f"\n=== voice round-trip {'PASS' if (ok_tts and ok_stt) else 'FAIL'} "
          f"(TTS {t_tts:.2f}s + STT {t_stt:.2f}s) ===")
    if not (ok_tts and ok_stt):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
