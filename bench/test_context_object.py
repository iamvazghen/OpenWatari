"""One situation per turn, and Afon says which one he assumed (SYSTEMS.md 27.F1, 27.F2).

Four sources already answered "where is he and what is he doing", and nothing assembled them, so
two halves of one turn could disagree: the answer assuming he was at the desk while proactivity
assumed he had gone. The object fixes the disagreement only while it stays the *single* place the
question is asked — which is why this file checks the wiring and the budget, not just the data.

Four things are checked, and the second is the one that decays:

  * assembly: each source lands in its field, and a source that raises degrades ONE field;
  * the wiring: every turn entry point wraps itself in the context manager. A new entry point, or
    a refactor that drops the wrap, is the realistic way this stops working and nothing else in
    the suite would notice;
  * disclosure: stated when the assumption changed the answer, silent when it did not — a
    disclosure on every turn is noise, and noise is how the feature gets switched off;
  * the budget: assembly is in-process arithmetic, never a model call. S27 says ≤30ms and "no LLM
    call ever", and the cheap way to keep that true is to fail the build when someone imports one.

Hermetic: every source is a stub, so this touches no database, no clock it does not control, and
no network.

    uv run python bench/test_context_object.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import perception as P  # noqa: E402
from afon.brain import situation as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: The turn entry points that must publish a situation. Both live in `agent.py`; a third would be
#: the thing this check exists to catch.
TURN_ENTRY_POINTS = ("respond", "respond_stream")

passed = failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


class _Snap:
    def __init__(self, app="Code.exe", idle=2.0, ts=1000.0):
        self.app, self.title, self.idle, self.ts = app, "a file", idle, ts


class _Presence:
    def __init__(self, snap=_Snap(), meeting=False):
        self._snap, self._meeting = snap, meeting

    def current(self):
        return self._snap

    def in_meeting(self, now=None):
        return self._meeting


class _Modes:
    def __init__(self, focus=False, guest=False, lockdown=False, commute=False):
        self.focus, self.guest, self.lockdown, self.commute = focus, guest, lockdown, commute

    def focus_active(self):
        return self.focus


def perc(presence=None, now=1000.0):
    """Build the snapshot S27 consumes. Going through the real `perceive()` rather than a fake
    Perception is deliberate: it means this test fails if the two modules stop agreeing on the
    shape, which is the seam most likely to rot."""
    return P.perceive(presence if presence is not None else _Presence(), now=now)


def main() -> None:
    print("[1] the object assembles from every source")
    s = S.assemble(perc(), _Modes(), now=1000.0)
    check(s.place == "desk", "a fresh presence sample means at the desk", s.place)
    check(s.place_source == "presence", "the source is named, so a wrong guess is attributable")
    check(s.app == "Code.exe", "the foreground app lands in the object")
    check(s.present is True, "`present` is derived, not stored twice")

    _late = 1000.0 + S.STALE_AFTER_S + 1
    stale = S.assemble(perc(_Presence(_Snap(ts=1000.0)), now=_late), _Modes(), now=_late)
    check(stale.place == "away", "a stale sample means away, not a confident desk", stale.place)

    # Commute must beat a stale desk sample: the last sample IS the desk he left.
    c = S.assemble(perc(_Presence(_Snap(ts=1000.0))), _Modes(commute=True), now=1000.0)
    check(c.place == "travelling" and c.place_source == "mode-commute",
          "commute overrides a desk sample", f"{c.place}/{c.place_source}")

    m = S.assemble(perc(_Presence(meeting=True)), _Modes(), now=1000.0)
    check(m.in_meeting is True, "meeting detection reaches the object")

    for hour, want in ((7, "morning"), (13, "afternoon"), (20, "evening"), (23, "night"),
                       (2, "night")):
        check(S.part_of_day(hour) == want, f"hour {hour} is {want}", S.part_of_day(hour))

    print("\n[2] one dead source degrades one field, never the object")

    class _Broken:
        def current(self):
            raise RuntimeError("store gone")

        def in_meeting(self, now=None):
            raise RuntimeError("store gone")

    b = S.assemble(perc(_Broken()), _Modes(focus=True), now=1000.0)
    check(b.place == S.UNKNOWN and b.place_source == "presence-error",
          "a raising presence yields unknown, not an exception", f"{b.place}/{b.place_source}")
    check(b.focus is True, "the surviving source still populated its field")

    class _BrokenModes:
        focus = guest = lockdown = commute = False

        def focus_active(self):
            raise RuntimeError("no modes")

    b2 = S.assemble(perc(), _BrokenModes(), now=1000.0)
    check(b2.place == "desk", "a raising modes leaves presence intact")

    print("\n[3] assembled once per turn, and read through one accessor")
    check(S.current() is None, "no situation leaks outside a turn")
    with S.situation(sit=s) as published:
        check(S.current() is published, "current() inside the turn IS the assembled object")
        check(S.current() is s, "it is the same object, not a copy that can drift")
    check(S.current() is None, "the turn's situation is cleared on the way out")

    # Nested turns must restore the outer one, or a sub-turn silently rewrites the caller's world.
    with S.situation(sit=s):
        with S.situation(sit=stale):
            check(S.current().place == "away", "an inner turn publishes its own situation")
        check(S.current().place == "desk", "the outer turn's situation is restored")

    agent = (ROOT / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    for name in TURN_ENTRY_POINTS:
        body = agent.split(f"async def {name}(", 1)
        ok = len(body) == 2 and "_turn_situation()" in body[1][:1200]
        check(ok, f"`{name}` publishes a situation for its turn", "no _turn_situation() found")
    check(agent.count("_turn_situation()") >= len(TURN_ENTRY_POINTS),
          "every entry point wraps, none share one wrap by accident")

    print("\n[4] disclosure — stated when it changed the answer, silent when it did not")
    check(S.disclosure(s) is None, "at the desk, nothing to disclose", str(S.disclosure(s)))
    check(S.disclosure(stale) == "I assumed you had stepped away", "away is disclosed")
    check(S.disclosure(S.assemble(perc(), _Modes(focus=True), now=1000.0))
          == "I assumed you were in focus time", "focus is disclosed")
    check(S.disclosure(S.assemble(perc(_Presence(meeting=True)), _Modes(), now=1000.0))
          == "I assumed you were in a meeting", "a meeting is disclosed")
    check(S.disclosure(S.assemble(perc(), _Modes(guest=True), now=1000.0))
          == "I assumed someone else was listening", "a guest is disclosed")

    # Ordering is load-bearing: with several true at once the most consequential must win, or the
    # owner is told about focus time while Afon was actually withholding for lockdown.
    loud = S.assemble(perc(_Presence(meeting=True)),
                      _Modes(focus=True, guest=True, lockdown=True), now=1000.0)
    check(S.disclosure(loud) == "I'm in lockdown, so I kept that local",
          "the most consequential assumption is the one disclosed", str(S.disclosure(loud)))

    with S.situation(sit=stale):
        check(S.disclosure() == "I assumed you had stepped away",
              "disclosure() with no argument reads the turn in flight")
    check(S.disclosure() is None, "and returns None outside a turn")

    print("\n[5] the budget: in-process arithmetic, never a model")
    src = (ROOT / "src/afon/brain/situation.py").read_text(encoding="utf-8")
    check(not re.search(r"^\s*(from|import)\s+.*\bllm\b", src, re.M),
          "situation.py imports no LLM client")
    check("await " not in src, "assembly is synchronous — nothing to await on the answer path")

    snap, m2 = perc(), _Modes()
    t0 = time.perf_counter()
    for _ in range(200):
        S.assemble(snap, m2, now=1000.0)
    per_ms = (time.perf_counter() - t0) * 1000 / 200
    check(per_ms < 30.0, f"assembly is {per_ms:.3f}ms, inside the 30ms budget")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
