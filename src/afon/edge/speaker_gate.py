"""SpeakerGate — drop transcripts that aren't in the owner's voice (Phase 5).

Sits AFTER the STT. It keeps a short rolling buffer of recent mic audio; when the STT produces a
``TranscriptionFrame``, it embeds that buffered audio and compares it to the enrolled voiceprint
(``SpeakerVerifier``). If the voice doesn't match, the transcript is **dropped** so the brain never
acts on it — Afon simply stays silent for a stranger. Everything else (audio, control frames)
passes through untouched.

Graceful by construction: if speaker-id is off, no profile is enrolled, or the ECAPA backend isn't
installed, ``verify`` returns accept=True, so the gate is a no-op (see ``speaker_id.py``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.edge.speaker_id import SpeakerVerifier

# Room-awareness policy (owner's ask, 2026-07-28): Afon looks at the camera ONLY when the audio is
# suspicious — a voice that isn't the owner's, or the owner's voice arriving amid someone else's
# (overlap) — to understand who's in the room. Never a routine poll: the camera stays off otherwise.
_ROOM_CONTEXT = Path.home() / ".afon" / "room_context.json"
_LOOK_COOLDOWN_S = 180.0    # at most one look per 3 min — a chatty video shouldn't strobe the camera
_OVERLAP_WINDOW_S = 12.0    # owner accepted this soon after a stranger = multiple voices in the room
_BORDERLINE_MARGIN = 0.05   # accepts within this of the threshold also earn a face check (the owner's
#                             floor touches the impostor ceiling until re-enrollment lifts his scores)


def _accept_threshold() -> float:
    from afon.config import settings
    return settings.speaker_threshold


class SpeakerGate(FrameProcessor):
    def __init__(self, verifier: SpeakerVerifier | None = None, window_s: float = 6.0) -> None:
        super().__init__()
        self._verifier = verifier or SpeakerVerifier()
        self._window_s = window_s
        self._buf: deque[bytes] = deque()
        self._buf_bytes = 0
        self._sample_rate = 16000
        # bytes for the window at 16-bit mono
        self._max_bytes = int(window_s * self._sample_rate * 2)
        self._last_reject = 0.0
        self._last_look = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._sample_rate = frame.sample_rate or self._sample_rate
            self._max_bytes = int(self._window_s * self._sample_rate * 2)
            self._buf.append(frame.audio)
            self._buf_bytes += len(frame.audio)
            while self._buf_bytes > self._max_bytes and len(self._buf) > 1:
                self._buf_bytes -= len(self._buf.popleft())
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            # Only gate when the feature is actually active + enrolled (cheap fast-path otherwise).
            if self._verifier.has_profile:
                audio = b"".join(self._buf)
                # OFF the event loop: verify() is soxr resampling + a full ECAPA forward pass on CPU
                # (~0.1-1s). Run inline it blocked the loop once per utterance, which starved the
                # Deepgram (5s) and ElevenLabs (10s) websocket keepalives -> both sockets hit their
                # idle timeouts and reconnected mid-conversation, and the edge<->brain opening
                # handshake missed its startup gate. Inference is CPU-bound C/torch that releases the
                # GIL, so a thread is the right tool here.
                accept, score = await asyncio.to_thread(
                    self._verifier.verify, audio, self._sample_rate
                )
                if not accept:
                    logger.info(f"speaker gate: ignored ({score:.2f}) — not the owner's voice: "
                                f"{frame.text!r}")
                    self._last_reject = time.monotonic()
                    self._maybe_look("unrecognized voice in the room")
                    return  # drop: brain never sees it
                # INFO (not DEBUG) so the owner's real accept-scores are visible in production — the
                # only way to calibrate the threshold against a live owner-vs-stranger separation.
                logger.info(f"speaker gate: accepted ({score:.2f}) — {frame.text!r}")
                if time.monotonic() - self._last_reject <= _OVERLAP_WINDOW_S:
                    self._maybe_look("multiple voices (owner + someone else)")
                elif score < _accept_threshold() + _BORDERLINE_MARGIN:
                    # Face as the second factor: a barely-passing voice (live data 2026-07-28: a
                    # YouTube voice cleared at exactly the owner's median) gets a camera check, so the
                    # brain's room context marks the turn as voice-marginal without adding latency.
                    self._maybe_look(f"borderline voice accept ({score:.2f})")
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _maybe_look(self, reason: str) -> None:
        """Fire-and-forget camera check, at most once per cooldown. Best-effort: the voice pipeline
        must never wait on (or die with) the camera."""
        from afon.config import settings
        if not settings.room_check_on_suspicion:
            return
        now = time.monotonic()
        if now - self._last_look < _LOOK_COOLDOWN_S:
            return
        self._last_look = now
        try:
            asyncio.get_running_loop().create_task(self._room_check(reason))
        except RuntimeError:
            pass

    async def _room_check(self, reason: str) -> None:
        """Look through the camera (local — the edge runs on the machine that owns it), log the
        verdict, and persist it to room_context.json so activity_snapshot carries it to the brain."""
        try:
            from afon.brain.tools.camera import _visual_presence_local
            verdict = await _visual_presence_local({})
            logger.info(f"room check ({reason}): {verdict}")
            _ROOM_CONTEXT.parent.mkdir(parents=True, exist_ok=True)
            _ROOM_CONTEXT.write_text(json.dumps(
                {"ts": time.time(), "reason": reason, "verdict": verdict}), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — a camera hiccup must never surface into the pipeline
            logger.debug(f"room check skipped ({type(e).__name__}: {e})")
