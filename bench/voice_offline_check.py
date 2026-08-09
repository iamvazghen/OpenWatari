"""Offline voice readiness — can Afon do a FULLY-LOCAL spoken turn, no network? (Roadmap 3.5)

The project's promise is local-first / privacy: with the cloud keys removed and the local models
present, one full spoken turn (mic -> Whisper STT -> brain -> Piper/Kokoro TTS -> speaker) must work
with no audio ever leaving the machine. This check reports, without needing network or a microphone:

  * which local engines are importable (Whisper STT, Piper/Kokoro TTS, openWakeWord, Silero VAD)
  * whether the LOCAL STT and TTS services actually CONSTRUCT (provider forced to local)
  * the personal-vs-framework default decision, stated plainly

It is INFORMATIONAL (never fails the build). When the `local-voice` extra isn't installed it prints
the exact remediation. Run:

    uv run python bench/voice_offline_check.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _line(label: str, ok: bool, note: str = "") -> None:
    mark = f"{GREEN}yes{RESET}" if ok else f"{RED}no {RESET}"
    print(f"  {mark}  {label}" + (f"  — {note}" if note else ""))


def main() -> None:
    from afon.config import STTProvider, TTSProvider, settings

    print("Offline voice readiness — fully-local STT/TTS stack (no cloud, no network).\n")

    # 1) Engine packages importable? (the local-voice extra)
    print("Local engine packages:")
    engines = {
        "Whisper STT (faster-whisper / pipecat whisper)": _have("faster_whisper") or _have("whisper"),
        "Piper TTS (pipecat piper)": _have("piper") or _have("pipecat.services.piper.tts"),
        "Kokoro TTS (kokoro-onnx)": _have("kokoro_onnx") or _have("kokoro"),
        "openWakeWord (wake word)": _have("openwakeword"),
        "Silero VAD (via pipecat/torch)": _have("pipecat") or _have("torch"),
    }
    for name, ok in engines.items():
        _line(name, ok)

    # 2) Do the LOCAL services actually construct? Force providers to local, then build.
    print("\nLocal service construction (provider forced local):")
    stt_ok = tts_ok = False
    stt_note = tts_note = ""
    old_stt, old_tts = settings.stt_provider, settings.tts_provider
    try:
        settings.stt_provider = STTProvider.whisper
        try:
            from afon.edge.stt import build_stt

            build_stt()
            stt_ok = True
        except Exception as e:  # noqa: BLE001 — report, don't crash
            stt_note = f"{type(e).__name__}: {str(e)[:90]}"

        settings.tts_provider = TTSProvider.piper
        try:
            from afon.edge.tts import build_tts

            build_tts()
            tts_ok = True
        except Exception as e:  # noqa: BLE001
            tts_note = f"{type(e).__name__}: {str(e)[:90]}"
    finally:
        settings.stt_provider, settings.tts_provider = old_stt, old_tts

    _line("Whisper STT constructs (local, offline)", stt_ok, stt_note)
    _line("Piper TTS constructs (local, offline)", tts_ok, tts_note)

    # 3) Verdict + the decision, stated plainly.
    engines_ok = all(engines[k] for k in ("Whisper STT (faster-whisper / pipecat whisper)",
                                          "Piper TTS (pipecat piper)"))
    ready = stt_ok and tts_ok
    print("\n  --- verdict ---")
    if ready:
        print(f"  {GREEN}READY{RESET} — a fully-local spoken turn can run with no network "
              "(Whisper STT + Piper TTS construct).")
    elif engines_ok:
        print(f"  {YELLOW}PARTIAL{RESET} — local engines are installed but a service didn't construct; "
              "see the note(s) above (often a one-time model download on first build).")
    else:
        print(f"  {YELLOW}NOT INSTALLED{RESET} — install the local stack: "
              f"{GREEN}uv sync --extra local-voice{RESET}, then re-run. "
              "Models (Whisper, Piper voice) auto-download ONCE, after which it's 100% offline.")

    print("\n  Decision (per docs): the PERSONAL Afon instance defaults to CLOUD quality "
          "(Deepgram STT + ElevenLabs TTS) with automatic LOCAL fallback when a key/engine is "
          "missing (voice_local_fallback=on). For a 100%-private run set "
          "AFON_STT_PROVIDER=whisper + AFON_TTS_PROVIDER=piper. The OpenAfon FRAMEWORK default "
          "is local/BYO-key so a stranger needs no paid accounts.")
    print(f"\n  current config: STT={settings.stt_provider.value}  TTS={settings.tts_provider.value}  "
          f"local_fallback={settings.voice_local_fallback}")


if __name__ == "__main__":
    main()
