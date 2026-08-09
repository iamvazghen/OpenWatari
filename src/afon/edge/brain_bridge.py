"""AfonBrain — the real brain in the voice pipeline (replaces EchoBrain in Phase 2).

On each finished transcript it runs Afon's own agent loop (personality + memory + tools)
and speaks the reply. Runs the brain IN-PROCESS on the edge for now; the same `AfonAgent`
will later sit behind the edge<->brain WebSocket (`shared/protocol.py`) when the 24/7 VPS
brain service lands (Phase 4) — the pipeline contract here doesn't change.

Tool progress (e.g. consulting the fleet) is spoken as a short filler so Afon never goes
silent during a longer turn.
"""

from __future__ import annotations

import asyncio
import re

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.brain.agent import AfonAgent

_CANCEL_RE = re.compile(
    r"\b(stop|cancel|abort|abandon|never mind|nevermind|forget it|leave it|drop it|"
    r"shut up|pause|give up)\b",
    re.IGNORECASE,
)


class AfonBrain(FrameProcessor):
    def __init__(self, agent: AfonAgent | None = None) -> None:
        super().__init__()
        self._agent = agent or AfonAgent()
        self._busy = False
        self._turn_task: asyncio.Task | None = None

    async def warmup(self) -> None:
        await self._agent.warmup()
        self._start_scheduler()

    def _start_scheduler(self) -> None:
        """Start the proactive scheduler and teach it to SPEAK fired reminders through us."""
        try:
            from afon.brain.scheduler import SCHEDULER

            loop = asyncio.get_running_loop()

            def speak(message: str) -> None:
                # Called from the scheduler (same loop); push a spoken reminder downstream.
                loop.create_task(self.push_frame(TTSSpeakFrame(f"Reminder, sir: {message}")))

            SCHEDULER.start(on_speak=speak)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"scheduler not started: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Barge-in: abort the in-flight turn so Afon stops talking and listens.
        if isinstance(frame, InterruptionFrame):
            if self._turn_task and not self._turn_task.done():
                logger.info("brain: interrupted — cancelling current turn")
                self._turn_task.cancel()
            self._busy = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                await self._start_or_supersede_turn(text)
            return  # consume the transcription

        await self.push_frame(frame, direction)

    async def _start_or_supersede_turn(self, text: str) -> None:
        """Start a turn, or let the owner interrupt a long/stuck turn with a new instruction.

        The old behaviour ignored transcripts while a tool was running, which made a stuck browser
        action feel like a loop. Once speech reaches the brain it is already wake-gated and
        speaker-verified, so a new utterance from the owner should be able to cancel or replace the
        current task.
        """
        if self._turn_task and not self._turn_task.done():
            if _CANCEL_RE.search(text):
                logger.info(f"heard while busy: {text!r} — cancelling current task")
                self._turn_task.cancel()
                await self.push_frame(TTSSpeakFrame("Cancelled, sir."))
                self._busy = False
                return
            logger.info(f"heard while busy: {text!r} — superseding current task")
            self._turn_task.cancel()
            await self.push_frame(TTSSpeakFrame("Stopping that and switching, sir."))
        self._turn_task = asyncio.create_task(self._handle(text))

    async def _handle(self, text: str) -> None:
        self._busy = True
        try:
            logger.info(f"heard: {text!r}")

            def progress(note: str) -> None:
                # Spoken filler so a longer (e.g. fleet) turn isn't dead air.
                asyncio.create_task(self.push_frame(TTSSpeakFrame(note)))

            # Stream sentence-by-sentence so Afon starts speaking the first sentence while the
            # rest is still being generated (instead of waiting for the whole reply, then talking).
            full: list[str] = []
            async for sentence in self._agent.respond_stream(text, on_progress=progress):
                if sentence:
                    full.append(sentence)
                    await self.push_frame(TTSSpeakFrame(sentence))
            if full:
                logger.info(f"reply: {' '.join(full)!r}")
        except asyncio.CancelledError:
            logger.info("brain turn cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("brain turn failed")
            await self.push_frame(TTSSpeakFrame("Sorry sir, I hit an error handling that."))
        finally:
            self._busy = False
