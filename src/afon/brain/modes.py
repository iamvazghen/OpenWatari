"""Runtime modes — the lightweight, non-destructive 'protocols' from the Phase-X expansion.

Unlike the password-gated protocols (goodnight/phoenix/ragnarok, which run privileged system
scripts), these are *behavioural modes* Afon flips on request and that other parts of the brain
read:

  * **focus / deepwork** — hold non-urgent interjections for a while (the proactive tick checks this).
  * **lockdown / privacy** — go quiet: no unprompted speech, a cue to stop listening.
  * **guest** — relax speaker-biometrics so other people can interact too.
  * **commute** — keep replies extra brief (a hint the persona/edge can honour).

State is process-local and resets on restart (a mode is a "for now" thing). All times are in the
user's timezone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from afon.config import settings

USER_TZ = ZoneInfo(settings.user_tz)


class Modes:
    def __init__(self) -> None:
        self._focus_until: datetime | None = None
        self._lockdown = False
        self._guest = False
        self._commute = False

    def _now(self) -> datetime:
        return datetime.now(USER_TZ)

    # ---- focus ------------------------------------------------------------------------
    def set_focus(self, minutes: int) -> None:
        self._focus_until = self._now() + timedelta(minutes=max(1, int(minutes)))

    def focus_active(self, now: datetime | None = None) -> bool:
        if self._focus_until is None:
            return False
        return (now or self._now()) < self._focus_until

    # ---- lockdown / guest / commute ---------------------------------------------------
    @property
    def lockdown(self) -> bool:
        return self._lockdown

    @lockdown.setter
    def lockdown(self, on: bool) -> None:
        self._lockdown = bool(on)

    @property
    def guest(self) -> bool:
        return self._guest

    @guest.setter
    def guest(self, on: bool) -> None:
        self._guest = bool(on)

    @property
    def commute(self) -> bool:
        return self._commute

    @commute.setter
    def commute(self, on: bool) -> None:
        self._commute = bool(on)

    # ---- composite --------------------------------------------------------------------
    def proactivity_suppressed(self, now: datetime | None = None) -> bool:
        """True when an unprompted interjection should be withheld (lockdown or active focus)."""
        return self._lockdown or self.focus_active(now)

    def clear(self) -> None:
        self._focus_until = None
        self._lockdown = False
        self._guest = False
        self._commute = False

    def status(self) -> str:
        on = []
        if self.focus_active():
            mins = int((self._focus_until - self._now()).total_seconds() // 60) + 1
            on.append(f"focus ({mins} min left)")
        if self._lockdown:
            on.append("lockdown")
        if self._guest:
            on.append("guest")
        if self._commute:
            on.append("commute")
        return ", ".join(on) if on else "normal"


MODES = Modes()
