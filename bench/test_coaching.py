"""Phase 2 companion — field coaching (skill reviews, level, streak, trend) + the evening offer.

Proves Afon can coach the owner's focus fields (e.g. German): track his level, log 1-10 reviews,
keep a daily streak + trend, know when a field is due, and OFFER an evening review at his level
through the proactive engine (kind 'coaching', so Phase 1's context-gate + dismissal-learning apply).

Hermetic: temp SQLite, injected dates, config overrides. No network, no LLM.

    uv run python bench/test_coaching.py
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
    settings.coaching_enabled = True
    settings.coaching_fields = "german,spanish"

    import afon.brain.coaching as coaching_mod
    from afon.brain.coaching import Coaching, coaching_signals, _in_window
    import afon.brain.tools.coaching as ct

    tz = ZoneInfo(settings.user_tz)
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    c = Coaching(db_path=Path(tmp.name) / "coaching.sqlite")
    coaching_mod.COACH = c   # the evening signal reads the module global
    ct.COACH = c             # the tools resolve COACH from their module

    print("[1] level set/get + score clamping")
    c.set_level("german", "B1")
    check("level stored + read back", c.get_level("german") == "B1")
    p = c.record_review("german", 15, "clamps high", now=datetime(2026, 7, 1, 20, tzinfo=tz))
    check("score clamped to 10", p.recent_avg == 10.0, str(p.recent_avg))
    check("level preserved through a review", c.get_level("german") == "B1")

    print("\n[2] streak: consecutive days build it, a gap resets it")
    c2 = Coaching(db_path=Path(tmp.name) / "streak.sqlite")
    d = datetime(2026, 7, 10, 20, tzinfo=tz)
    c2.record_review("german", 7, now=d)
    p = c2.record_review("german", 8, now=d + timedelta(days=1))
    check("two consecutive days -> streak 2", p.streak == 2, str(p.streak))
    p = c2.record_review("german", 6, now=d + timedelta(days=2))
    check("three consecutive -> streak 3", p.streak == 3, str(p.streak))
    p = c2.record_review("german", 9, now=d + timedelta(days=5))  # gap
    check("a gap resets the streak to 1", p.streak == 1, str(p.streak))
    p = c2.record_review("german", 9, now=d + timedelta(days=5, hours=2))  # same day again
    check("second review same day doesn't inflate the streak", p.streak == 1, str(p.streak))

    print("\n[3] trend + progress")
    c3 = Coaching(db_path=Path(tmp.name) / "trend.sqlite")
    base = datetime(2026, 7, 1, 20, tzinfo=tz)
    for i, s in enumerate([6, 6, 6, 9]):
        c3.record_review("german", s, now=base + timedelta(days=i))
    pr = c3.progress("german")
    check("trend reads 'improving' after a jump", pr.trend == "improving", pr.trend)
    check("reviews counted", pr.reviews == 4, str(pr.reviews))

    print("\n[4] fields() + reviewed_today + due_field")
    check("fields include configured + history", set(c.fields()) >= {"german", "spanish"}, str(c.fields()))
    today = datetime.now(tz)
    check("german not reviewed today (in the live DB c)", not c.reviewed_today("german", today))
    c.record_review("german", 7, now=today)
    check("german now reviewed today", c.reviewed_today("german", today))
    due = c.due_field(today)
    check("due_field returns spanish (german done today)", due == "spanish", str(due))

    print("\n[5] tools: start / record / progress / set-level / list")
    r = await ct.start_skill_check({"field": "german"})
    check("start_skill_check names level + gives a directive", "B1" in r and "review" in r.lower(), r)
    r = await ct.record_skill_review({"field": "spanish", "score": 8, "note": "solid"})
    check("record_skill_review confirms", "8/10" in r, r)
    r = await ct.set_skill_level({"field": "spanish", "level": "A2"})
    check("set_skill_level confirms", "A2" in r, r)
    r = await ct.skill_progress({"field": "german"})
    check("skill_progress reports level + trend", "level B1" in r and "trend" in r, r)
    r = await ct.list_coaching({})
    check("list_coaching lists fields", "german" in r and "spanish" in r, r)

    print("\n[6] evening offer via the proactive engine")
    check("_in_window 19:00 inside 18:00-22:00", _in_window(datetime(2026, 7, 1, 19, tzinfo=tz), "18:00-22:00"))
    check("_in_window 12:00 outside 18:00-22:00", not _in_window(datetime(2026, 7, 1, 12, tzinfo=tz), "18:00-22:00"))
    check("_in_window handles wrap 23:00 in 22:00-02:00", _in_window(datetime(2026, 7, 1, 23, tzinfo=tz), "22:00-02:00"))

    # Fresh store so nothing is reviewed "today" yet -> a field is genuinely due. All-day window so
    # the offer depends only on due-ness (windowing itself is covered by the _in_window checks above).
    c6 = Coaching(db_path=Path(tmp.name) / "evening.sqlite")
    coaching_mod.COACH = c6
    settings.coaching_fields = "german"     # single field -> deterministic offer
    c6.set_level("german", "B1")
    settings.coaching_evening_hours = "00:00-23:59"
    sigs = coaching_signals()
    check("an evening coaching signal is offered", len(sigs) == 1, str(len(sigs)))
    if sigs:
        s = sigs[0]
        check("signal kind is 'coaching' (feedback-learned)", s.kind == "coaching")
        check("urgency 0.62 (held while busy, above base threshold)", abs(s.urgency - 0.62) < 1e-9)
        check("offer names the field + level", "german" in s.message.lower() and "B1" in s.message, s.message)
    # once the field is reviewed today, nothing is due -> no offer (no nagging)
    c6.record_review("german", 7, now=today)
    check("no offer once the field is done today", coaching_signals() == [])

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
