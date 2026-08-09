"""Edge reflexes (subsystem #24) — answer truly-local turns without a brain round-trip.

Some turns need NOTHING external: "what time is it", "what's the date". Routing them to the VPS brain
pays a full WS hop + LLM TTFT for an answer the edge already holds. A ``ReflexGate`` placed right before
the brain (after speaker-verification + affect) catches these, speaks the answer locally, and consumes
the transcript so the brain never runs. Everything else passes straight through — the matcher is
deliberately high-precision (full-string anchored) so it NEVER swallows a turn that needs the brain
("what time does the pharmacy close", "what time is it in Tokyo" → both need data, both fall through).

Scope is intentionally tiny: read-NOW queries that need no external data (time / date / day). "set a
timer" is NOT a reflex — you don't wait on its answer, so its round-trip isn't latency-critical, and it
needs real scheduler machinery the brain already owns. "stop"/"louder" are handled by barge-in / the OS.
Add a reflex here only when it's both instant-answerable locally AND latency-critical.

ponytail: the "sir" honorific mirrors the edge's other hardcoded persona strings (brain_bridge.py); if
the persona is ever made configurable, thread it through here too.
"""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Optional leading wake word ("hey afon, ...") + optional trailing "?" — the rest must match EXACTLY
# (anchored ^…$) so any extra words ("… in Tokyo", "… does the shop open") fall through to the brain.
_WAKE = r"(?:hey\s+\w+[,\s]+)?"

_TIME_RE = re.compile(
    rf"^\s*{_WAKE}(?:"
    r"what(?:'?s| is)\s+the\s+time(?:\s+now)?|"
    r"what\s+time\s+is\s+it(?:\s+now)?|"
    r"tell\s+me\s+the\s+time|"
    r"(?:do\s+you\s+have|got|you\s+got)\s+the\s+time|"
    r"(?:the\s+)?current\s+time"
    r")\s*\??\s*$",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    rf"^\s*{_WAKE}(?:"
    r"what(?:'?s| is)\s+(?:the\s+|today'?s\s+)?date|"
    r"what(?:'?s| is)\s+today(?:'?s\s+date)?|"
    r"what\s+day\s+is\s+(?:it|today)(?:\s+today)?|"
    r"today'?s\s+date|"
    r"what\s+day\s+of\s+the\s+week(?:\s+is\s+it)?"
    r")\s*\??\s*$",
    re.IGNORECASE,
)


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def reflex_answer(text: str, now: datetime | None = None) -> str | None:
    """A locally-answerable, latency-critical read-now query -> the spoken answer; else None (→ brain).
    ``now`` is injectable for a deterministic test."""
    now = now or datetime.now()
    if _TIME_RE.match(text):
        # %-I isn't portable (Windows); strip the leading zero off %I instead ("03:14"→"3:14").
        return f"It's {now.strftime('%I:%M %p').lstrip('0')}, sir."
    if _DATE_RE.match(text):
        return f"It's {now.strftime('%A, %B ')}{_ordinal(now.day)}, sir."
    return None


class ReflexGate(FrameProcessor):
    """Consume a reflex transcript and answer it locally; pass everything else through untouched."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            ans = reflex_answer((getattr(frame, "text", "") or "").strip())
            if ans:
                logger.info(f"edge reflex: {frame.text!r} -> local answer (no brain round-trip)")
                await self.push_frame(TTSSpeakFrame(ans))
                return  # consume — the brain never sees this transcript
        await self.push_frame(frame, direction)


if __name__ == "__main__":
    fixed = datetime(2026, 7, 26, 15, 14)  # a Sunday
    # HITS — answered locally.
    for q in ("what time is it", "What's the time?", "hey afon, what time is it now",
              "tell me the time", "what's the date", "what day is it today", "today's date"):
        assert reflex_answer(q, fixed) is not None, f"should be a reflex: {q!r}"
    assert reflex_answer("what time is it", fixed) == "It's 3:14 PM, sir."
    assert reflex_answer("what's the date", fixed) == "It's Sunday, July 26th, sir."
    # MISSES — must fall through to the brain (need external data / are not read-now).
    for q in ("what time is it in Tokyo", "what time does the pharmacy close", "set a timer for 5 minutes",
              "what's the date of the meeting", "remind me at 3pm", "what's the weather", "stop"):
        assert reflex_answer(q, fixed) is None, f"should NOT be a reflex: {q!r}"
    print("reflexes self-check OK")
