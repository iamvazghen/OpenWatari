"""EchoBrain — the Phase 0/1 stand-in for Afon's brain.

Repeats the final transcript so we can exercise the voice loop before the real brain
(Phase 2) exists. Replaced by the BrainBridge that streams to afon-brain.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class EchoBrain(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                logger.info(f"heard: {text!r}")
                await self.push_frame(TTSSpeakFrame(f"You said: {text}"))
            return  # consume the transcription; don't forward it further

        await self.push_frame(frame, direction)
