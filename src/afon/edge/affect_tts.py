"""C3 (edge) — mood-adaptive voice on the LIVE path.

The brain already adapts Afon's WORDING to the owner's affect, and the Telegram voice path adapts his
prosody. This closes the loop on the live mic/AirPods path: it reads each owner utterance, infers affect,
and nudges the ElevenLabs voice (stability/style/speed) so Afon SOUNDS steadier when you're stressed,
gentler when tired, livelier when you're upbeat — before he speaks the reply.

Mechanism: on a ``TranscriptionFrame`` it pushes a ``TTSUpdateSettingsFrame`` downstream (through the
brain, which ignores it) to the TTS service, which applies it to the next utterance. Only re-sends when
the settings actually change, so it's near-silent. Import-light + fail-quiet — voice must never break.

ponytail: the affect→settings numbers live in ``brain.affect.affect_to_voice`` (one source of truth,
already unit-tested); this processor is just the edge wire + a change-filter. Calibrate by ear.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.brain.affect import affect_to_voice, infer_affect


class AffectTTS(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self._last: dict | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and (frame.text or "").strip():
            try:
                vs = affect_to_voice(infer_affect(frame.text))
                if vs != self._last:
                    self._last = vs
                    await self.push_frame(TTSUpdateSettingsFrame(settings=vs), FrameDirection.DOWNSTREAM)
                    logger.debug(f"affect-tts: voice -> {vs}")
            except Exception as e:  # noqa: BLE001 — prosody is a nicety, never break the turn
                logger.debug(f"affect-tts skipped: {type(e).__name__}")

        await self.push_frame(frame, direction)
