"""WakeWordGate — true always-listening, directed-only, multi-wake-word.

Audio is analysed **locally** by openWakeWord and is NOT forwarded to the (cloud) STT
until one of the owner's wake words is heard. After a wake, a listening window opens so a
command — and short follow-ups after Afon replies — flow through. The window closes on
silence, so ambient speech/media is ignored. Keeps Deepgram engaged only when addressed.

The owner's required wake set (see README): afon, alfred, robbin, assist, time to work,
wake up, six-one-nine. openWakeWord only ships pretrained models for a few phrases, so only
the resolvable ones load here; the rest are reported as pending (Porcupine path). The engine
is pluggable via ``AFON_WAKE_WORD_ENGINE``.

Placement: directly after ``transport.input()`` (before the echo gate and STT).
"""

from __future__ import annotations

import random
import time

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Desired phrase -> openWakeWord pretrained model name (the only ones available without training).
OPENWAKEWORD_PRETRAINED: dict[str, str] = {
    "afon": "hey_jarvis",
    "hey afon": "hey_jarvis",
    "alexa": "alexa",
    "hey mycroft": "hey_mycroft",
    "hey rhasspy": "hey_rhasspy",
    "timer": "timer",
    "weather": "weather",
}


def resolve_openwakeword_models(desired: list[str]) -> tuple[list[str], list[str]]:
    """Split desired phrases into (loadable openWakeWord models, pending phrases).

    A phrase that is a path to a trained model file (``.onnx``/``.tflite``) — e.g. the custom
    "afon" models in ``.wakewords/`` — is loaded directly by path. Everything else maps to a
    pretrained model name; unmatched phrases are reported pending.

    (Those models are trained out-of-repo with openWakeWord's own pipeline under WSL. Four places
    used to cite a ``bench/train_wake_word.py`` / ``train_wakeword.py`` that has never existed in
    this repo, one of them asserting outright that it does.)
    """
    import os

    models: list[str] = []
    pending: list[str] = []
    for phrase in desired:
        raw = phrase.strip()
        key = raw.lower()
        if key.endswith((".onnx", ".tflite")):
            if os.path.isfile(raw):
                if raw not in models:
                    models.append(raw)        # custom trained model, loaded by path
            else:
                pending.append(raw)           # configured but not trained yet
            continue
        model = OPENWAKEWORD_PRETRAINED.get(key)
        if model and model not in models:
            models.append(model)
        elif not model:
            pending.append(raw)
    return models, pending


class WakeWordGate(FrameProcessor):
    def __init__(
        self,
        models: list[str],
        threshold: float = 0.5,
        listen_window_s: float = 8.0,
        suppress_during_tts: bool = True,
        ack_phrase: str = "",
        barge_in: bool = False,
        hot_mic_after_wake: bool = False,
        hot_mic_idle_minutes: int = 30,
    ) -> None:
        super().__init__()
        if not models:
            raise ValueError("WakeWordGate needs at least one loadable wake-word model")
        # Spoken "I heard you" acknowledgement choices (pipe-separated -> random for variety).
        # Spoken on EVERY wake so the owner always knows the wake word landed.
        self._ack_choices = [p.strip() for p in (ack_phrase or "").split("|") if p.strip()]
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models()
        self._model = Model(wakeword_models=models, inference_framework="onnx")
        self._names = list(self._model.models.keys())
        self._threshold = threshold
        self._listen_window_s = listen_window_s
        # Hot-mic mode: first wake opens the window for up to `hot_mic_idle_minutes` of silence
        # (default 30 min); subsequent turns within that idle window flow without the wake word.
        # `_open_until` is the cutoff timestamp; we extend it on each successful forward.
        self._hot_mic = hot_mic_after_wake
        self._hot_mic_idle_s = float(hot_mic_idle_minutes) * 60.0
        self._open_until = 0.0
        self._bot_speaking = False
        self._speaking_since = 0.0
        # Watchdog: if BotStoppedSpeaking never arrives (brain link drops mid-reply, TTS aborts), the
        # gate would stay "speaking" forever and go permanently deaf. No real spoken reply lasts this
        # long, so we force-recover after it — the single most important edge-liveness guarantee.
        self._max_speak_s = 30.0
        self._suppress_during_tts = suppress_during_tts
        self._resume_after_tts = False
        # Speakers barge-in: keep wake DETECTION hot while Afon talks, so saying the wake word
        # interrupts him mid-sentence (VAD barge-in stays headphones-only — on speakers his own
        # voice trips the VAD, but it can't say his own wake word at wake-model confidence).
        # A raised threshold guards against TTS bleed scoring near the line.
        self._barge_in = barge_in
        self._barge_threshold = min(0.95, threshold + 0.15)
        # openWakeWord keeps a sliding feature buffer. Whenever we STOP feeding it (listening
        # window open, bot speaking), the buffer freezes with the wake phrase still inside — and
        # the first prediction after resuming re-fires on that stale audio. Observed live: a
        # phantom re-wake + spoken ack every ~9.5s (window + ack), forever. Reset on resume.
        self._needs_reset = False
        logger.info(f"wake words active: {self._names} (threshold {threshold}"
                    + (", barge-in on wake word" if barge_in else "") + ")")

    @property
    def _awake(self) -> bool:
        return time.monotonic() < self._open_until

    @property
    def is_idle(self) -> bool:
        """True when waiting for a wake word (not in a listening window, not speaking) — the only
        time the soft 'listening' pulse should sound. Drives edge/listening_pulse.py."""
        return not self._awake and not self._bot_speaking

    def _wake(self) -> None:
        # Hot-mic: first wake opens the window for the full idle budget. Subsequent opens within
        # the same session extend to the same deadline. Short listen_window_s still applies on the
        # post-TTS reopen path (line ~163) so follow-ups after a reply still get a normal window.
        if self._hot_mic:
            target = time.monotonic() + self._hot_mic_idle_s
            self._open_until = max(self._open_until, target)
        else:
            self._open_until = time.monotonic() + self._listen_window_s

    def _detect(self, frame: InputAudioRawFrame, threshold: float | None = None) -> str | None:
        samples = np.frombuffer(frame.audio, dtype=np.int16)
        if frame.num_channels and frame.num_channels > 1:
            samples = samples[:: frame.num_channels]  # first channel
        if frame.sample_rate and frame.sample_rate != 16000:
            import soxr

            samples = soxr.resample(
                samples.astype(np.float32), frame.sample_rate, 16000
            ).astype(np.int16)
        scores = self._model.predict(samples)
        min_score = threshold if threshold is not None else self._threshold
        # ponytail: near-miss log so "it never wakes" is debuggable — shows the best score when the
        # wake phrase almost landed. Only fires on a real near-miss (rare), so no per-frame spam.
        best_name, best = max(scores.items(), key=lambda kv: kv[1], default=("", 0.0))
        if 0.2 <= best < min_score:
            logger.info(f"wake near-miss: '{best_name}' {best:.2f} < {min_score:.2f} threshold")
        for name, score in scores.items():
            if score >= min_score:
                return name
        return None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Track playback so we can spare the CPU while Afon speaks (see below).
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._speaking_since = time.monotonic()
            if self._suppress_during_tts and self._awake:
                self._resume_after_tts = True
                self._open_until = 0.0
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # After Afon finishes speaking, keep listening briefly for a follow-up. In hot-mic
            # mode this re-opens the long idle window so the owner can keep talking without the
            # wake word; otherwise it falls back to the short listen_window_s.
            if self._awake or self._resume_after_tts:
                self._wake()
            self._resume_after_tts = False

        if isinstance(frame, InputAudioRawFrame):
            # Liveness watchdog: a stuck "speaking" state (missed BotStoppedSpeaking) would mute wake
            # detection forever. Recover so the gate can never go permanently deaf.
            if self._bot_speaking and (time.monotonic() - self._speaking_since) > self._max_speak_s:
                logger.warning(f"wake gate: bot-speaking stuck >{self._max_speak_s:.0f}s — "
                               "force-clearing (lost BotStoppedSpeaking); resuming wake detection")
                self._bot_speaking = False
                self._resume_after_tts = False
            if self._bot_speaking and self._suppress_during_tts:
                # Open speakers path: mic audio is never FORWARDED while Afon speaks (his TTS
                # would leak into STT). With barge-in, wake detection alone stays hot so the wake
                # word interrupts him. ponytail: ONNX predict per 20ms frame during playback can
                # stutter on a starved CPU — if that shows up, score every 2nd frame.
                if self._barge_in:
                    if self._needs_reset:
                        self._model.reset()
                        self._needs_reset = False
                        return
                    hit = self._detect(frame, self._barge_threshold)
                    if hit:
                        logger.info(f"barge-in: wake word '{hit}' over TTS — interrupting")
                        await self.broadcast_interruption()
                        self._bot_speaking = False
                        self._resume_after_tts = False
                        self._wake()
                        self._needs_reset = True
            elif self._awake:
                self._needs_reset = True   # predict is paused; buffer will be stale on resume
                await self.push_frame(frame, direction)  # forward command audio to STT
            elif self._bot_speaking:
                # Asleep AND Afon is talking: skip wake inference entirely. Running an ONNX
                # predict on every 20ms frame here starves the audio-output thread and makes
                # playback stutter. We also never want to wake on our own TTS, so just swallow.
                self._needs_reset = True
            else:
                if self._needs_reset:
                    self._model.reset()
                    self._needs_reset = False
                    return
                hit = self._detect(frame)
                if hit:
                    self._wake()
                    logger.info(f"wake: '{hit}' detected — listening")
                    if self._ack_choices:
                        # Speak a short acknowledgement on EVERY wake so the owner always hears that
                        # the wake word landed and Afon is listening (a separate beat from the
                        # command). Interaction is two-step: "Hey Afon" -> ack -> then the command.
                        ack = random.choice(self._ack_choices)
                        await self.push_frame(TTSSpeakFrame(ack), FrameDirection.DOWNSTREAM)
                # asleep: swallow audio so the STT never hears ambient speech
            return

        await self.push_frame(frame, direction)
