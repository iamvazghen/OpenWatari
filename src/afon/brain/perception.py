"""One sensing snapshot, and every fact in it says how old it is (SYSTEMS.md 26.F2, 26.F3).

S26 turns sensors into facts. S27 (`situation.py`) turns facts into a situation. The line matters
and the plan states it: **S26 never decides what a state means, S27 never touches a camera.** So
this module reports "the last visual check, ninety seconds ago, saw a person" and says nothing
about whether that means the owner is available.

Before this, each caller assembled its own view from whichever sensor it knew about, and the worst
version of that bug is silent: a visual verdict from twenty minutes ago read as if it were current,
because a bare `True` carries no age. Every fact here therefore has a timestamp and a staleness
rule, and `describe()` refuses to assert a stale fact as present tense.

**`perceive()` never captures.** It reads what is already known. Turning the camera on costs
seconds, wakes a device the owner can see, and — on this laptop specifically — walks into the
PortAudio/Intel Smart Sound territory where device enumeration has hard-segfaulted before. A
snapshot on the answer path must be arithmetic over cached facts, which is also what keeps S27
inside its 30ms budget. A caller that genuinely needs a fresh look calls the camera tool, and that
tool feeds `record_visual()` on its way past.

    from afon.brain.perception import perceive
    p = perceive()
    p.visual.value, p.visual.age_s(), p.visual.stale

ponytail: the visual fact lives in a module global, not a store. It is worthless after a restart
by definition — a verdict about who was in the room an hour before the process died should not
survive — so persisting it would be work that produces a wrong answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

#: Per-fact staleness. These differ on purpose: a room empties in seconds, while "which app is in
#: the foreground" stays true across a poll interval. One shared constant would make the visual
#: fact too trusting or the activity fact too timid.
MAX_AGE_S = {"presence": 300.0, "visual": 120.0, "activity": 300.0, "meeting": 300.0}

UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fact:
    """A sensed value and when it was sensed. `at = 0.0` means never sensed, which is different
    from sensed-and-false and must stay different — 'I have not looked' is not 'nobody is there'."""

    value: Any = None
    at: float = 0.0
    source: str = UNKNOWN
    max_age_s: float = 300.0

    @property
    def known(self) -> bool:
        return self.at > 0.0 and self.value is not None

    def age_s(self, now: float | None = None) -> float:
        """Seconds since this was sensed. `-1.0` when it never was."""
        if not self.known:
            return -1.0
        return max(0.0, (time.time() if now is None else now) - self.at)

    def is_stale(self, now: float | None = None) -> bool:
        """Unknown counts as stale: a caller that treats unknown as fresh is the bug 26.F3 names."""
        if not self.known:
            return True
        return self.age_s(now) > self.max_age_s


@dataclass(frozen=True)
class Perception:
    """What the sensors last reported. Assembled by `perceive()`, consumed by `situation.assemble`."""

    presence: Fact
    visual: Fact
    activity: Fact
    meeting: Fact
    ts: float = 0.0

    def describe(self, now: float | None = None) -> str:
        """A sentence that never presents a stale fact as current.

        This is 26.F3 in one method: a fresh fact is stated plainly, a stale one is stated with its
        age, and an unsensed one is stated as not looked at. All three are useful; only the first
        two are ever confused for each other, which is why the age is in the text and not in a
        field somebody has to remember to read."""
        out = []
        for name in ("presence", "visual", "activity", "meeting"):
            f: Fact = getattr(self, name)
            if not f.known:
                out.append(f"{name}: not sensed")
            elif f.is_stale(now):
                out.append(f"{name}: {f.value} (stale, {f.age_s(now):.0f}s ago)")
            else:
                out.append(f"{name}: {f.value}")
        return " · ".join(out)


#: Last visual verdict, set by the camera path. A tuple rather than a Fact so the writer cannot
#: accidentally hand in a stale timestamp: the clock is read here, at the moment of recording.
_VISUAL: tuple[Any, float, str] = (None, 0.0, UNKNOWN)


def record_visual(value: Any, source: str = "camera") -> None:
    """Called by the camera path after a real look. Fail-quiet: recording a fact must never be
    able to break the tool that sensed it."""
    global _VISUAL
    try:
        _VISUAL = (value, time.time(), source)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"perception: could not record visual ({type(e).__name__})")


def reset_visual() -> None:
    """Tests only — the module global would otherwise leak between cases."""
    global _VISUAL
    _VISUAL = (None, 0.0, UNKNOWN)


def perceive(presence: Any = None, now: float | None = None) -> Perception:
    """One snapshot of what is already known. Reads no camera and blocks on nothing.

    `presence` is injected so a hermetic test needs no store; production passes the singleton.
    """
    now = time.time() if now is None else now

    pres = Fact(max_age_s=MAX_AGE_S["presence"])
    act = Fact(max_age_s=MAX_AGE_S["activity"])
    meet = Fact(max_age_s=MAX_AGE_S["meeting"])
    if presence is not None:
        try:
            snap = presence.current()
            if snap is not None:
                pres = Fact(value="at-screen" if float(snap.idle) < 120 else "idle",
                            at=float(snap.ts), source="presence",
                            max_age_s=MAX_AGE_S["presence"])
                act = Fact(value=snap.app or UNKNOWN, at=float(snap.ts), source="presence",
                           max_age_s=MAX_AGE_S["activity"])
                # Meeting detection is a read of the same sample, so it carries that sample's
                # timestamp — not `now`. Stamping it now would make a twenty-minute-old verdict
                # look freshly sensed, which is the exact confusion 26.F3 exists to prevent.
                meet = Fact(value=bool(presence.in_meeting(now)), at=float(snap.ts),
                            source="presence", max_age_s=MAX_AGE_S["meeting"])
        except Exception as e:  # noqa: BLE001
            logger.debug(f"perception: presence unavailable ({type(e).__name__})")
            pres = Fact(source="presence-error", max_age_s=MAX_AGE_S["presence"])
            act = Fact(source="presence-error", max_age_s=MAX_AGE_S["activity"])
            meet = Fact(source="presence-error", max_age_s=MAX_AGE_S["meeting"])

    value, at, source = _VISUAL
    visual = Fact(value=value, at=at, source=source, max_age_s=MAX_AGE_S["visual"])
    return Perception(presence=pres, visual=visual, activity=act, meeting=meet, ts=now)


def _selfcheck() -> None:
    """The thirty-second version. `bench/test_perception_snapshot.py` is the gate."""
    class _Snap:
        app, title, idle, ts = "Code.exe", "x", 3.0, 1000.0

    class _P:
        def current(self): return _Snap()
        def in_meeting(self, now=None): return False

    reset_visual()
    p = perceive(_P(), now=1000.0)
    assert p.presence.value == "at-screen" and not p.presence.is_stale(1000.0)
    assert p.activity.value == "Code.exe"
    assert not p.visual.known and p.visual.is_stale(1000.0)
    assert "not sensed" in p.describe(1000.0)

    old = perceive(_P(), now=1000.0 + MAX_AGE_S["presence"] + 1)
    assert old.presence.is_stale(1000.0 + MAX_AGE_S["presence"] + 1)
    assert "stale" in old.describe(1000.0 + MAX_AGE_S["presence"] + 1)

    record_visual("person")
    fresh = perceive(_P())
    assert fresh.visual.value == "person" and not fresh.visual.is_stale()
    reset_visual()
    print("perception: selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
