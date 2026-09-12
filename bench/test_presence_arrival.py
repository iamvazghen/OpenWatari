"""Phase 3 (Perception) — presence-aware proactivity: greet-on-arrival, hermetic.

Locks the away->return edge detection (fires once per genuine return, ignores brief pauses and stale
samples) and the greeting signal source (privacy gate, kind, per-return key). No webcam, no network.

    uv run python bench/test_presence_arrival.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import presence as pmod  # noqa: E402
from afon.brain.presence import Presence, _greeting  # noqa: E402
from afon.config import settings  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _p() -> Presence:
    return Presence(db_path=Path(tempfile.mkdtemp()) / "p.sqlite")


def main() -> None:
    now = 1_000_000.0

    print("[1] no greeting without a prior departure")
    p = _p()
    p.record("code", "x", idle=2.0, ts=now)
    check("active-from-boot doesn't greet", p.arrival(now) is False)

    print("\n[2] away then back = one arrival edge")
    p = _p()
    p.record("code", "x", idle=400.0, ts=now)      # away (>= 300)
    check("away sample doesn't greet", p.arrival(now) is False)
    p.record("code", "x", idle=3.0, ts=now + 10)   # back, active
    check("return fires exactly once", p.arrival(now + 10) is True)
    check("no re-fire on the next active poll", p.arrival(now + 10) is False)

    print("\n[3] a brief pause (< away threshold) is NOT a departure")
    p = _p()
    p.record("code", "x", idle=120.0, ts=now)      # idle but under presence_away_seconds (300)
    p.arrival(now)
    p.record("code", "x", idle=2.0, ts=now + 5)
    check("120s pause -> no greeting", p.arrival(now + 5) is False)

    print("\n[4] a stale sample never fakes a transition")
    p = _p()
    p.record("code", "x", idle=400.0, ts=now)
    p.arrival(now)
    # a return sample that is now STALE (older than 3*poll) must not count
    check("stale return -> no greeting", p.arrival(now + 10_000) is False)

    print("\n[5] greeting source: privacy gate + shape")
    saved = pmod.PRESENCE
    try:
        class Fake:
            # Shaped like Presence, deliberately: a stub that agrees with a broken caller instead
            # of with the class is how a defect survives its own test.
            enabled = True
            last_absence_s = 0.0
            def arrival(self, now=None):
                return True
        pmod.PRESENCE = Fake()
        sigs = asyncio.run(pmod.presence_signals())
        check("emits one signal on arrival", len(sigs) == 1)
        check("kind is 'presence'", sigs and sigs[0].kind == "presence")
        check("key is per-return unique", sigs and sigs[0].key.startswith("arrival-"))
        check("message is a welcome", sigs and "Welcome back" in sigs[0].message or "Back at it" in sigs[0].message)
        check("urgency is modest (respects quiet hours)", sigs and 0.0 < sigs[0].urgency < 0.7)

        Fake.enabled = False
        check("privacy off-switch mutes the greeting", asyncio.run(pmod.presence_signals()) == [])

        Fake.enabled = True
        Fake.arrival = lambda self, now=None: False
        check("no arrival -> no signal", asyncio.run(pmod.presence_signals()) == [])
    finally:
        pmod.PRESENCE = saved

    print("\n[6] greeting is time-aware")
    check("night line differs", "midnight" in _greeting(datetime(2026, 7, 20, 2, 0)))
    check("day line is a welcome", "Welcome back" in _greeting(datetime(2026, 7, 20, 9, 0)))

    print("\n[7] 43.F2 — departure and return are symmetric")
    # Before this, a departure left no trace: ten minutes and ten hours both produced the identical
    # "Welcome back, sir." A return that cannot tell them apart cannot say anything useful about
    # either, which is why the catch-up half of this floor had nothing to stand on.
    p2 = pmod.Presence(db_path=Path(tempfile.mkdtemp()) / "presence.sqlite")
    base = 100_000.0
    p2.record("Code.exe", "x", idle=1.0, ts=base)
    p2.arrival(base)
    gone = settings.presence_away_seconds + 60
    p2.record("Code.exe", "x", idle=gone, ts=base + gone)
    p2.arrival(base + gone)
    p2.record("Code.exe", "x", idle=1.0, ts=base + gone + 10)
    check("the return still fires once", p2.arrival(base + gone + 10) is True)
    check("...and the absence has a LENGTH now", p2.last_absence_s > gone - 5, p2.last_absence_s)
    check("...measured from when he stopped touching it, not when the poll noticed",
          abs(p2.last_absence_s - (gone + 10)) < 120, p2.last_absence_s)

    check("a short absence reads in minutes", "minutes" in pmod._absence_phrase(600))
    check("an hour is said as an hour", pmod._absence_phrase(3600) == "an hour")
    check("several hours are counted", "hours" in pmod._absence_phrase(5 * 3600))
    check("a whole day is not counted in hours", pmod._absence_phrase(20 * 3600) == "a while")

    # The rule the floor actually states: a catch-up only when there is something to catch up on.
    import afon.brain.tools.inbox as ib

    real_sources = dict(ib.SOURCES)

    async def _empty():
        return []

    async def _one():
        return [ib.Waiting("email", "t1", "Jane", "the invoice", 2, base)]

    try:
        ib.SOURCES.clear()
        ib.SOURCES["email"] = _empty
        check("a coffee break gets no catch-up at all",
              asyncio.run(pmod.while_you_were_gone(300)) == "")
        check("a long absence with nothing waiting ALSO gets none — an empty catch-up trains him "
              "to ignore the full one",
              asyncio.run(pmod.while_you_were_gone(4 * 3600)) == "")

        ib.SOURCES["email"] = _one
        check("a short absence with mail waiting still gets none — he was at the coffee machine",
              asyncio.run(pmod.while_you_were_gone(300)) == "")
        said = asyncio.run(pmod.while_you_were_gone(4 * 3600))
        check("a long absence with something waiting gets a catch-up", said != "", said)
        check("...saying how long he was gone", "hours" in said, said)
        check("...how much arrived", "2 things" in said, said)
        check("...and where the newest came from", "Jane" in said and "email" in said, said)

        async def _boom():
            raise RuntimeError("mailbox on fire")

        ib.SOURCES["telegram"] = _boom
        said = asyncio.run(pmod.while_you_were_gone(4 * 3600))
        check("a channel it couldn't read is admitted, not papered over",
              "couldn't check everything" in said, said)

        ib.SOURCES.clear()
        ib.SOURCES["email"] = _boom
        check("a catch-up that fails entirely falls back to the plain greeting, never an error",
              asyncio.run(pmod.while_you_were_gone(4 * 3600)) == "")
    finally:
        ib.SOURCES.clear()
        ib.SOURCES.update(real_sources)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
