"""Phase 10 — proactive engine, offline + hermetic.

Verifies the full decision policy with an injected clock + signal sources + emitter (no mic, no
network, no waiting): relevance threshold, most-urgent selection, interruption budget, day rollover,
repeat-suppression, quiet-hours suppression, the quiet-hours override to a silent push, voice vs
push channel choice, and the clarify/confirm helpers.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0
TZ = ZoneInfo("Europe/Berlin")


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def at(h: int, m: int = 0, day: int = 11) -> datetime:
    return datetime(2026, 6, day, h, m, tzinfo=TZ)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make_engine(signals, listening=True, **kw):
    from jarvis.brain.proactive import ProactiveEngine

    sent = []

    async def emit(message, urgency, speak):
        channel = "voice" if speak else "push"
        sent.append((message, urgency, channel))
        return channel

    clock = Clock(at(10, 0))
    eng = ProactiveEngine(
        emit=emit,
        sources=[lambda: list(signals)],
        is_listening=lambda: listening,
        clock=clock,
        quiet_hours="23:00-07:00",
        daily_budget=kw.get("budget", 6),
        threshold=kw.get("threshold", 0.6),
        repeat_suppress_minutes=kw.get("suppress", 120),
        quiet_override=kw.get("override", 0.95),
    )
    return eng, sent, clock


def main() -> None:
    from jarvis.brain.proactive import (
        Signal,
        confirm_required,
        in_quiet_hours,
        needs_clarification,
    )

    print("[1] threshold gate — below-threshold signals stay silent")
    eng, sent, clk = make_engine([Signal("a", "minor note", 0.3)])
    res = asyncio.run(eng.maybe_interject())
    check("low-urgency signal not voiced", res is None and sent == [], str(sent))

    print("\n[2] most-urgent eligible signal is chosen, and spoken when listening")
    eng, sent, clk = make_engine([
        Signal("a", "standup in 5", 0.7, kind="calendar"),
        Signal("b", "stretch break", 0.65, kind="routine"),
    ])
    res = asyncio.run(eng.maybe_interject())
    check("an interjection happened", res is not None)
    check("it picked the most urgent", res and res.signal.key == "a", res and res.signal.key)
    check("channel was voice (device listening)", res and res.channel == "voice", res and res.channel)

    print("\n[3] interruption budget caps interjections per day")
    eng, sent, clk = make_engine(
        [Signal("a", "thing", 0.9)], budget=2, suppress=0  # suppress=0 so the same key can repeat
    )
    for _ in range(5):
        asyncio.run(eng.maybe_interject())
    check("only 'budget' interjections emitted", len(sent) == 2, f"emitted {len(sent)}")
    check("budget_remaining hits zero", eng.budget_remaining == 0, str(eng.budget_remaining))

    print("\n[4] budget resets on a new day")
    clk.t = at(10, 0, day=12)
    asyncio.run(eng.maybe_interject())
    check("a new day frees the budget again", len(sent) == 3, f"emitted {len(sent)}")

    print("\n[5] repeat-suppression — same signal not repeated within the window")
    eng, sent, clk = make_engine([Signal("a", "same thing", 0.9)], suppress=120)
    asyncio.run(eng.maybe_interject())
    clk.t = at(10, 30)            # 30 min later, inside the 120-min window
    asyncio.run(eng.maybe_interject())
    check("suppressed within window (one emit)", len(sent) == 1, f"emitted {len(sent)}")
    clk.t = at(13, 0)            # >120 min later
    asyncio.run(eng.maybe_interject())
    check("repeats after the window passes", len(sent) == 2, f"emitted {len(sent)}")

    print("\n[6] quiet hours suppress routine-grade signals entirely")
    eng, sent, clk = make_engine([Signal("a", "routine note", 0.7)])
    clk.t = at(2, 0)            # 02:00 is inside 23:00–07:00
    res = asyncio.run(eng.maybe_interject())
    check("routine signal held during quiet hours", res is None and sent == [], str(sent))

    print("\n[7] quiet-hours override reaches him — as a silent push, never voiced")
    eng, sent, clk = make_engine([Signal("a", "URGENT: door unlocked", 0.97)], listening=True)
    clk.t = at(2, 0)
    res = asyncio.run(eng.maybe_interject())
    check("override emitted", res is not None and len(sent) == 1)
    check("override used push, not voice", sent and sent[0][2] == "push", str(sent[:1]))

    print("\n[8] no device listening -> push even outside quiet hours")
    eng, sent, clk = make_engine([Signal("a", "heads up", 0.8)], listening=False)
    res = asyncio.run(eng.maybe_interject())
    check("falls back to push when nobody's listening", sent and sent[0][2] == "push", str(sent[:1]))

    print("\n[9] in_quiet_hours boundary maths (wrapping window)")
    check("02:00 is quiet", in_quiet_hours(at(2, 0), "23:00-07:00"))
    check("12:00 is not quiet", not in_quiet_hours(at(12, 0), "23:00-07:00"))
    check("07:00 (end, exclusive) is not quiet", not in_quiet_hours(at(7, 0), "23:00-07:00"))
    check("23:00 (start, inclusive) is quiet", in_quiet_hours(at(23, 0), "23:00-07:00"))

    print("\n[10] confirm policy — outward/destructive gated, reads not")
    check("send_email needs confirm", confirm_required("send_email"))
    check("delete file needs confirm", confirm_required("file_op", {"action": "delete_file"}))
    check("create file does NOT need confirm", not confirm_required("file_op", {"action": "create_file"}))
    check("recall never needs confirm", not confirm_required("recall"))
    check("run_protocol needs confirm", confirm_required("run_protocol"))

    print("\n[10b] state destruction gated; frictionless capture stays frictionless")
    for t in ("delete_macro", "delete_task", "forget", "write_vault",
              "drop_objective", "complete_objective"):
        check(f"{t} needs confirm", confirm_required(t))
    # An update that adds information is not a loss, so it must not interrogate him — that path is
    # the one he uses by voice all day. Overwriting prose or blanking a field is a loss, so it does.
    check("update: progress NOT gated", not confirm_required("update_task", {"progress": 50}))
    check("update: appending a note NOT gated",
          not confirm_required("update_task", {"note": "called them back"}))
    check("update: moving a deadline NOT gated",
          not confirm_required("update_task", {"deadline": "friday"}))
    check("update: overwriting the title IS gated",
          confirm_required("update_task", {"title": "new name"}))
    check("update: clearing a field IS gated", confirm_required("update_task", {"deadline": ""}))
    check("notion: status change NOT gated",
          not confirm_required("notion_update_task", {"status": "In progress"}))
    check("notion: clearing the reminder IS gated",
          confirm_required("notion_update_task", {"reminder": "  "}))
    check("adding a task is never gated", not confirm_required("add_task"))

    print("\n[11] clarify heuristic — bare/ambiguous asks get a question first")
    check("empty -> clarify", needs_clarification("   "))
    check("'do it' -> clarify", needs_clarification("do it"))
    check("bare 'that' -> clarify", needs_clarification("that"))
    check("a real request -> act", not needs_clarification("what's the weather in Yerevan"))

    print("\n[12] calendar signal source — imminent timed events nudge, all-day skipped")
    from datetime import timedelta, timezone

    from jarvis.brain.tools import calendar as cal

    now = datetime.now(timezone.utc)
    soon = (now + timedelta(minutes=10)).isoformat()
    fake = {"items": [
        {"id": "e1", "summary": "Standup", "start": {"dateTime": soon}},
        {"id": "e2", "summary": "A birthday", "start": {"date": "2026-06-30"}},  # all-day -> skip
    ]}
    cal.configured = lambda: True

    async def _fake_get(url, params=None):
        return fake

    cal.api_get = _fake_get
    sigs = asyncio.run(cal.calendar_signals())
    check("one signal (all-day skipped)", len(sigs) == 1, f"got {len(sigs)}")
    check("timed calendar event", bool(sigs) and sigs[0].kind == "calendar" and "Standup" in sigs[0].message)
    check("urgency clears threshold", bool(sigs) and sigs[0].urgency >= 0.6)
    cal.configured = lambda: False
    check("unconfigured -> fail-quiet []", asyncio.run(cal.calendar_signals()) == [])

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
