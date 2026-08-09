"""Proactive signals must never read a tool's ERROR STRING aloud as if it were data.

Every tool handler returns speakable prose and never raises (tools/base.py contract), so a caller
that consumes a result as DATA cannot tell "here are your events" from "I couldn't do that". On
2026-07-28 production proved the gap: with Google auth broken, `list_events` returned its failure
note, `anticipatory_prep` accepted it as an event and Afon announced

    Heads up: 'I couldn't complete the calendar read just now (RuntimeError...' is starting soon.

`tool_failed()` closes it. This pins both the predicate and the two signal call sites.

    uv run python bench/test_tool_failure_guard.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
    from afon.brain.tools.base import not_configured, tool_error, tool_failed

    print("tool_failed() predicate")
    # The two real failure shapes, taken from the helpers themselves (not hand-copied prose, so a
    # reworded helper fails this test instead of silently escaping the guard).
    check("tool_error(...) is a failure", tool_failed(tool_error("calendar read", RuntimeError("google not authorized"))))
    check("not_configured(...) is a failure", tool_failed(not_configured("Google Calendar", "a client id")))
    check("empty is a failure", tool_failed(""))
    check("None is a failure", tool_failed(None))
    check("whitespace is a failure", tool_failed("   \n "))
    # Real calendar output must pass through untouched.
    check("real event line is DATA", not tool_failed("09:00 Standup with the team (30 min)"))
    check("'nothing scheduled' is DATA", not tool_failed("Nothing scheduled, sir."))

    print("\nanticipatory_prep() with a failing calendar")
    import afon.brain.tools.calendar as cal
    from afon.brain import proactive_signals as ps

    async def _boom(_args):
        return tool_error("calendar read", RuntimeError("google not authorized"))

    async def _real(_args):
        return "10:30 Investor call — Zoom\n14:00 Dentist"

    orig = cal.list_events
    try:
        cal.list_events = _boom
        sigs = await ps.anticipatory_prep()
        check("failure -> NO signal emitted", sigs == [], f"got {[s.message for s in sigs]}")

        cal.list_events = _real
        sigs = await ps.anticipatory_prep()
        check("real events -> signal emitted", len(sigs) == 1, f"got {sigs}")
        if sigs:
            check("signal quotes the real event",
                  "Investor call" in sigs[0].message,
                  sigs[0].message)
            check("signal never contains error prose",
                  "couldn't complete" not in sigs[0].message.lower(),
                  sigs[0].message)
    finally:
        cal.list_events = orig

    print("\nweekly_digest() must not append an error as 'Upcoming'")
    from datetime import datetime, timezone

    sunday_2000 = datetime(2026, 7, 26, 20, 0, tzinfo=timezone.utc)  # Sun 20:00 UTC = the fire window
    try:
        cal.list_events = _boom
        sigs = await ps.weekly_digest(now=sunday_2000)
        check("digest still fires", len(sigs) == 1, f"got {sigs}")
        if sigs:
            check("digest has no 'Upcoming:' from a failed read",
                  "Upcoming" not in sigs[0].message, sigs[0].message)
            check("digest has no error prose",
                  "couldn't complete" not in sigs[0].message.lower(), sigs[0].message)
    finally:
        cal.list_events = orig

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
