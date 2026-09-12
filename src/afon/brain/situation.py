"""One assembled view of the owner's situation per turn (SYSTEMS.md 27.F1, 27.F2).

Four sources already answer "where is he and what is he doing" — `presence.py` (foreground app,
idle, meeting detection), `modes.py` (focus, guest, lockdown, commute), the activity tool and the
maps tool. Nothing assembled them. Each caller reached for whichever source it happened to know
about, so two parts of the same turn could disagree about whether the owner was at the desk: the
answer would assume he was present while proactivity assumed he had gone. That is the failure this
module exists to end, and it is a *shape* problem, not an intelligence problem — there is no model
here and there must not be one, because the budget is 30ms in-process with no LLM call ever.

Assembled once inside the turn, read through `current()` by anything downstream, exactly the way
`turn_trace` carries the row. A helper deep in the tool path therefore sees the same world the
answer did, without every signature growing a parameter.

    from afon.brain.situation import situation, current
    with situation(perceive(PRESENCE), MODES):
        ...                        # current() is the same object everywhere in this turn

Fail-quiet throughout: a source that raises yields `UNKNOWN` for its field, never an exception.
A context layer that can break a turn is worse than no context layer.

ponytail: `part_of_day` is four buckets off the local clock, not sun position. `astral` is the
declared base for real daylight (SYSTEMS.md S27) and belongs with 27.R1's location granularity;
buckets are enough for "is it reasonable to speak", which is all the floor promises.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Iterator

from loguru import logger

from afon.brain.perception import MAX_AGE_S as _MAX_AGE

#: Every field falls back to this rather than to None, so a caller can print the situation without
#: a null check and a missing source is visible in a trace instead of looking like "at the desk".
UNKNOWN = "unknown"

#: Where the owner is, coarsest first. 27.R1 refines this; the floor only has to name a value and
#: say which source produced it, so that a wrong assumption is attributable rather than anonymous.
PLACES = ("desk", "away", "travelling", UNKNOWN)

#: Staleness lives with the sensing, in `perception.MAX_AGE_S`. Re-exported so a caller reasoning
#: about "how long until he counts as away" reads one number and not two kept in step — two copies
#: of a staleness rule is how a fresh fact and a stale one start disagreeing.
STALE_AFTER_S = _MAX_AGE["presence"]


@dataclass(frozen=True)
class Situation:
    """What Afon assumed about the world for one turn. Frozen: a turn's context must not drift
    under it half-way through, or the disclosure would describe something that no longer held."""

    place: str = UNKNOWN
    place_source: str = UNKNOWN
    app: str = UNKNOWN
    in_meeting: bool = False
    focus: bool = False
    guest: bool = False
    lockdown: bool = False
    commute: bool = False
    part_of_day: str = UNKNOWN
    local_hour: int = -1
    ts: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def present(self) -> bool:
        """At the desk and recently active — the only state in which an unprompted remark is
        certain to be heard by a human rather than by an empty room."""
        return self.place == "desk"

    def line(self) -> str:
        """One line for the HUD and for a trace row."""
        bits = [self.place, self.part_of_day]
        for flag in ("in_meeting", "focus", "guest", "lockdown", "commute"):
            if getattr(self, flag):
                bits.append(flag)
        return " · ".join(b for b in bits if b and b != UNKNOWN)


def part_of_day(hour: int) -> str:
    """Four buckets. Night runs past midnight, which is why this is not a chain of `elif hour <`."""
    if hour < 0 or hour > 23:
        return UNKNOWN
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def assemble(perception: Any = None, modes: Any = None, now: float | None = None,
             local: datetime | None = None) -> Situation:
    """Interpret one sensing snapshot plus the declared modes. Reads no sensor itself.

    The split is the ownership boundary SYSTEMS.md settles between S26 and S27: perception says
    "presence sensed 'at-screen', four seconds ago"; this function decides that means at the desk.
    Every read is guarded, because the object is on the answer path and one missing field must not
    cost the turn.
    """
    now = time.time() if now is None else now
    local = local or datetime.now()

    place, source, app, meeting = UNKNOWN, UNKNOWN, UNKNOWN, False
    if perception is not None:
        try:
            pres = perception.presence
            if not pres.known:
                source = pres.source or "presence-empty"
            elif pres.is_stale(now):
                place, source = "away", "presence-stale"
            else:
                # Sensed and fresh: at the screen is the desk, idle at the screen is still the desk
                # — he has not gone anywhere, he has stopped typing. Only staleness means away.
                place, source = "desk", pres.source
            app = perception.activity.value if perception.activity.known else UNKNOWN
            # A stale meeting verdict is not a meeting. Believing one is how Afon stays silent for
            # twenty minutes after a call ended.
            meeting = bool(perception.meeting.value) and not perception.meeting.is_stale(now)
        except Exception as e:  # noqa: BLE001 — see module docstring
            logger.debug(f"situation: perception unusable ({type(e).__name__})")
            source = "perception-error"

    focus = guest = lockdown = commute = False
    if modes is not None:
        try:
            focus = bool(modes.focus_active())
            guest, lockdown, commute = bool(modes.guest), bool(modes.lockdown), bool(modes.commute)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"situation: modes unavailable ({type(e).__name__})")

    # Commute wins over a fresh desk sample: the laptop's last sample is from the desk he left.
    if commute:
        place, source = "travelling", "mode-commute"

    return Situation(place=place, place_source=source, app=app, in_meeting=meeting,
                     focus=focus, guest=guest, lockdown=lockdown, commute=commute,
                     part_of_day=part_of_day(local.hour), local_hour=local.hour, ts=now)


#: 27.F2 — the assumptions worth saying out loud, and what to say. A disclosure is owed only when
#: the assumption CHANGED the answer, so this is deliberately not every field: "it is Tuesday
#: afternoon" explains nothing, while "I assumed you had stepped away" explains a held message.
#: Ordered: the first match wins, so the most consequential assumption is the one disclosed.
_DISCLOSABLE = (
    ("lockdown", "I'm in lockdown, so I kept that local"),
    ("guest", "I assumed someone else was listening"),
    ("in_meeting", "I assumed you were in a meeting"),
    ("commute", "I assumed you were travelling"),
    ("focus", "I assumed you were in focus time"),
)


def disclosure(sit: "Situation | None" = None) -> str | None:
    """What Afon assumed, when the assumption changed the answer. `None` when nothing did.

    Callers append this; it is not a sentence on its own. A disclosure for the default situation
    would be noise on every single turn, which is how a disclosure feature gets turned off."""
    sit = sit if sit is not None else current()
    if sit is None:
        return None
    for flag, text in _DISCLOSABLE:
        if getattr(sit, flag, False):
            return text
    if sit.place == "away":
        return "I assumed you had stepped away"
    return None


_CURRENT: contextvars.ContextVar["Situation | None"] = contextvars.ContextVar(
    "afon_situation", default=None)


def current() -> Situation | None:
    """The situation assembled for the turn in flight, or None outside a turn."""
    return _CURRENT.get()


@contextmanager
def situation(perception: Any = None, modes: Any = None, sit: "Situation | None" = None
              ) -> Iterator[Situation]:
    """Assemble once, publish for the turn, restore on the way out.

    `sit` is for tests and for a caller that has already assembled one; passing it skips the
    interpretation entirely so a hermetic test never touches a sensor or a clock."""
    s = sit if sit is not None else assemble(perception, modes)
    token = _CURRENT.set(s)
    try:
        yield s
    finally:
        _CURRENT.reset(token)


def _selfcheck() -> None:
    """One runnable check for the parts that carry a branch. `bench/test_context_object.py` is the
    gate; this is the thirty-second version for someone editing this file."""
    from afon.brain import perception as P

    assert part_of_day(7) == "morning" and part_of_day(23) == "night" and part_of_day(0) == "night"
    assert part_of_day(-1) == UNKNOWN

    class _Snap:
        app, title, idle, ts = "Code.exe", "situation.py", 3.0, 1000.0

    class _P:
        def current(self): return _Snap()
        def in_meeting(self, now=None): return False

    class _M:
        guest = lockdown = commute = False
        def focus_active(self): return False

    P.reset_visual()
    fresh = assemble(P.perceive(_P(), now=1000.0), _M(), now=1000.0)
    assert fresh.place == "desk" and fresh.present and disclosure(fresh) is None

    late = 1000.0 + STALE_AFTER_S + 1
    stale = assemble(P.perceive(_P(), now=late), _M(), now=late)
    assert stale.place == "away" and disclosure(stale) == "I assumed you had stepped away"

    class _Broken:
        def current(self): raise RuntimeError("db gone")
        def in_meeting(self, now=None): raise RuntimeError("db gone")

    broken = assemble(P.perceive(_Broken(), now=1000.0), _M(), now=1000.0)
    assert broken.place == UNKNOWN and broken.place_source == "presence-error"

    with situation(sit=fresh) as s:
        assert current() is s
    assert current() is None
    print("situation: selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
