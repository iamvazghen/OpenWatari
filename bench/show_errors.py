"""Read the structured error journal from a terminal — the CLI behind ``afon-errors``.

Runs identically on the laptop and on the VPS, so the same command answers "what broke?" on either
side of the system. Prints nothing dramatic when all is well, because that is the common case.

    python bench/show_errors.py                     # last hour
    python bench/show_errors.py --minutes 1440      # last day
    python bench/show_errors.py --subsystem edge    # one subsystem
    python bench/show_errors.py --turn a1b2c3d4     # one turn, end to end
    python bench/show_errors.py --summary           # counts, worst first
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.shared import errors as err  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Afon error journal")
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--subsystem", default="")
    p.add_argument("--turn", default="")
    p.add_argument("--level", default="")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="include successful operations (the full activity trail)")
    a = p.parse_args()

    if not err.JOURNAL.exists():
        print(f"no journal yet at {err.JOURNAL} (nothing has failed, or the process hasn't started)")
        return 0

    if a.summary:
        s = err.summary(since_minutes=a.minutes)
        if not s["total"]:
            print(f"clean — nothing recorded in the last {a.minutes} min")
            return 0
        rate = (100.0 * s["total"] / s["operations"]) if s["operations"] else 0.0
        print(f"{s['total']} failure(s) in the last {s['window_minutes']} min "
              f"({s['ok']} successful op(s), {rate:.1f}% failure rate)")
        print("  by level:     " + ", ".join(f"{k}={v}" for k, v in s["by_level"].items()))
        print("  by subsystem:")
        for k, v in s["by_subsystem"].items():
            print(f"    {v:5d}  {k}")
        # Operations carry a duration, so the slowest failures are worth surfacing here: "Afon is
        # slow" is a complaint that otherwise has nowhere to land.
        timed = [(e["context"]["duration_ms"], e["subsystem"], e.get("message", ""))
                 for e in err.read(limit=2000, since_minutes=a.minutes)
                 if isinstance(e.get("context"), dict)
                 and isinstance(e["context"].get("duration_ms"), (int, float))]
        if timed:
            timed.sort(reverse=True)
            print("  slowest tracked operations:")
            for ms, sub, msg in timed[:5]:
                print(f"    {int(ms):6d}ms  {sub}  {msg[:60]}")
        return 0

    # Tracing ONE turn always includes successes: there the successful steps are the story — they
    # show how far the turn got before it went wrong.
    entries = err.read(limit=a.limit, since_minutes=None if a.turn else a.minutes,
                       subsystem=a.subsystem, turn=a.turn, level=a.level,
                       include_ok=a.all or bool(a.turn))
    if not entries:
        scope = f"turn {a.turn}" if a.turn else f"the last {a.minutes} min"
        print(f"clean — nothing recorded for {scope}")
        return 0

    # Oldest-first reads like a story when tracing a single turn.
    for e in reversed(entries):
        turn = e.get("turn") or "--------"
        print(f"{e.get('ts','')}  {e.get('level',''):<8} {turn:<8} "
              f"{e.get('host','')}/{e.get('process','')} {e.get('subsystem','')}")
        typ = e.get("type")
        print(f"    {(typ + ': ') if typ else ''}{e.get('message','')}")
        if e.get("where"):
            print(f"    at {e['where']}")
        if e.get("context"):
            print(f"    ctx {e['context']}")
    print(f"\n{len(entries)} entr(ies).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
