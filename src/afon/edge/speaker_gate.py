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
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.edge.speaker_id import SpeakerVerifier
from afon.shared.paths import state_dir

# Room-awareness policy (owner's ask, 2026-07-28): Afon looks at the camera ONLY when the audio is
# suspicious — a voice that isn't the owner's, or the owner's voice arriving amid someone else's
# (overlap) — to understand who's in the room. Never a routine poll: the camera stays off otherwise.
_ROOM_CONTEXT = state_dir() / "room_context.json"
_LOOK_COOLDOWN_S = 180.0    # at most one look per 3 min — a chatty video shouldn't strobe the camera
_OVERLAP_WINDOW_S = 12.0    # owner accepted this soon after a stranger = multiple voices in the room
_BORDERLINE_MARGIN = 0.05   # accepts within this of the threshold also earn a face check (the owner's
#                             floor touches the impostor ceiling until re-enrollment lifts his scores)
_PREROLL_S = 0.3            # VAD fires slightly after speech onset — keep this much of what precedes it


def _borderline_margin(verifier: SpeakerVerifier) -> float:
    """How close to the bar still counts as "barely passed", and so earns a face check.

    Derived from the same measured band as the bar itself (10.F3). A fixed 0.05 was fine against a
    hand-placed threshold, but against a derived one it can be wider than a third of the band — and
    then EVERY accept is borderline and the camera opens on every turn (cooldown-limited, but still
    a camera opening because a constant outgrew its measurement). A third of the gap keeps the
    borderline region proportional to how much separation the profile actually has.
    """
    sep = verifier.separation
    if sep is None:
        return _BORDERLINE_MARGIN
    return min(_BORDERLINE_MARGIN, max(0.01, (sep[0] - sep[1]) / 3.0))


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
        # Audio of the CURRENT utterance only (see _utterance_audio). Kept until the next speech
        # onset, so a second final transcript inside one utterance re-scores the whole utterance
        # instead of the sliver since the first — that sliver was short enough to fall under
        # embed()'s 0.5s floor, and a None embedding makes verify() fail OPEN.
        self._utt: list[bytes] = []
        self._utt_bytes = 0
        self._capturing = False
        self._memo: tuple[int, bool, float] | None = None

    def _utterance_audio(self) -> bytes:
        """The audio to identify the speaker from.

        Prefer the VAD-delimited utterance; fall back to the whole rolling window if no VAD frame
        has ever arrived. Scoring the whole 6s window was the default before 2026-08-11, and it
        diluted a 2s utterance with 4s of room tone, television and other people — which is why the
        live score distribution over 742 decisions was a single smooth hump with the threshold on
        its slope, rather than two separated by a valley. It also cost ~1.1s of ECAPA per turn on
        the critical path, against ~0.6s for a 2s utterance.
        """
        utt = b"".join(self._utt)
        # Below ECAPA's 0.5s floor embed() returns None and verify() fails OPEN — so a one-word turn
        # would skip the check entirely, and "yes" is the word that confirms a privileged action.
        # Widening back to the rolling window keeps a short turn checked; a diluted score is still a
        # score, whereas no score at all is an open door.
        if len(utt) < int(0.6 * self._sample_rate * 2):
            return b"".join(self._buf)
        return utt

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._sample_rate = frame.sample_rate or self._sample_rate
            self._max_bytes = int(self._window_s * self._sample_rate * 2)
            self._buf.append(frame.audio)
            self._buf_bytes += len(frame.audio)
            while self._buf_bytes > self._max_bytes and len(self._buf) > 1:
                self._buf_bytes -= len(self._buf.popleft())
            if self._capturing:
                self._utt.append(frame.audio)
                self._utt_bytes += len(frame.audio)
                while self._utt_bytes > self._max_bytes and len(self._utt) > 1:
                    self._utt_bytes -= len(self._utt.pop(0))
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            # Slice the pre-roll to the byte, not to whole chunks: the transport's chunk size is
            # not ours to assume, and keeping the last WHOLE chunk keeps the entire preceding
            # window whenever chunks are large — which is the very dilution this exists to stop.
            preroll = int(_PREROLL_S * self._sample_rate * 2) & ~1   # 16-bit sample aligned
            tail = b"".join(self._buf)[-preroll:] if preroll else b""
            self._utt = [tail] if tail else []
            self._utt_bytes = len(tail)
            self._capturing = True
            self._memo = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            # Only gate when the feature is actually active + enrolled (cheap fast-path otherwise).
            if self._verifier.has_profile:
                audio = self._utterance_audio()
                # OFF the event loop: verify() is soxr resampling + a full ECAPA forward pass on CPU
                # (~0.1-1s). Run inline it blocked the loop once per utterance, which starved the
                # Deepgram (5s) and ElevenLabs (10s) websocket keepalives -> both sockets hit their
                # idle timeouts and reconnected mid-conversation, and the edge<->brain opening
                # handshake missed its startup gate. Inference is CPU-bound C/torch that releases the
                # GIL, so a thread is the right tool here.
                # Deepgram can emit several final transcripts inside one utterance. Re-running the
                # forward pass on identical audio buys nothing and costs a second ~1s on the turn.
                if self._memo and self._memo[0] == len(audio):
                    _, accept, score = self._memo
                else:
                    accept, score = await asyncio.to_thread(
                        self._verifier.verify, audio, self._sample_rate
                    )
                    self._memo = (len(audio), accept, score)
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
                elif score < self._verifier.accept_bar()[0] + _borderline_margin(self._verifier):
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
