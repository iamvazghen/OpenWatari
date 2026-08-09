"""Per-kind SUPPRESSION counters — telling "correctly restrained" apart from "never generated".

`proactive_state.json.feedback[kind].stats` already counted `shown` / `act` / `dismiss` / `ignore`,
i.e. only what reached the owner. So a capability that generates signals every tick and is held
back by its threshold, and a capability whose signal source has silently broken, produced the
SAME evidence: nothing. Five companion capabilities were read as dormant on exactly that basis.

This proves each gate now leaves a per-kind trace, that the traces persist across a restart, and
— the point of the whole thing — that a restrained kind and an absent kind no longer look alike.

Hermetic: injected clock, injected sources, temp state file. No network, no real time.

    uv run python bench/test_proactive_suppression.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.proactive import ProactiveEngine, Signal  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")
passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    """NB argument order is (name, ok) — the same as test_proactive_learning.py, and the OPPOSITE
    of several other bench files (J3.1). The assert is not decoration: swapping these two args
    silently passes every check, because any non-empty name is truthy. That has happened three
    times in this repo, so the guard stays until there is one shared checker."""
    global passed, failed
    assert isinstance(ok, bool), f"check({name!r}, {ok!r}) — second arg must be a bool; args swapped?"
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" -> {detail}" if detail else ""))


def sig(kind: str, urgency: float, key: str | None = None) -> Signal:
    return Signal(kind=kind, key=key or f"{kind}-1", message=f"{kind} message", urgency=urgency)


def engine(now: datetime, **kw) -> ProactiveEngine:
    """Threshold 0.50, no quiet hours, generous budget unless a test overrides."""
    kw.setdefault("threshold", 0.50)
    kw.setdefault("quiet_hours", "")
    kw.setdefault("daily_budget", 10)
    return ProactiveEngine(clock=lambda: now, **kw)


async def main() -> int:
    now = datetime(2026, 8, 9, 14, 0, tzinfo=TZ)

    print("\n[1] a signal below its threshold is COUNTED, not just dropped")
    e = engine(now)
    check("select() returns nothing", e.select([sig("coaching", 0.30)], now) is None)
    check("held_threshold recorded for that kind", e.held_counts("coaching").get("held_threshold") == 1,
          str(e.held_counts("coaching")))

    print("\n[2] repeat-suppression is distinguishable from threshold rejection")
    e = engine(now, repeat_suppress_minutes=60)
    e._spoken_at["coaching-1"] = now - timedelta(minutes=5)
    check("select() returns nothing", e.select([sig("coaching", 0.90)], now) is None)
    c = e.held_counts("coaching")
    check("held_repeat recorded, held_threshold NOT",
          c.get("held_repeat") == 1 and "held_threshold" not in c, str(c))

    print("\n[3] the loser of a two-signal tick is counted as outranked")
    e = engine(now)
    best = e.select([sig("coaching", 0.60), sig("calendar", 0.90)], now)
    check("the more urgent signal wins", best is not None and best.kind == "calendar")
    check("the loser is counted under ITS kind",
          e.held_counts("coaching").get("held_outranked") == 1, str(e.held_counts("coaching")))
    check("the winner is not counted as held", e.held_counts("calendar") == {},
          str(e.held_counts("calendar")))

    print("\n[4] quiet hours leave a per-kind trace")
    night = datetime(2026, 8, 9, 23, 30, tzinfo=TZ)
    e = ProactiveEngine(clock=lambda: night, threshold=0.50, quiet_hours="22:00-07:00",
                        daily_budget=10, quiet_override=0.95,
                        sources=[lambda: [sig("coaching", 0.60)]])
    check("nothing is interjected at 23:30", await e.maybe_interject() is None)
    check("held_quiet recorded", e.held_counts("coaching").get("held_quiet") == 1,
          str(e.held_counts("coaching")))

    print("\n[5] the busy context gate leaves a per-kind trace")
    e = ProactiveEngine(clock=lambda: now, threshold=0.50, quiet_hours="", daily_budget=10,
                        context_override=0.95, is_busy=lambda: True,
                        sources=[lambda: [sig("coaching", 0.60)]])
    check("nothing is interjected while busy", await e.maybe_interject() is None)
    check("held_busy recorded", e.held_counts("coaching").get("held_busy") == 1,
          str(e.held_counts("coaching")))

    print("\n[6] budget exhaustion has no kind, so it lands on the engine pseudo-kind")
    e = ProactiveEngine(clock=lambda: now, threshold=0.50, quiet_hours="", daily_budget=0,
                        sources=[lambda: [sig("coaching", 0.90)]])
    check("nothing is interjected on a spent budget", await e.maybe_interject() is None)
    check("held_budget recorded on _engine",
          e.held_counts("_engine").get("held_budget") == 1, str(e.held_counts("_engine")))
    check("no kind is blamed for a global gate", e.held_counts("coaching") == {},
          str(e.held_counts("coaching")))

    print("\n[7] counters survive a restart (they are the record of weeks, not of one tick)")
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "proactive_state.json")
        e = ProactiveEngine(clock=lambda: now, threshold=0.50, quiet_hours="", daily_budget=10,
                            state_path=path, sources=[lambda: [sig("coaching", 0.20)]])
        check("tick with a sub-threshold signal interjects nothing", await e.maybe_interject() is None)
        check("state file written", Path(path).exists())
        e2 = ProactiveEngine(clock=lambda: now, threshold=0.50, quiet_hours="", daily_budget=10,
                             state_path=path)
        check("held_threshold survived the reload",
              e2.held_counts("coaching").get("held_threshold") == 1, str(e2.held_counts("coaching")))

        print("\n[8] THE POINT: restrained and never-generated no longer look identical")
        # 'coaching' produced a signal every tick and was held; 'wearable' produced nothing at all.
        check("the restrained kind has evidence", bool(e2.held_counts("coaching")))
        check("the absent kind has none", e2.held_counts("wearable") == {},
              str(e2.held_counts("wearable")))

    print("\n[9] a clean tick writes nothing — the counter must not become a per-tick disk write")
    # Counting saves, not checking for the file: the FIRST tick of a day always saves (day
    # rollover resets the budget), so a file-existence assertion here passes or fails for a
    # reason that has nothing to do with these counters. It did, on the first run of this test.
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "proactive_state.json")
        e = ProactiveEngine(clock=lambda: now, threshold=0.50, quiet_hours="", daily_budget=10,
                            state_path=path, sources=[lambda: []])
        await e.maybe_interject()                       # day rollover happens on this one
        saves = 0
        real_save = e._save_state

        def counting_save() -> None:
            nonlocal saves
            saves += 1
            real_save()

        e._save_state = counting_save                   # type: ignore[method-assign]
        check("no signals at all -> no interjection", await e.maybe_interject() is None)
        check("a tick that held nothing performs no save", saves == 0, f"saves={saves}")

        e._sources = [lambda: [sig("coaching", 0.20)]]   # now something IS held
        check("sub-threshold tick -> no interjection", await e.maybe_interject() is None)
        check("a tick that held something DOES save", saves == 1, f"saves={saves}")

    print(f"\n  {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
