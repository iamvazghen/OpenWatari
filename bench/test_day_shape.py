"""21.F1/21.F2 — Afon knows the shape of a normal day, and can say when one will not work.

Both floors are about the same missing thing. `routines.json` held two commitments with no times and
no windowed entries at all, so everything downstream — the morning planning prompt, routine anchors,
focus protection — reasoned about a day that does not exist. And nothing anywhere looked at a day as
a whole: Afon could say "you have a dentist in ten minutes" but not "two of these are at the same
time and you cannot be at both".

What this asserts:

  * a routine draft comes from OBSERVED activity, carries its evidence, and is never installed by
    the thing that drafted it;
  * a habit needs enough days behind it — two afternoons is not a routine;
  * the validator catches every structural fault the live loader silently swallows;
  * double-booking, missing travel time and unbroken runs are each detected and NAMED;
  * a tight day, an unnamed location and an all-day event are not clashes, because a checker that
    cries wolf gets switched off.

Hermetic: a temp activity database and synthetic events. No calendar, no network.

    uv run python bench/test_day_shape.py
"""

from __future__ import annotations

import sqlite3
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


def _db(tmp: Path, tz, now, plan) -> Path:
    """plan: {day_offset: [(hour, app), ...]}"""
    p = tmp / "presence.sqlite"
    conn = sqlite3.connect(str(p))
    try:
        conn.execute("DROP TABLE IF EXISTS activity")  # reused path across sections
        conn.execute("CREATE TABLE activity (ts REAL, app TEXT, title TEXT, idle REAL)")
        for offset, entries in plan.items():
            midnight = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0,
                                                              microsecond=0)
            for hour, app, idle in entries:
                conn.execute("INSERT INTO activity VALUES (?,?,?,?)",
                             ((midnight + timedelta(hours=hour)).timestamp(), app, "t", idle))
        conn.commit()
    finally:
        conn.close()
    return p


def main() -> None:
    from afon.brain import routine_draft as rd
    from afon.brain import schedule as sch
    from afon.config import settings

    tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    tmp = Path(tmpdir.name)
    rd.draft_path = lambda: tmp / "draft.json"  # type: ignore[assignment]
    tz = ZoneInfo(settings.user_tz)
    now = datetime.now(tz)

    print("[1] a draft comes from what he actually did, and carries its evidence")
    plan = {}
    for day in range(1, 11):
        entries = [(9, "Code.exe", 0.0), (10, "Code.exe", 0.0)]
        if day <= 2:
            entries.append((21, "steam.exe", 0.0))       # twice is not a habit
        plan[day] = entries
    drafts = rd.observe(db=_db(tmp, tz, now, plan), now=now.timestamp())
    check("a daily block becomes one drafted routine", [d["key"] for d in drafts] == ["coding-09"],
          str(drafts))
    check("adjacent hours merge into one window", drafts[0]["window"] == "09:00-10:59",
          str(drafts))
    check("the draft says how many days it held on", "of the last" in drafts[0]["evidence"],
          str(drafts))
    check("the draft says it was observed, not assumed", drafts[0]["source"] == "observed")
    check("twice in ten days is NOT drafted as a routine",
          not [d for d in drafts if "gaming" in d["key"]], str(drafts))
    check("a valid draft is structurally valid", rd.validate(drafts) == [], str(rd.validate(drafts)))

    print("\n[2] it refuses to claim a habit it has no record of")
    empty_dir = tmp / "empty"
    empty_dir.mkdir(exist_ok=True)
    thin = rd.observe(db=_db(empty_dir, tz, now, {}), now=now.timestamp())
    check("no observation at all yields no draft", thin == [], str(thin))
    two_days = rd.observe(db=_db(tmp, tz, now, {1: [(9, "Code.exe", 0.0)],
                                                2: [(9, "Code.exe", 0.0)]}), now=now.timestamp())
    check("two days of record is below the floor", two_days == [], str(two_days))
    idle_only = rd.observe(db=_db(tmp, tz, now,
                                  {d: [(9, "Code.exe", 9999.0)] for d in range(1, 11)}),
                           now=now.timestamp())
    check("an idle machine is not evidence of work", idle_only == [], str(idle_only))

    print("\n[3] drafting NEVER installs — the proposal and the adoption are separate acts")
    from afon.brain.proactive_signals import _ROUTINES_PATH

    before = _ROUTINES_PATH.read_text(encoding="utf-8") if _ROUTINES_PATH.exists() else None
    rd.write_draft(drafts)
    after = _ROUTINES_PATH.read_text(encoding="utf-8") if _ROUTINES_PATH.exists() else None
    check("writing a draft does not touch the live routines file", before == after)
    check("the draft round-trips", rd.read_draft() == drafts)
    check("the spoken draft quotes the window", "09:00" in rd.spoken(drafts))
    check("...and its evidence, so he knows which lines to trust",
          "of the last" in rd.spoken(drafts))
    check("with nothing observed, it says so rather than inventing a day",
          "long enough" in rd.spoken([]))

    print("\n[4] the validator catches what the live loader swallows silently")
    bad = [
        ("a windowless, non-dynamic entry can never fire",
         [{"key": "a", "message": "m"}], "never fire"),
        ("a backwards window", [{"key": "a", "message": "m", "window": "18:00-09:00"}],
         "ends before it starts"),
        ("an unreadable window", [{"key": "a", "message": "m", "window": "half nine"}],
         "unreadable"),
        ("a duplicate key", [{"key": "a", "message": "m", "window": "09:00-10:00"},
                             {"key": "a", "message": "m", "window": "11:00-12:00"}], "duplicate"),
        ("an empty message", [{"key": "a", "message": "  ", "window": "09:00-10:00"}], "no message"),
        ("an urgency outside 0..1",
         [{"key": "a", "message": "m", "window": "09:00-10:00", "urgency": 4}], "urgency"),
        ("a bad weekday name",
         [{"key": "a", "message": "m", "window": "09:00-10:00", "days": ["Funday"]}], "days"),
        ("a dynamic entry carrying a dead window",
         [{"key": "a", "message": "m", "dynamic": True, "window": "09:00-10:00"}],
         "dynamic AND windowed"),
    ]
    for label, routines, needle in bad:
        problems = rd.validate(routines)
        check(f"names {label}", any(needle in p for p in problems), str(problems))
    check("an empty file is itself a fault", rd.validate([]) != [])
    check("a non-list is a fault", rd.validate({"key": "a"}) != [])
    check("a well-formed dynamic entry is fine", rd.validate([{"key": "a", "message": "m",
                                                               "dynamic": True}]) == [])
    from afon.brain.proactive_signals import _DEFAULT_ROUTINES

    check("the shipped defaults are valid", rd.validate(_DEFAULT_ROUTINES) == [],
          str(rd.validate(_DEFAULT_ROUTINES)))

    print("\n[5] a broken day is detected AND named  [21.F2]")

    def ev(summary, start, end, location=""):
        d = {"summary": summary,
             "start": {"dateTime": f"2026-09-14T{start}:00+02:00"},
             "end": {"dateTime": f"2026-09-14T{end}:00+02:00"}}
        if location:
            d["location"] = location
        return d

    double = sch.clashes([ev("dentist", "10:00", "11:00"), ev("review", "10:30", "11:30")])
    check("a double-booking is detected", [c.kind for c in double] == ["overlap"], str(double))
    check("...and both events are named", double[0].events == ("dentist", "review"))
    check("...and the overlapping window is spoken", "10:30" in double[0].message,
          double[0].message)

    travel = [c for c in sch.clashes([ev("client", "10:00", "11:00", "Cologne"),
                                      ev("dentist", "11:05", "11:30", "Berlin")])
              if c.kind == "travel"]
    check("no travel time between two places is detected", len(travel) == 1, str(travel))
    check("...and names both places", travel and "Cologne" in travel[0].message
          and "Berlin" in travel[0].message, travel[0].message if travel else "")

    runs = [c for c in sch.clashes([ev("a", "09:00", "10:00"), ev("b", "10:05", "11:00"),
                                    ev("c", "11:00", "12:30"), ev("d", "12:35", "13:30")])
            if c.kind == "no-break"]
    check("an unbroken run is detected", len(runs) == 1, str(runs))
    check("...and counts the commitments in it", runs and len(runs[0].events) == 4)

    print("\n[6] and a workable day is left alone — a checker that cries wolf gets switched off")
    check("a day with gaps has no clashes",
          sch.clashes([ev("standup", "09:00", "09:15"), ev("lunch", "12:30", "13:30")]) == [])
    check("touching events are a tight day, not a double-booking",
          not [c for c in sch.clashes([ev("a", "10:00", "11:00"), ev("b", "11:00", "12:00")])
               if c.kind == "overlap"])
    check("the same place back-to-back needs no travel time",
          not [c for c in sch.clashes([ev("a", "10:00", "11:00", "Office"),
                                       ev("b", "11:05", "11:30", "office")]) if c.kind == "travel"])
    check("an unrecorded location invents no journey",
          not [c for c in sch.clashes([ev("a", "10:00", "11:00"), ev("b", "11:05", "11:30")])
               if c.kind == "travel"])
    check("a real gap splits the run, so a day with lunch in it is fine",
          not [c for c in sch.clashes([ev("a", "09:00", "11:00"), ev("b", "12:00", "14:00")])
               if c.kind == "no-break"])
    allday = {"summary": "birthday", "start": {"date": "2026-09-14"},
              "end": {"date": "2026-09-15"}}
    check("an all-day event is not a double-booking",
          sch.clashes([allday, ev("a", "10:00", "11:00")]) == [])
    check("a malformed event is skipped, never guessed at",
          sch.clashes([{"summary": "x", "start": {"dateTime": "nope"}, "end": {}}]) == [])
    check("a clean day says so out loud", "holds together" in sch.spoken([]))

    print("\n[7] the impossible is said before the merely tiring")
    mixed = sch.clashes([ev("a", "09:00", "12:00", "Berlin"), ev("b", "11:00", "13:00", "Cologne"),
                         ev("c", "13:00", "15:00", "Berlin")])
    check("overlap is reported first", mixed and mixed[0].kind == "overlap",
          str([c.kind for c in mixed]))

    print("\n[8] no model is consulted — a hallucinated clash is worse than none")
    src = (Path(__file__).resolve().parents[1] / "src" / "afon" / "brain" / "schedule.py").read_text(
        encoding="utf-8")
    for bad_token in ("llm", "complete(", "await ", "httpx", "requests"):
        check(f"schedule.py contains no {bad_token.strip('( ')}", bad_token not in src.lower())

    print("\n[9] the tools are registered, and the write is confirm-gated")
    from afon.brain.proactive import confirm_required
    from afon.brain.tools import tool_names

    names = set(tool_names())
    for t in ("propose_routines", "adopt_routines", "day_clashes"):
        check(f"{t} is registered", t in names, str(sorted(names))[:200])
    check("adopting routines is confirm-gated", confirm_required("adopt_routines"))
    check("proposing them is not", not confirm_required("propose_routines"))
    check("reading the day is not", not confirm_required("day_clashes"))

    tmpdir.cleanup()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
