"""TTFWMeter — live per-turn latency tracer (Phase 5 → subsystem #23).

Originally measured only Time-To-First-Word (user-stop → first spoken word). Now it also ATTRIBUTES
that total to the three buckets a single number can't separate, so a lag is fixed with data instead of
by ear:

    turn latency: stt=310ms brain=1180ms tts=190ms total=1680ms

  * **stt**   — user stopped speaking → STT emitted the final transcript.
  * **brain** — final transcript → TTS started (the VPS hop + LLM TTFT + any tool call).
  * **tts**   — TTS started → first audible word.
  * **total** — the TTFW, still recorded into the shared ``TTFW`` accumulator for the bench summary.

All four frames pass through this one late slot (right before ``transport.output()``) regardless of
direction — user-stop/transcript/tts-start travel downstream, bot-start upstream — so one processor
sees the whole turn. A missing mark (a reflex turn with no STT, a barge-in) renders as ``?`` and its
segment is skipped; nothing here ever alters the stream. Sub-brain spans (token vs tool) are NOT split
here — drill into the brain only once a trace says ``brain`` is the culprit (ponytail: measure the
bucket before instrumenting inside it).
"""

from __future__ import annotations

import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.bench_metrics import TTFW


def format_turn_trace(marks: dict[str, float]) -> tuple[str, float | None]:
    """Build the one-line segment breakdown + total-ms (TTFW) from monotonic stage marks (seconds).
    Pure so it's unit-testable without pipecat frames. Missing marks -> '?' and that segment is skipped."""

    def seg(a: str, b: str) -> float | None:
        ta, tb = marks.get(a), marks.get(b)
        return None if ta is None or tb is None else (tb - ta) * 1000.0

    stt = seg("user_stop", "stt_final")
    brain = seg("stt_final", "tts_start")
    tts = seg("tts_start", "bot_first_word")
    total = seg("user_stop", "bot_first_word")

    def f(v: float | None) -> str:
        return f"{v:.0f}ms" if v is not None else "?"

    line = f"turn latency: stt={f(stt)} brain={f(brain)} tts={f(tts)} total={f(total)}"
    return line, total


class TTFWMeter(FrameProcessor):
    def __init__(self, accumulator: TTFW | None = None) -> None:
        super().__init__()
        self.ttfw = accumulator or TTFW()
        self._marks: dict[str, float] = {}

    def _mark(self, key: str) -> None:
        # First occurrence per turn wins (multi-sentence STT / repeated TTS starts don't overwrite).
        self._marks.setdefault(key, time.monotonic())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._marks = {"user_stop": time.monotonic()}  # a new turn starts — clear stale marks
        elif isinstance(frame, TranscriptionFrame):
            self._mark("stt_final")
        elif isinstance(frame, TTSStartedFrame):
            self._mark("tts_start")
        elif isinstance(frame, BotStartedSpeakingFrame) and "user_stop" in self._marks:
            self._mark("bot_first_word")
            line, total = format_turn_trace(self._marks)
            logger.info(line)
            if total is not None:
                self.ttfw.record(total)
            self._marks = {}

        await self.push_frame(frame, direction)


if __name__ == "__main__":
    # Self-check: full turn splits into the three buckets + total; a missing mark degrades to '?'.
    full = {"user_stop": 0.0, "stt_final": 0.31, "tts_start": 1.49, "bot_first_word": 1.68}
    line, total = format_turn_trace(full)
    assert line == "turn latency: stt=310ms brain=1180ms tts=190ms total=1680ms", line
    assert total is not None and abs(total - 1680.0) < 0.5, total
    line2, total2 = format_turn_trace({"user_stop": 0.0, "bot_first_word": 0.5})  # reflex: no STT/TTS
    assert "stt=?" in line2 and "brain=?" in line2 and "total=500ms" in line2, line2
    assert abs(total2 - 500.0) < 0.5, total2
    print("latency_meter self-check OK")
