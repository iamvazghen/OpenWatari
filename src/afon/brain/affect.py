"""Phase 6.1 — affect sensing: read the owner's state and adapt Afon's manner.

Heuristic and on the hot path (no LLM call, no audio): infer stress / tiredness / low mood / good spirits /
curtness from the utterance TEXT plus cheap context (the small hours). Returns a compact ``Affect`` and a
one-line manner directive that rides the turn as a system note — concise and calm when he's frustrated,
gentle when he's flat, out of the way when he's neutral.

ponytail: a keyword+context heuristic, NOT a voice-emotion model. It reads text (post-STT), so real
prosody (tone, pace, a sigh) is invisible — the upgrade path is a speech-emotion model at the edge feeding
an ``affect_hint`` alongside the transcript. Calibrate the cue lists to how the owner actually talks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_STRESS_CUES = (
    "frustrat", "annoy", "stressed", "overwhelm", "angry", "pissed", "hate this", "not working",
    "won't work", "wont work", "still broken", "ugh", "damn", "shit", "fuck", "come on", "seriously",
    "why isn't", "why isnt", "for god's sake",
)
_TIRED_CUES = (
    "exhaust", "so tired", "i'm tired", "im tired", "knackered", "sleepy", "burnt out", "burned out",
    "no energy", "drained", "can't focus", "cant focus", "wiped",
)
_LOW_CUES = ("feeling down", "so sad", "depress", "lonely", "hopeless", "pointless", "nothing works", "giving up")
_UP_CUES = (
    "awesome", "amazing", "let's go", "lets go", "great news", "so excited", "love it", "nailed it",
    "we did it", "finally works", "brilliant",
)


@dataclass
class Affect:
    stress: bool = False
    tired: bool = False
    low: bool = False
    upbeat: bool = False
    terse: bool = False   # very short, clipped command

    @property
    def neutral(self) -> bool:
        return not (self.stress or self.tired or self.low or self.upbeat or self.terse)

    def tags(self) -> list[str]:
        return [k for k in ("stress", "tired", "low", "upbeat", "terse") if getattr(self, k)]


def infer_affect(text: str, *, now: datetime | None = None) -> Affect:
    """Read affect from one utterance (+ time of day). Pure/cheap — safe on every turn."""
    t = (text or "").lower().strip()
    a = Affect()
    if not t:
        return a
    words = t.split()
    a.stress = any(c in t for c in _STRESS_CUES) or t.count("!") >= 2
    a.tired = any(c in t for c in _TIRED_CUES)
    a.low = any(c in t for c in _LOW_CUES)
    a.upbeat = any(c in t for c in _UP_CUES)
    # Curt: a very short, non-question, non-celebratory line ("stop", "no", "just do it").
    a.terse = len(words) <= 3 and "?" not in t and not a.upbeat
    # The small hours nudge toward tired even without an explicit cue.
    if now is not None and 1 <= now.hour < 5:
        a.tired = True
    return a


def affect_to_voice(a: Affect) -> dict:
    """C3 — map the owner's inferred affect to ElevenLabs ``voice_settings`` so Afon's VOICE (not just
    his wording) adapts: steadier + a touch slower when he's stressed/low, gentler when tired, a little
    livelier when he's upbeat. Returns a dict for the TTS request body (only the keys we steer).

    ponytail: these are STARTING values — prosody is a physical-world thing you tune by ear, so this is
    the calibration knob, not a fixed truth. `speed` is ElevenLabs' 0.7–1.2 range; `stability`/`style`
    are 0–1. One flag wins, most-caring first (low → stress → tired → upbeat → terse), else neutral."""
    if a.low:
        return {"stability": 0.70, "style": 0.10, "speed": 0.93}   # warm, steady, unhurried
    if a.stress:
        return {"stability": 0.75, "style": 0.00, "speed": 0.98}   # calm, no flourish, straight at it
    if a.tired:
        return {"stability": 0.60, "style": 0.00, "speed": 0.95}   # gentle, easy
    if a.upbeat:
        return {"stability": 0.40, "style": 0.30, "speed": 1.05}   # a touch livelier
    if a.terse:
        return {"stability": 0.50, "style": 0.00, "speed": 1.08}   # match the clipped pace
    return {"stability": 0.50, "style": 0.00, "speed": 1.00}       # neutral (ElevenLabs defaults)


def manner_note(a: Affect) -> str | None:
    """One system-note line steering Afon's manner this turn. None when neutral — stay out of the way."""
    bits: list[str] = []
    if a.stress:
        bits.append("he sounds frustrated — stay calm and concise, go straight at the problem, no chirpiness or over-explaining")
    if a.tired:
        bits.append("he seems tired — keep it short and easy, and offer to defer anything non-urgent")
    if a.low:
        bits.append("he sounds low — be warm and steady, no forced positivity")
    if a.terse and not (a.stress or a.tired or a.low):
        bits.append("he's being terse — match it: brief and direct, skip the preamble")
    if a.upbeat:
        bits.append("he's in good spirits — you can be a touch warmer, while staying useful")
    if not bits:
        return None
    return "Owner's apparent state right now — " + "; ".join(bits) + ". Adjust your manner to match, without mentioning that you're doing so."
