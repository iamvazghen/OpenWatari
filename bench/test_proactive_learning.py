"""Phase 1 — context-aware proactivity + dismissal-learning feedback loop.

Proves the two trust primitives on top of the Phase 0 presence layer:

  * CONTEXT GATE — a routine nudge is HELD (not spoken, budget untouched) while the owner is busy
    (deep in code/docs, in a meeting, watching media); a signal urgent enough (>= context_override)
    still interrupts. When he's at a natural break, routine nudges flow.
  * FEEDBACK LEARNING — each interjection's reaction (act / dismiss / ignore) adjusts a per-KIND
    penalty: dismiss a kind a few times and it stops surfacing; act on it and it eases back. The
    penalty decays (half-life) and survives a brain restart. A short keyword classifier grades the
    owner's reply high-precision (only clear yes/no moves the needle).

Hermetic: injected clock + busy flag + temp state file. No network, no real time.

    uv run python bench/test_proactive_learning.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def main() -> None:
    from afon.config import settings
    settings.presence_poll_seconds = 30
    settings.presence_engaged_idle_seconds = 25
    from afon.brain.presence import Presence
    from afon.brain.proactive import ProactiveEngine, Signal, classify_reaction

    tz = ZoneInfo(settings.user_tz)
    t0 = datetime(2026, 7, 14, 14, 0, tzinfo=tz)
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    print("[1] presence context detection: busy vs a good moment")
    pr = Presence(db_path=Path(tmp.name) / "p.sqlite")
    nowts = time.time()
    pr.record("Code", "server.py", 5.0, ts=nowts)
    check("deep in code -> busy", pr.busy(nowts) and "code" in pr.busy_reason(nowts))
    pr.record("chrome", "reddit", 40.0, ts=nowts)
    check("casual browsing -> NOT busy (a good moment)", not pr.busy(nowts))
    pr.record("vlc", "movie.mkv", 300.0, ts=nowts)
    check("watching media -> busy", pr.busy(nowts))
    pr.record("Zoom", "Zoom Meeting", 3.0, ts=nowts)
    check("in a meeting -> busy", pr.in_meeting(nowts) and pr.busy(nowts))
    pr.record("Code", "old", 5.0, ts=nowts - 10_000)  # stale
    check("stale sample -> not busy (unknown)", not pr.busy(nowts))

    print("\n[2] context gate holds routine nudges while busy; urgent ones still interrupt")
    busy = [True]

    async def emit(msg, urg, listening):
        return "voice"

    def routine():
        return [Signal(key="tip", message="you often play lofi now", urgency=0.7, kind="pattern")]

    def urgent():
        return [Signal(key="mtg", message="call in 3 min", urgency=0.9, kind="calendar")]

    eng = ProactiveEngine(emit=emit, sources=[routine], is_listening=lambda: True,
                          is_busy=lambda: busy[0], clock=lambda: t0, quiet_hours="",
                          daily_budget=10, threshold=0.6, context_override=0.85)
    held = await eng.maybe_interject(t0)
    check("routine nudge HELD while busy", held is None)
    check("budget untouched when held", eng._used_today == 0)
    eng._sources = [urgent]
    fired = await eng.maybe_interject(t0)
    check("urgent nudge interrupts even while busy", fired is not None and fired.channel == "voice")
    busy[0] = False
    eng._sources = [routine]
    ok = await eng.maybe_interject(t0)
    check("routine nudge flows at a good (not-busy) moment", ok is not None)

    print("\n[3] dismissal learning: keep dismissing a KIND and it stops surfacing")
    state = str(Path(tmp.name) / "state.json")
    eng2 = ProactiveEngine(emit=emit, sources=[], is_listening=lambda: True,
                           clock=lambda: t0, quiet_hours="", daily_budget=10, threshold=0.6,
                           state_path=state)
    base = eng2.effective_threshold("pattern", t0)
    check("base threshold with no history == 0.6", abs(base - 0.6) < 1e-9, str(base))
    eng2.record_feedback("pattern", "dismiss", t0)
    eng2.record_feedback("pattern", "dismiss", t0)
    thr2 = eng2.effective_threshold("pattern", t0)
    check("two dismissals raise the kind's threshold above 0.7", thr2 > 0.7, str(thr2))
    check("a 0.7 nudge of that kind is now filtered out",
          eng2.select([Signal("k", "m", 0.7, kind="pattern")], t0) is None)
    check("a genuinely urgent 0.95 of that kind still gets through",
          eng2.select([Signal("k", "m", 0.95, kind="pattern")], t0) is not None)
    check("a DIFFERENT kind is unaffected",
          eng2.select([Signal("k2", "m", 0.7, kind="calendar")], t0) is not None)

    print("\n[4] acting on a nudge eases the kind back")
    before = eng2.effective_threshold("pattern", t0)
    eng2.record_feedback("pattern", "act", t0)
    check("acting lowers the penalty", eng2.effective_threshold("pattern", t0) < before)

    print("\n[5] an ignored interjection (no reaction before the next one) is penalised")
    eng3 = ProactiveEngine(emit=emit, sources=[], is_listening=lambda: True, clock=lambda: t0,
                           quiet_hours="", daily_budget=10, threshold=0.6)
    eng3._sources = [lambda: [Signal("a", "first", 0.7, kind="news")]]
    await eng3.maybe_interject(t0)                     # emit news -> pending
    eng3._sources = [lambda: [Signal("b", "second", 0.7, kind="health")]]
    await eng3.maybe_interject(t0)                     # emit health -> 'news' never got a reaction
    check("unaddressed prior interjection recorded as ignore",
          eng3._fb_stats.get("news", {}).get("ignore") == 1, str(eng3._fb_stats))

    print("\n[6] learning persists across a restart, and decays over time")
    eng4 = ProactiveEngine(emit=emit, sources=[], is_listening=lambda: True, clock=lambda: t0,
                           quiet_hours="", daily_budget=10, threshold=0.6, state_path=state)
    check("penalty reloaded after restart", eng4.effective_threshold("pattern", t0) > 0.6)
    now_decayed = t0 + timedelta(days=14)             # two half-lives later
    check("penalty decays toward base over time",
          eng4.effective_threshold("pattern", now_decayed) < eng4.effective_threshold("pattern", t0))

    print("\n[7] pending-feedback window + reaction classifier")
    eng5 = ProactiveEngine(emit=emit, sources=[lambda: [Signal("x", "m", 0.7, kind="pattern")]],
                           is_listening=lambda: True, clock=lambda: t0, quiet_hours="", daily_budget=10)
    await eng5.maybe_interject(t0)
    check("pending within 6 min returns the kind", eng5.pending_feedback(t0 + timedelta(minutes=5)) == "pattern")
    check("pending after the window returns None", eng5.pending_feedback(t0 + timedelta(minutes=7)) is None)
    check("classify 'stop reminding me' -> negative", classify_reaction("stop reminding me") == "negative")
    check("classify 'yes do it' -> positive", classify_reaction("yes do it") == "positive")
    check("classify a normal question -> neutral",
          classify_reaction("what's the weather in Berlin?") == "neutral")

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
