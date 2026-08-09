"""VAD wiring + barge-in for Phase 1.

Two pieces:

- ``build_vad_processor()`` returns a Pipecat ``VADProcessor`` backed by Silero VAD
  (CPU, bundled). It emits ``VADUserStartedSpeakingFrame`` / ``VADUserStoppedSpeakingFrame``
  / ``UserSpeakingFrame`` into the pipeline — the speech-activity signal everything else
  keys off.

- ``BargeInProcessor`` turns "user started speaking while Afon is talking" into a real
  interruption: it calls ``broadcast_interruption()``, which pushes an ``InterruptionFrame``
  downstream (stops the TTS + output transport) and upstream (so the brain can abort its
  in-flight turn). It ONLY interrupts while the bot is speaking, so a normal user turn isn't
  self-cancelled.

BARGE-IN REQUIRES HEADPHONES (or AEC). On open speakers the mic re-hears Afon's own TTS,
VAD flags it as "user speech", and Afon interrupts himself. That's why barge-in is gated
behind ``settings.barge_in_enabled`` (full-duplex) and the assistant keeps the HalfDuplexGate
for the speakers path instead.

Placement: ``transport.input() -> VADProcessor -> ... -> BargeInProcessor -> STT -> brain``.
The output transport pushes Bot{Started,Stopped}SpeakingFrame UPSTREAM, so BargeInProcessor
(sitting before the brain/TTS) observes them.
"""

from __future__ import annotations

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.config import settings


def build_vad_processor() -> VADProcessor:
    """Silero VAD processor tuned from settings (CPU-friendly, no network)."""
    analyzer = SileroVADAnalyzer(
        params=VADParams(
            confidence=settings.vad_confidence,
            start_secs=settings.vad_start_secs,
            stop_secs=settings.vad_stop_secs,
            min_volume=settings.vad_min_volume,
        )
    )
    logger.info(
        f"VAD: Silero (confidence={settings.vad_confidence} "
        f"start={settings.vad_start_secs}s stop={settings.vad_stop_secs}s)"
    )
    return VADProcessor(vad_analyzer=analyzer)


class BargeInProcessor(FrameProcessor):
    """Interrupt Afon when the user starts speaking over him.

    Tracks bot-speaking state from Bot{Started,Stopped}SpeakingFrame (pushed upstream by the
    output transport) and, on a VADUserStartedSpeakingFrame while the bot is speaking,
    broadcasts an interruption so playback + the brain turn are cancelled.
    """

    def __init__(self) -> None:
        super().__init__()
        self._bot_speaking = False

    def _note(self, frame: Frame) -> bool:
        """Update bot-speaking state from a frame; return True if it should interrupt.

        Pure state machine (no I/O) so it can be unit-tested without the pipeline.
        """
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking:
            self._bot_speaking = False  # one interruption per bot turn
            return True
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if self._note(frame):
            logger.info("barge-in: user spoke over Afon — interrupting")
            await self.broadcast_interruption()

        await self.push_frame(frame, direction)
