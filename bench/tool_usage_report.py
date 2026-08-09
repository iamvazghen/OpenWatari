"""What the durable tool-usage store knows so far — the input to catalogue tiering.

Run this after the brain has been up for a while. Until `total()` is a meaningful number, the
"never called" list is NOT evidence that a tool is unused; it is evidence that nothing has been
called yet, which is the failure mode this repo has already hit twice (an empty table read as a
broken pipeline). The script says so rather than letting the reader infer it.

    uv run python bench/tool_usage_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tool_usage import USAGE  # noqa: E402
from afon.brain.tools import core_tool_schemas, tool_names  # noqa: E402

MIN_CALLS_FOR_EVIDENCE = 200


def toks(o) -> int:
    return len(json.dumps(o, ensure_ascii=False)) // 4


def main() -> int:
    total = USAGE.total()
    core = {s["function"]["name"]: toks(s) for s in core_tool_schemas()}
    print(f"tool usage store: {USAGE._path}")
    print(f"total recorded calls: {total}")

    if total == 0:
        print("\nNo calls recorded yet. This is a fresh store, NOT a signal that tools are unused.")
        return 0

    print(f"\ntop tools:\n  {'calls':>6}  {'tok':>4}  name")
    for name, n in USAGE.top(20):
        print(f"  {n:6}  {core.get(name, 0):4}  {name}")

    never = USAGE.unused(list(core))
    never_tok = sum(core[n] for n in never)
    print(f"\ncore tools never called: {len(never)} of {len(core)} (~{never_tok} tok of the surface)")
    for n in never:
        print(f"  {core[n]:4} tok  {n}")

    if total < MIN_CALLS_FOR_EVIDENCE:
        print(f"\n[!] {total} calls is too little to act on (want >= {MIN_CALLS_FOR_EVIDENCE}).")
        print("    A tool absent from a short sample is untested, not unused — do NOT tier on this.")
    else:
        print(f"\nEnough sample to tier on: moving the never-called set to a lazy group would cut "
              f"~{never_tok} tok from every un-narrowed turn.")

    unknown = sorted(set(USAGE.counts()) - set(tool_names()))
    if unknown:
        print(f"\nrecorded names not in the registry (renamed/removed tools): {', '.join(unknown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
