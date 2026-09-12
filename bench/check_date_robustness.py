"""Run the date-sensitive tests on other days AND in other timezones (21.F3).

`test_calendar_dates` asserted `"T" not in s` against an all-day string formatted as
"on Tue 11 August" — the T in "Tue". It went red on Tuesdays and Thursdays and green the other
five days, and was caught only because a run happened to straddle midnight into a Tuesday.
Luck is not a test strategy.

This replays the tests that touch dates under a handful of deliberately awkward days: both
weekdays whose abbreviation contains a capital T, a month rollover, a year rollover, a leap day
and a European DST boundary. A test that passes today and fails on one of those is not a product
bug — it is a test that has been lying about which days it covers.

The timezone axis was added for the same reason and found the same shape of bug immediately.
`_fmt_when` resolved "today" from the process clock, so on a brain running UTC on the VPS with an
owner in Europe/Berlin, every event between midnight and 02:00 his time was spoken as "tomorrow".
Two hours a day of confidently wrong answers, every day, and invisible to a suite that only ever
ran in the afternoon from the same machine as the person reading it.

    uv run python bench/check_date_robustness.py            # dates and zones
    uv run python bench/check_date_robustness.py 2026-03-29 # plus a date of your own

Dates are faked by `bench/_faketime/sitecustomize.py`, which CPython imports at startup — before
any test binds `date` — so no test needs to know this exists. Timezones move `AFON_USER_TZ`, which
is the only clock the brain is entitled to read: the process timezone belongs to whichever host it
happens to be running on, and that is exactly the assumption being tested.

ponytail: a thread pool over subprocesses. Two axes at ~6s a run is ten minutes serially, which is
long enough that the gate gets skipped, and a gate nobody runs is not a gate. The work is entirely
process startup, so threads are the right tool and the GIL is irrelevant.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKETIME = ROOT / "bench" / "_faketime"

# Why each date is here. Keep the reason attached: a date set with no rationale gets trimmed by
# the next person who finds it slow.
DATES: list[tuple[str, str]] = [
    ("2026-08-10", "tomorrow is a Tue — 'Tue' contains a capital T"),
    ("2026-08-12", "tomorrow is a Thu — the other T weekday"),
    ("2026-08-31", "month rollover: tomorrow is 1 September"),
    ("2026-12-31", "year rollover: tomorrow is 1 January 2027"),
    ("2028-02-28", "leap year: tomorrow is 29 February"),
    ("2026-10-24", "Europe/Berlin DST ends the next night"),
]

# Timezones, chosen to break different assumptions rather than to cover the map: UTC because the
# VPS runs on it, a large negative offset and a large positive one so the owner's date differs from
# the machine's in both directions, and a half-hour offset because code that assumes whole-hour
# zones passes every other case here. The whole TESTS list is verified green under all four, so
# there is no exempt subset to keep in step with this one.
ZONES: list[tuple[str, str]] = [
    ("UTC", "the brain's own host clock"),
    ("Pacific/Honolulu", "UTC-10: the owner's date is behind the machine's"),
    ("Pacific/Kiritimati", "UTC+14: the owner's date is ahead of the machine's"),
    ("Asia/Kolkata", "UTC+05:30: a half-hour offset"),
]

# The tests that read the clock. From `grep -l "today\|now()" bench/*.py`, minus the ones that
# need the network. Add to this when you add a test that formats or compares a date.
TESTS = [
    "test_calendar_dates.py",
    "test_intent_router.py",
    "test_clause_routing.py",
    "test_if_then_operator.py",
    "test_memory_hygiene.py",
    "test_world_model.py",
    "test_proactive_thresholds.py",
    "test_weather_when.py",
    "test_presence.py",
    "test_relational.py",
    "test_day_shape.py",
]


def run(test: str, when: str = "", zone: str = "") -> tuple[bool, str]:
    env = dict(os.environ)
    if when:
        env["AFON_FAKE_TODAY"] = when
        env["PYTHONPATH"] = str(FAKETIME) + os.pathsep + env.get("PYTHONPATH", "")
    if zone:
        env["AFON_USER_TZ"] = zone
    try:
        p = subprocess.run([sys.executable, str(ROOT / "bench" / test)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if p.returncode == 0:
        return True, ""
    tail = [ln for ln in (p.stdout + p.stderr).splitlines()
            if "FAIL" in ln or "Error" in ln]
    return False, (tail[-1].strip()[:120] if tail else f"exit {p.returncode}")


def _axis(label: str, cases: list[tuple[str, str]], kwarg: str,
          pool: ThreadPoolExecutor) -> list[str]:
    """Run every test under every case of one axis. Returns the failures."""
    failures: list[str] = []
    jobs = {(value, test): pool.submit(run, test, **{kwarg: value})
            for value, _ in cases for test in TESTS}
    for value, why in cases:
        print(f"-- {value}  ({why})")
        bad = 0
        for test in TESTS:
            ok, detail = jobs[(value, test)].result()
            if not ok:
                bad += 1
                failures.append(f"{test} @ {label} {value}: {detail}")
                print(f"   FAIL  {test}  {detail}")
        if not bad:
            print(f"   all {len(TESTS)} pass")
    return failures


def main() -> int:
    dates = list(DATES) + [(d, "supplied on the command line") for d in sys.argv[1:]]
    total = len(TESTS) * (len(dates) + len(ZONES))
    print(f"Replaying {len(TESTS)} clock-sensitive tests across {len(dates)} days "
          f"and {len(ZONES)} timezones ({total} runs).\n")
    # ponytail: four workers, not eight. Eight thrashed — these tests import the whole tool
    # registry, and four concurrent copies of that is already most of the machine's memory. A
    # replay that stalls for ten minutes is a replay that gets deleted.
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = _axis("date", dates, "when", pool)
        print()
        failures += _axis("tz", ZONES, "zone", pool)
    print()
    if failures:
        print(f"=== {len(failures)} clock-dependent failure(s) ===")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"=== no clock-dependent failures across {len(dates)} days "
          f"and {len(ZONES)} timezones ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
