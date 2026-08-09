"""Local voice (TTS) — build_tts honours AFON_TTS_PROVIDER so the stack can be 100% local.

Proves task #5: the pipeline is no longer hardwired to ElevenLabs (cloud). build_tts dispatches to
ElevenLabs (cloud), Piper (local) or Kokoro (local) by provider, and ElevenLabs fails with a clear,
actionable message (pointing at the offline option) when its keys are missing.

Hermetic: the heavy local engines are NEVER instantiated here (that would download models). We test
the dispatch wiring + the elevenlabs guard only.

    uv run python bench/test_local_voice.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  XX  {name}  {detail}")


def main() -> None:
    import afon.edge.tts as tts_mod
    from afon.config import TTSProvider, settings
    from afon.edge import voice_health

    # Hermetic: a REAL cooldown marker (from live cloud-voice trouble on this machine) must not
    # leak in — build_tts would then correctly pick Piper and the provider checks would misfire.
    real_healthy = voice_health.cloud_tts_healthy
    real_stt_healthy = voice_health.cloud_stt_healthy
    voice_health.cloud_tts_healthy = lambda: True
    voice_health.cloud_stt_healthy = lambda: True

    # All three providers exist (the system CAN run fully local, not only cloud).
    check("provider enum offers a local Piper voice", hasattr(TTSProvider, "piper"))
    check("provider enum offers a local Kokoro voice", hasattr(TTSProvider, "kokoro"))

    # The dispatch table maps each provider to its dedicated builder (wiring, no instantiation).
    check("elevenlabs -> _build_elevenlabs", tts_mod._BUILDERS[TTSProvider.elevenlabs] is tts_mod._build_elevenlabs)
    check("piper -> _build_piper (local)", tts_mod._BUILDERS[TTSProvider.piper] is tts_mod._build_piper)
    check("kokoro -> _build_kokoro (local)", tts_mod._BUILDERS[TTSProvider.kokoro] is tts_mod._build_kokoro)

    # build_tts() selects the right builder per provider — proven with sentinels so nothing heavy loads.
    real = tts_mod._BUILDERS
    tts_mod._BUILDERS = {
        TTSProvider.elevenlabs: lambda: "EL",
        TTSProvider.piper: lambda: "PIPER",
        TTSProvider.kokoro: lambda: "KOKORO",
    }
    saved = settings.tts_provider
    try:
        for prov, want in ((TTSProvider.elevenlabs, "EL"), (TTSProvider.piper, "PIPER"),
                           (TTSProvider.kokoro, "KOKORO")):
            settings.tts_provider = prov
            got = tts_mod.build_tts()
            check(f"build_tts picks {prov.value}", got == want, f"got {got!r}")
    finally:
        settings.tts_provider = saved
        tts_mod._BUILDERS = real

    # ElevenLabs without keys must raise a clear, actionable error that names the offline option.
    el_key, el_voice = settings.elevenlabs_api_key, settings.elevenlabs_voice_id
    settings.elevenlabs_api_key = None
    settings.elevenlabs_voice_id = None
    try:
        tts_mod._build_elevenlabs()
        check("elevenlabs builder raises when unconfigured", False, "did not raise")
    except RuntimeError as e:
        msg = str(e).lower()
        check("error explains the missing keys", "elevenlabs" in msg)
        check("error points at the local fallback", "piper" in msg)
    except Exception as e:  # noqa: BLE001
        check("elevenlabs builder raises a clear RuntimeError", False, type(e).__name__)
    finally:
        settings.elevenlabs_api_key = el_key
        settings.elevenlabs_voice_id = el_voice

    # Cloud -> local fallback: ElevenLabs unconfigured + fallback ON -> build_tts yields the LOCAL voice.
    saved_prov, real_builders = settings.tts_provider, tts_mod._BUILDERS
    saved_fb, saved_key, saved_voice = (settings.voice_local_fallback,
                                        settings.elevenlabs_api_key, settings.elevenlabs_voice_id)
    settings.elevenlabs_api_key = None
    settings.elevenlabs_voice_id = None
    settings.tts_provider = TTSProvider.elevenlabs
    tts_mod._BUILDERS = {
        TTSProvider.elevenlabs: tts_mod._build_elevenlabs,  # real builder -> raises (no keys)
        TTSProvider.piper: lambda: "LOCAL-PIPER",
        TTSProvider.kokoro: lambda: "LOCAL-KOKORO",
    }
    try:
        settings.voice_local_fallback = True
        check("cloud TTS failure falls back to the local voice", tts_mod.build_tts() == "LOCAL-PIPER")
        settings.voice_local_fallback = False
        raised = False
        try:
            tts_mod.build_tts()
        except RuntimeError:
            raised = True
        check("with fallback OFF, a cloud failure raises (no silent local swap)", raised)
    finally:
        settings.voice_local_fallback = saved_fb
        settings.tts_provider = saved_prov
        settings.elevenlabs_api_key = saved_key
        settings.elevenlabs_voice_id = saved_voice
        tts_mod._BUILDERS = real_builders

    # The same fallback wiring exists on the STT side (Deepgram -> Whisper).
    import afon.edge.stt as stt_mod
    from afon.config import STTProvider
    check("STT exposes a cloud->local fallback table", hasattr(stt_mod, "_BUILDERS") and bool(stt_mod._CLOUD))

    # All three STT providers dispatch to their OWN real builder — moonshine must NOT alias whisper
    # (the old bug: 'moonshine' silently ran the slower whisper engine). Wiring check, no model load.
    check("deepgram -> _build_deepgram", stt_mod._BUILDERS[STTProvider.deepgram] is stt_mod._build_deepgram)
    check("whisper -> _build_whisper (local)", stt_mod._BUILDERS[STTProvider.whisper] is stt_mod._build_whisper)
    check("moonshine -> _build_moonshine (its OWN engine, NOT whisper)",
          stt_mod._BUILDERS[STTProvider.moonshine] is stt_mod._build_moonshine
          and stt_mod._build_moonshine is not stt_mod._build_whisper)
    # build_stt() selects the right builder per provider (sentinels — nothing heavy loads).
    real_stt = stt_mod._BUILDERS
    stt_mod._BUILDERS = {
        STTProvider.deepgram: lambda: "DG",
        STTProvider.whisper: lambda: "WH",
        STTProvider.moonshine: lambda: "MOON",
    }
    saved_stt = settings.stt_provider
    try:
        settings.stt_provider = STTProvider.moonshine
        check("build_stt picks moonshine's engine", stt_mod.build_stt() == "MOON")
    finally:
        settings.stt_provider = saved_stt
        stt_mod._BUILDERS = real_stt

    voice_health.cloud_tts_healthy = real_healthy
    voice_health.cloud_stt_healthy = real_stt_healthy

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
