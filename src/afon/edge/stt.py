"""STT builder — pick + configure the speech-to-text service for the voice pipeline.

Multilingual by design: the owner speaks English, Russian, German, French, Spanish and Ukrainian
(`afon/shared/language.py` is the list). Two engines, and the trade-off is a real one:

* **Deepgram** (default, low-latency cloud): nova-3 ``language='multi'`` code-switches across
  English / Spanish / French / German / Russian with excellent latency. It does **not** cover
  Ukrainian — and crucially it does not error on it, it returns plausible Russian.
* **Whisper** (``faster-whisper``, local): auto-detects and transcribes **all six**. Heavier on a
  no-GPU CPU, but the only configured engine that covers everything. ``AFON_STT_PROVIDER=whisper``.

Whichever is chosen, `_warn_language_gaps` says out loud which configured languages this ear
cannot actually hear, because that failure is silent by construction: the transcript looks
well-formed, the language detector agrees with it, and Afon answers confidently in the wrong
language. The reply language is decided downstream (`AFON_REPLY_LANGUAGE`, "match" to mirror the
speaker); STT only decides which spoken languages Afon can *understand*.
"""

from __future__ import annotations

from loguru import logger

from afon.config import STTProvider, settings


def _auto_whisper(model: str):
    """Whisper STT that genuinely AUTO-DETECTS the spoken language.

    Pipecat's ``WhisperSTTService`` forces ``language='en'`` on faster-whisper (it resolves
    ``language=None`` to English so its ``assert_given`` check passes), which mis-transcribes
    Armenian/Russian/French/etc. We override ``run_stt`` to call the model with ``language=None``
    (true auto-detect across all six languages) and report the *detected* language. Task stays the
    default 'transcribe', preserving the spoken language; the brain then always replies in English.
    """
    import asyncio
    from collections.abc import AsyncGenerator

    import numpy as np
    from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
    from pipecat.services.whisper.stt import WhisperSTTService
    from pipecat.utils.time import time_now_iso8601

    class _AutoDetectWhisper(WhisperSTTService):
        async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
            if not self._model:
                yield ErrorFrame("Whisper model not available")
                return
            await self.start_processing_metrics()
            audio_float = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            # language: pin to settings.whisper_language ("en" default) — per-utterance auto-detect
            #   mis-fires to Russian on short English speech. "auto" -> None (detect all six).
            # condition_on_previous_text=False: the #1 fix for faster-whisper REPEAT LOOPS ("X. X. X…").
            # vad_filter=True: drop silence/noise so it doesn't hallucinate phantom words from quiet.
            lang = settings.whisper_language
            lang = None if (not lang or lang.lower() == "auto") else lang
            # Proper-noun biasing (Phase 1.5): favour the owner's names/places/projects so "Yerevan"
            # doesn't become "your event". faster-whisper takes ONE space-joined string.
            from afon.edge.proper_nouns import hotwords_str
            hot = hotwords_str() or None
            segments, info = await asyncio.to_thread(
                self._model.transcribe, audio_float,
                language=lang,
                condition_on_previous_text=False,
                vad_filter=True,
                hotwords=hot,
            )
            detected = getattr(info, "language", None)
            text = ""
            threshold = self._settings.no_speech_prob
            for seg in segments:
                if threshold is None or seg.no_speech_prob < threshold:
                    text += f"{seg.text} "
            text = text.strip()
            await self.stop_processing_metrics()
            if text:
                await self._handle_transcription(text, True, detected)
                logger.debug(f"Whisper[{detected}]: {text}")
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), detected)

    return _AutoDetectWhisper(model=model, language=None)


def _build_whisper():
    _lang = settings.whisper_language
    _auto = not _lang or _lang.lower() == "auto"
    _desc = "AUTO-DETECT all configured languages" if _auto else f"language={_lang}"
    logger.info(f"STT: Whisper local ({_desc}, model={settings.whisper_model})")
    if _auto:
        _warn_language_gaps("whisper")
    else:
        # A pinned language is not a gap in the model, it is a gap in the configuration — and a
        # louder one, because Whisper will happily force a Ukrainian utterance into English words.
        from afon.shared.language import name_of, parse_languages
        others = [c for c in parse_languages(settings.understood_languages) if c != _lang.lower()]
        if others:
            logger.warning(
                f"Whisper is PINNED to '{_lang}', so {', '.join(name_of(c) for c in others)} will "
                f"be forced into it rather than transcribed. Set AFON_WHISPER_LANGUAGE=auto to "
                f"detect per utterance."
            )
    return _auto_whisper(settings.whisper_model)


def _build_moonshine():
    """Moonshine local STT — English-only, ~3x faster than whisper base on CPU (~0.4s).

    Real engine (the provider used to alias whisper). Holds ONE persistent ONNX model + tokenizer
    across turns (the convenience ``transcribe()`` reloads per call and is ~10x slower). VAD upstream
    segments speech, so each ``run_stt`` sees one short utterance."""
    import asyncio
    import os
    from collections.abc import AsyncGenerator

    import numpy as np
    # Force HF Hub offline: the cached-model revision check otherwise makes a network call that HANGS
    # the windowless pythonw edge at startup (stuck ~36MB, no log). The edge is local-first — every
    # model is cached after first use — so offline is both the fix and the correct posture.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
    from pipecat.frames.frames import Frame, TranscriptionFrame
    from pipecat.services.stt_service import SegmentedSTTService
    from pipecat.utils.time import time_now_iso8601

    class _MoonshineSTT(SegmentedSTTService):
        def __init__(self, model_name: str) -> None:
            super().__init__(sample_rate=16000)
            self._model_name = model_name
            self._model = None
            self._tok = None

        def _ensure(self) -> None:
            # Lazy load OFF the event loop (run_stt calls this via to_thread): loading the ONNX model
            # synchronously during async pipeline construction deadlocks the worker. First utterance
            # pays the one-time load (~6s); every one after is ~0.4s.
            if self._model is not None:
                return
            try:
                self._model = MoonshineOnnxModel(model_name=self._model_name)
            except Exception:  # noqa: BLE001 — first ever run: not cached -> allow ONE online fetch
                os.environ.pop("HF_HUB_OFFLINE", None)
                self._model = MoonshineOnnxModel(model_name=self._model_name)
                os.environ["HF_HUB_OFFLINE"] = "1"
            self._tok = load_tokenizer()

        async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
            samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            if samples.size < 1600:  # < 0.1s -> a click/noise, not speech
                return
            await self.start_processing_metrics()

            def _infer():
                self._ensure()
                return self._tok.decode_batch(self._model.generate(samples[None, :]))[0].strip()

            text = await asyncio.to_thread(_infer)
            await self.stop_processing_metrics()
            if text:
                logger.debug(f"Moonshine: {text}")
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), "en")

    logger.info(f"STT: Moonshine local ({settings.moonshine_model}, English, ~0.4s)")
    return _MoonshineSTT(settings.moonshine_model)


def _warn_language_gaps(provider: str) -> None:
    """Say out loud which configured languages this ear cannot actually hear.

    The failure this prevents is not a crash. Deepgram's multilingual model returns *plausible
    Russian* for Ukrainian speech rather than an error, so the transcript looks fine, the language
    detector agrees with it, and Afon answers confidently in the wrong language. Nothing anywhere
    would have said why.
    """
    from afon.shared.language import name_of, parse_languages, stt_gaps

    wanted = parse_languages(settings.understood_languages)
    gaps = stt_gaps(provider, wanted)
    if not gaps:
        if wanted:
            logger.info(f"STT understands all {len(wanted)} configured languages: "
                        f"{', '.join(name_of(c) for c in wanted)}")
        return
    logger.warning(
        f"STT provider '{provider}' cannot transcribe: {', '.join(name_of(c) for c in gaps)}. "
        f"Speech in those will come back as a plausible-looking transcript in another language, "
        f"not as an error. Set AFON_STT_PROVIDER=whisper (local, auto-detects all six, slower) "
        f"or drop them from AFON_UNDERSTOOD_LANGUAGES."
    )


def _build_deepgram():
    if not settings.deepgram_api_key:
        raise RuntimeError("AFON_DEEPGRAM_API_KEY is not set (.env)")
    from pipecat.services.deepgram.stt import DeepgramSTTService

    logger.info(
        f"STT: Deepgram {settings.deepgram_model} (language={settings.deepgram_language}, "
        f"endpointing={settings.deepgram_endpointing_ms}ms)"
    )
    _warn_language_gaps("deepgram")
    opts = dict(
        model=settings.deepgram_model,
        language=settings.deepgram_language,  # 'multi' = EN/FR/DE/RU code-switch
        smart_format=True,
        # Finalise quickly after the user stops so the brain fires sooner (perceived latency).
        endpointing=settings.deepgram_endpointing_ms,
        # We act only on finals in a wake-gated turn; interim hypotheses would just be churn.
        interim_results=False,
    )
    # Proper-noun biasing (Phase 1.5): nova-3 accepts `keyterm`. Guarded — if this pipecat/Deepgram
    # build doesn't accept the field, drop it rather than fail to build the STT (the Whisper path
    # still biases via hotwords). nova-2 would want `keywords=` instead; keyterm is nova-3.
    from afon.edge.proper_nouns import hotwords_list
    hot = hotwords_list()
    try:
        s = DeepgramSTTService.Settings(**opts, keyterm=hot) if hot else DeepgramSTTService.Settings(**opts)
    except Exception as e:  # noqa: BLE001 — unknown field on this version -> bias via Whisper only
        logger.warning(f"Deepgram keyterm biasing unavailable ({type(e).__name__}); STT still builds")
        s = DeepgramSTTService.Settings(**opts)

    from pipecat.frames.frames import ErrorFrame
    from afon.edge import voice_health

    class _MonitoredDeepgram(DeepgramSTTService):
        """Records every escalated error so the watchdog can fail over to local Whisper when the
        cloud WebSocket keeps dying (a REST probe can't see that — the socket fails while REST is up)."""

        async def push_error_frame(self, error: ErrorFrame) -> None:
            voice_health.record_cloud_error(str(getattr(error, "error", "")))
            await super().push_error_frame(error)

    return _MonitoredDeepgram(api_key=settings.deepgram_api_key, settings=s)


# provider -> builder. All three are real engines: Deepgram (cloud streaming), Whisper (local,
# all six languages), Moonshine (local, English-only, ~0.4s on CPU — the lowest-latency offline STT).
_BUILDERS = {
    STTProvider.deepgram: _build_deepgram,
    STTProvider.whisper: _build_whisper,
    STTProvider.moonshine: _build_moonshine,
}
_CLOUD = {STTProvider.deepgram}


def build_stt():
    """Construct the configured STT service (honours ``AFON_STT_PROVIDER``).

    Cloud (Deepgram) is the default for latency/accuracy; if it can't be built (missing key, etc.)
    and ``voice_local_fallback`` is on, fall back to local Whisper so the pipeline always comes up.
    """
    prov = settings.stt_provider
    fb = settings.stt_fallback_provider
    # Startup health gate: if cloud is the chosen route, verify it actually answers (reachable + key
    # valid, or recent-failure cooldown) BEFORE building it — otherwise come up on local so the edge
    # can always hear. Runtime WebSocket failures are handled separately by the error monitor above.
    if prov in _CLOUD and settings.voice_local_fallback and fb not in _CLOUD:
        from afon.edge import voice_health

        if not voice_health.cloud_stt_healthy():
            logger.warning(f"cloud STT '{prov.value}' unhealthy at startup — using local '{fb.value}'")
            voice_health.mark_cloud_active(False)
            return _BUILDERS[fb]()
        voice_health.mark_cloud_active(True)
    try:
        return _BUILDERS.get(prov, _build_deepgram)()
    except Exception as e:  # noqa: BLE001
        if prov in _CLOUD and settings.voice_local_fallback and fb not in _CLOUD:
            logger.warning(f"cloud STT '{prov.value}' unavailable ({e}); falling back to local '{fb.value}'")
            from afon.edge import voice_health
            voice_health.mark_cloud_active(False)
            return _BUILDERS[fb]()
        raise
