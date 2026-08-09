"""TTS builder — pick + configure the text-to-speech voice for the edge pipeline.

Like ``build_stt``, this honours ``settings.tts_provider`` so the SAME pipeline runs cloud-quality
or **100% local** just by flipping one flag — no code change:

  * ``elevenlabs`` (default) — cloud, streaming, the chosen "Afon voice". Needs an API key + voice id.
  * ``piper``      — local, CPU-fast. Fully offline: the voice model auto-downloads ONCE, then no
                     network at all.
  * ``kokoro``     — local, higher quality than Piper, still CPU-runnable. Model auto-downloads once.

Pair ``AFON_TTS_PROVIDER=piper`` (or ``kokoro``) with ``AFON_STT_PROVIDER=whisper`` (already the
default — local + multilingual) for a **fully offline voice stack**: no audio ever leaves the
machine, no cloud key required. That satisfies the local-first / privacy non-negotiable. The local
voices need the ``local-voice`` extra installed (``uv sync --extra local-voice``).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from afon.config import TTSProvider, settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _build_elevenlabs():
    if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id):
        raise RuntimeError(
            "TTS provider is 'elevenlabs' but AFON_ELEVENLABS_API_KEY / AFON_ELEVENLABS_VOICE_ID "
            "are not set. Add them to .env, or switch to a fully-local voice with "
            "AFON_TTS_PROVIDER=piper (offline, no key)."
        )
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    from pipecat.frames.frames import ErrorFrame
    from afon.edge import voice_health

    logger.info(f"TTS: ElevenLabs {settings.elevenlabs_model} (cloud, streaming)")

    class _MonitoredElevenLabs(ElevenLabsTTSService):
        """Records escalated errors so the watchdog can fail over to local Piper when the cloud
        WebSocket keeps timing out (handshake/keepalive deaths a REST probe can't detect)."""

        async def push_error_frame(self, error: ErrorFrame) -> None:
            voice_health.record_cloud_error(str(getattr(error, "error", "")))
            await super().push_error_frame(error)

    return _MonitoredElevenLabs(
        api_key=settings.elevenlabs_api_key,
        settings=ElevenLabsTTSService.Settings(
            voice=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_model,
        ),
    )


def _build_piper():
    try:
        from pipecat.services.piper.tts import PiperTTSService
    except ImportError as e:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Piper (local TTS) isn't installed. Run: uv sync --extra local-voice"
        ) from e

    download_dir = _REPO_ROOT / ".piper-voices"
    download_dir.mkdir(exist_ok=True)
    logger.info(f"TTS: Piper LOCAL (voice={settings.piper_voice}) — offline, no key, no cloud")
    return PiperTTSService(
        download_dir=download_dir,
        settings=PiperTTSService.Settings(model=None, voice=settings.piper_voice, language=None),
    )


def _build_kokoro():
    try:
        from pipecat.services.kokoro.tts import KokoroTTSService
        from pipecat.transcriptions.language import Language
    except ImportError as e:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Kokoro (local TTS) isn't installed. Run: uv sync --extra local-voice"
        ) from e

    logger.info(f"TTS: Kokoro LOCAL (voice={settings.kokoro_voice}) — offline, no key, no cloud")
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(
            model=None, voice=settings.kokoro_voice, language=Language.EN
        ),
    )


# provider -> builder. Kept as a dispatch table so the selection is unit-testable without
# instantiating (and downloading) any heavy model.
_BUILDERS = {
    TTSProvider.elevenlabs: _build_elevenlabs,
    TTSProvider.piper: _build_piper,
    TTSProvider.kokoro: _build_kokoro,
}
# Cloud providers: if one of these can't be built, we fall back to the local engine.
_CLOUD = {TTSProvider.elevenlabs}


def build_tts():
    """Construct the configured TTS service (honours ``AFON_TTS_PROVIDER``).

    Cloud is the default for quality; if it can't be built (missing key, etc.) and
    ``voice_local_fallback`` is on, fall back to the configured LOCAL voice so the pipeline always
    comes up instead of dying. Non-cloud providers raise their own clear, actionable error.
    """
    prov = settings.tts_provider
    fb = settings.tts_fallback_provider
    # Startup health gate (mirrors build_stt): only build cloud TTS if it actually answers now (or
    # isn't in post-failure cooldown); else come up on local Piper so the edge can always speak.
    if prov in _CLOUD and settings.voice_local_fallback and fb not in _CLOUD:
        from afon.edge import voice_health

        if not voice_health.cloud_tts_healthy():
            logger.warning(f"cloud TTS '{prov.value}' unhealthy at startup — using local '{fb.value}'")
            return _BUILDERS[fb]()
        voice_health.mark_cloud_active(True)
    try:
        return _BUILDERS.get(prov, _build_elevenlabs)()
    except Exception as e:  # noqa: BLE001
        if prov in _CLOUD and settings.voice_local_fallback and fb not in _CLOUD:
            logger.warning(f"cloud TTS '{prov.value}' unavailable ({e}); falling back to local '{fb.value}'")
            return _BUILDERS[fb]()
        raise
