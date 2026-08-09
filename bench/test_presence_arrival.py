"""Phase 3 (Perception) — presence-aware proactivity: greet-on-arrival, hermetic.

Locks the away->return edge detection (fires once per genuine return, ignores brief pauses and stale
samples) and the greeting signal source (privacy gate, kind, per-return key). No webcam, no network.

    uv run python bench/test_presence_arrival.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import presence as pmod  # noqa: E402
from afon.brain.presence import Presence, _greeting  # noqa: E402

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
            enabled = True
            def arrival(self, now=None):
                return True
        pmod.PRESENCE = Fake()
        sigs = pmod.presence_signals()
        check("emits one signal on arrival", len(sigs) == 1)
        check("kind is 'presence'", sigs and sigs[0].kind == "presence")
        check("key is per-return unique", sigs and sigs[0].key.startswith("arrival-"))
        check("message is a welcome", sigs and "Welcome back" in sigs[0].message or "Back at it" in sigs[0].message)
        check("urgency is modest (respects quiet hours)", sigs and 0.0 < sigs[0].urgency < 0.7)

        Fake.enabled = False
        check("privacy off-switch mutes the greeting", pmod.presence_signals() == [])

        Fake.enabled = True
        Fake.arrival = lambda self, now=None: False
        check("no arrival -> no signal", pmod.presence_signals() == [])
    finally:
        pmod.PRESENCE = saved

    print("\n[6] greeting is time-aware")
    check("night line differs", "midnight" in _greeting(datetime(2026, 7, 20, 2, 0)))
    check("day line is a welcome", "Welcome back" in _greeting(datetime(2026, 7, 20, 9, 0)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
