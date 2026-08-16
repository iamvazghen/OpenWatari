"""Unpark checklist — parking is one command, so leaving the park must not be (SYSTEMS.md 23.F4).

`~/.afon/MAINTENANCE` is a file, and `rm` takes no view on whether the thing it unblocks is ready.
That asymmetry is the risk: parking was a deliberate decision made with reasons, and unparking is
currently a keystroke that forgets all of them. This is the checklist that stands in between.

It refuses on any unmet Wave-0 floor, and it takes "unmet" from `docs/SYSTEMS.md` itself — the wave
table for which systems are in Wave 0, the checkboxes for which floors are done, and the *gate:*
line for which test decides. Nothing is duplicated here, because a hardcoded copy of the plan is a
copy that goes stale silently and then approves an unpark it should have blocked.

Two ways a floor fails, and the second is the one worth having:

  * the box is unchecked — the work is not done;
  * the box is checked but its gate does not pass — the plan says done and the code disagrees. That
    is the relabeling this document's own rules forbid, and only running the gate can catch it.

It never removes the lock. On a full pass it prints the command; a human still has to mean it.

    python scripts/unpark_check.py          # verify, report, refuse or print the unpark command
    python scripts/unpark_check.py --quiet  # exit code only
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "SYSTEMS.md"
BENCH = ROOT / "bench"

#: The wave whose floors gate production. Named here only to find its row in the plan's wave table.
WAVE = "0"


def wave_systems(text: str) -> list[str]:
    """The systems in Wave 0, read from the plan's own wave table."""
    for line in text.splitlines():
        if line.startswith("|") and f"**{WAVE} —" in line:
            return re.findall(r"\bS\d\d\b", line)
    return []


def floors(text: str, systems: list[str]) -> list[dict]:
    """Every floor task of the given systems, with its checkbox state and the gates it names."""
    out: list[dict] = []
    current: str | None = None
    tier: str | None = None
    for raw in text.splitlines():
        m = re.match(r"^## (S\d\d)\b", raw)
        if m:
            current = m.group(1)
            tier = None
            continue
        if raw.startswith("**Floor**"):
            tier = "F"
            continue
        if raw.startswith(("**Raise**", "**Elite**", "**Blocked**", "---")):
            tier = None
            continue
        if tier != "F" or current not in systems:
            continue
        m = re.match(r"^\s*-\s*\[([ x~])\]\s*(\d+\.F\d+)\s+(.*)$", raw)
        if m:
            out.append({"system": current, "done": m.group(1) == "x", "id": m.group(2),
                        "title": m.group(3).strip(), "gates": []})
            raw = m.group(3)
        elif not out:
            continue
        # Collect from EVERY line of the task, not just the one carrying "*gate:*". A task naming
        # two gates wraps, and the second sits alone on the next line — so a `*gate:*`-only rule
        # silently ran half of 31.F2's gates while reporting the floor verified.
        for g in re.findall(r"`([A-Za-z0-9_./]+\.py)`", raw):
            if g not in out[-1]["gates"]:
                out[-1]["gates"].append(g)
    return out


def run_gate(name: str) -> tuple[bool, str]:
    """Run one named gate. A gate that does not exist is a failure, not a skip — 'the test is
    missing' and 'the test passes' must never produce the same verdict."""
    path = BENCH / Path(name).name
    if not path.is_file():
        return False, "no such test file"
    r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=420)
    if r.returncode == 0:
        tail = [ln for ln in r.stdout.splitlines() if "checks passed" in ln]
        return True, (tail[-1].strip() if tail else "exit 0")
    bad = [ln.strip() for ln in r.stdout.splitlines() if "[FAIL]" in ln or "FAIL:" in ln]
    return False, (bad[0][:120] if bad else f"exit {r.returncode}")


def evaluate(tasks: list[dict], run=run_gate, report=None) -> list[str]:
    """Why unpark must be refused, one line per reason. Empty means it is defensible.

    `run` is injected so the decision can be tested without executing the real suites — the logic
    worth testing is which conditions block, not whether the gates themselves pass today.
    """
    blockers: list[str] = []
    ran: set[str] = set()
    for t in tasks:
        if not t["done"]:
            blockers.append(f"{t['id']} is not done — {t['title'][:70]}")
            if report:
                report(f"  TODO  {t['id']}  {t['title'][:66]}")
            continue
        if not t["gates"]:
            # The plan's own rule: "A task without a named gate is not a task; it is a wish."
            blockers.append(f"{t['id']} is marked done but names no gate")
            if report:
                report(f"  FAIL  {t['id']}  marked done, names no gate")
            continue
        for g in t["gates"]:
            if g in ran:
                continue           # several floors legitimately share one gate; run it once
            ran.add(g)
            ok, detail = run(g)
            if report:
                report(f"  {'ok  ' if ok else 'FAIL'}  {t['id']}  {g}  {detail[:80]}")
            if not ok:
                blockers.append(f"{t['id']}: gate {g} does not pass — {detail[:80]}")
    return blockers


def main() -> int:
    quiet = "--quiet" in sys.argv
    text = PLAN.read_text(encoding="utf-8")
    systems = wave_systems(text)
    if not systems:
        print("unpark: could not read the Wave-0 systems from docs/SYSTEMS.md — refusing")
        return 1
    tasks = floors(text, systems)
    if not tasks:
        print(f"unpark: found no floor tasks for {systems} — refusing")
        return 1

    if not quiet:
        print(f"== unpark checklist — Wave 0 ({', '.join(systems)}), "
              f"{len(tasks)} floor task(s) ==")

    blockers = evaluate(tasks, run=run_gate, report=(None if quiet else print))

    # Report the lock, never touch it. Unparking is the owner's act; this only says whether it
    # would be defensible.
    from importlib import import_module

    sys.path.insert(0, str(ROOT / "src"))
    try:
        lock = import_module("afon.shared.maintenance").LOCK
        parked = lock.exists()
    except Exception:  # noqa: BLE001
        lock, parked = Path.home() / ".afon" / "MAINTENANCE", None

    print()
    if blockers:
        print(f"=== UNPARK REFUSED — {len(blockers)} unmet Wave-0 floor(s) ===")
        for b in blockers:
            print(f"  - {b}")
        print("\nThe park switch stays on. Close these, then run this again.")
        return 1

    print("=== every Wave-0 floor is done and its gate passes ===")
    if parked is False:
        print(f"  note: {lock} is already absent — this deployment is not parked.")
    else:
        print("  Unpark is now defensible. It is still a deliberate act, so do it by hand:")
        print(f"      rm {lock}")
        print("  ...and on the brain host too. Re-run with --remote in preflight to confirm both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
