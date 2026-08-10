"""Run the date-sensitive tests on other days, so calendar bugs are found on purpose.

`test_calendar_dates` asserted `"T" not in s` against an all-day string formatted as
"on Tue 11 August" — the T in "Tue". It went red on Tuesdays and Thursdays and green the other
five days, and was caught only because a run happened to straddle midnight into a Tuesday.
Luck is not a test strategy.

This replays the tests that touch dates under a handful of deliberately awkward days: both
weekdays whose abbreviation contains a capital T, a month rollover, a year rollover, a leap day
and a European DST boundary. A test that passes today and fails on one of those is not a product
bug — it is a test that has been lying about which days it covers.

    uv run python bench/check_date_robustness.py            # the default date set
    uv run python bench/check_date_robustness.py 2026-03-29 # plus one of your own

Dates are faked by `bench/_faketime/sitecustomize.py`, which CPython imports at startup — before
any test binds `date` — so no test needs to know this exists.
"""
from __future__ import annotations

import os
import subprocess
import sys
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
]


def run(test: str, when: str) -> tuple[bool, str]:
    env = dict(os.environ)
    env["AFON_FAKE_TODAY"] = when
    env["PYTHONPATH"] = str(FAKETIME) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run([sys.executable, str(ROOT / "bench" / test)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if p.returncode == 0:
        return True, ""
    tail = [ln for ln in (p.stdout + p.stderr).splitlines()
            if "FAIL" in ln or "Error" in ln]
    return False, (tail[-1].strip()[:120] if tail else f"exit {p.returncode}")


def main() -> int:
    dates = list(DATES) + [(d, "supplied on the command line") for d in sys.argv[1:]]
    print(f"Replaying {len(TESTS)} date-sensitive tests across {len(dates)} days.\n")
    failures: list[str] = []
    for when, why in dates:
        print(f"-- {when}  ({why})")
        bad = 0
        for test in TESTS:
            ok, detail = run(test, when)
            if not ok:
                bad += 1
                failures.append(f"{test} @ {when}: {detail}")
                print(f"   FAIL  {test}  {detail}")
        if not bad:
            print(f"   all {len(TESTS)} pass")
    print()
    if failures:
        print(f"=== {len(failures)} date-dependent failure(s) ===")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"=== no date-dependent failures across {len(dates)} days ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
