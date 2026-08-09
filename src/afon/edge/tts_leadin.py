"""Small output pad so the first spoken phoneme is not clipped by the audio device."""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TTSLeadInSilence(FrameProcessor):
    """Prepend a short PCM silence pad to the first audio chunk of each TTS utterance.

    Some Windows audio devices wake the output path a fraction late, making users hear from word two.
    Padding the first chunk keeps TTS streaming intact while giving the endpoint time to open cleanly.
    """

    def __init__(self, ms: int = 120) -> None:  # ponytail: 220→120, enough to avoid word-2 clipping on a
        super().__init__()
        self._ms = max(0, ms)
        self._needs_pad = True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            self._needs_pad = True
        elif isinstance(frame, TTSStoppedFrame):
            self._needs_pad = True
        elif isinstance(frame, TTSAudioRawFrame) and self._needs_pad and self._ms:
            bytes_per_sample = 2
            nbytes = int(frame.sample_rate * frame.num_channels * bytes_per_sample * self._ms / 1000)
            frame = TTSAudioRawFrame(
                audio=(b"\x00" * nbytes) + frame.audio,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                context_id=frame.context_id,
            )
            self._needs_pad = False
            logger.debug(f"tts lead-in: prepended {self._ms}ms silence")

        await self.push_frame(frame, direction)
