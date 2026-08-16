"""Unparking refuses while any Wave-0 floor is unmet — SYSTEMS.md 23.F4.

`~/.afon/MAINTENANCE` is a file. `rm` has no opinion about whether the thing it unblocks is ready,
and that asymmetry is the whole problem: parking was a decision taken with reasons, unparking is a
keystroke that forgets all of them. `scripts/preflight.sh --unpark` is the checklist in between.

What has to be true for it to be worth anything:

  1. it reads the Wave-0 floors from `docs/SYSTEMS.md`, not from a second list kept here — a copy
     of a plan is a copy that goes stale quietly and then approves an unpark it should have blocked;
  2. an unchecked floor refuses;
  3. **a floor that is checked but whose gate fails also refuses.** This is the one that matters.
     The plan's own rule is "no relabeling", and a checklist that trusts the checkbox is a checklist
     that can only catch the honest mistakes;
  4. a floor marked done that names no gate refuses — "a task without a named gate is not a task,
     it is a wish", so it cannot be evidence either;
  5. a gate whose file is missing FAILS rather than skipping. "The test is absent" and "the test
     passed" must never produce the same verdict;
  6. it never removes the lock. Parking stays a decision a person makes.

The refusal logic is driven with an injected runner, so these assert which conditions block rather
than re-running the real suites — which `preflight.sh --unpark` does for real, slowly, on purpose.

Run:
    uv run python bench/test_unpark_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import unpark_check as U  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


PLAN = """
| Wave | Systems | Why first |
|---|---|---|
| **0 — Unpark safely** | S31, S36, S22, S23 | Nothing goes back into production until… |
| **1 — The organs** | S01, S02 | later |

## S31 · Self-Monitoring

**Floor**
- [x] 31.F1 A thing that is done. *gate:* `test_alpha.py`
- [ ] 31.F2 A thing that is not done. *gate:* `test_beta.py`

**Raise**
- [ ] 31.R1 Not a floor, must be ignored. *gate:* `test_never_run.py`

---

## S36 · Security

**Floor**
- [x] 36.F1 Two gates, the second wrapped onto its own line. *gate:* `test_alpha.py`,
      `test_gamma.py`

---

## S01 · Brain

**Floor**
- [ ] 1.F1 Wave 1, must be ignored. *gate:* `test_never_run.py`

---
"""


def _runner(results: dict):
    seen: list[str] = []

    def run(gate: str):
        seen.append(gate)
        return results.get(gate, (True, "ok"))

    run.seen = seen  # type: ignore[attr-defined]
    return run


def main() -> int:
    print("[1] the checklist reads the plan, rather than keeping its own copy of it")
    systems = U.wave_systems(PLAN)
    check(f"Wave 0's systems come from the wave table ({systems})",
          systems == ["S31", "S36", "S22", "S23"])
    tasks = U.floors(PLAN, systems)
    check(f"only Wave-0 FLOOR tasks are collected ({[t['id'] for t in tasks]})",
          [t["id"] for t in tasks] == ["31.F1", "31.F2", "36.F1"],
          "raise tasks and other waves must not gate production")
    check("a gate wrapped onto a continuation line is still collected",
          tasks[2]["gates"] == ["test_alpha.py", "test_gamma.py"],
          "31.F2 in the real plan names two gates this way; a *gate:*-only rule ran half of them")
    check("and the checkbox state is read per task",
          [t["done"] for t in tasks] == [True, False, True])
    # Not the same statement as "raise tasks are not collected". Gates are gathered from every line
    # of a task, so without a tier boundary the **Raise** section's gate is appended to the LAST
    # floor task — 31.F2 quietly acquires test_never_run.py and production is gated on a raise.
    # The id regex alone does not stop this: `31.R1` fails to start a new task, so its lines simply
    # keep flowing into the previous one.
    check("a raise task's gate does not leak into the floor above it",
          tasks[1]["gates"] == ["test_beta.py"], str(tasks[1]["gates"]))

    print("\n[2] an unfinished floor refuses, and says which")
    run = _runner({})
    blockers = U.evaluate(tasks, run=run)
    check("an unchecked floor blocks the unpark",
          any(b.startswith("31.F2") for b in blockers), str(blockers))
    check("and its gate is never run — there is nothing to verify yet",
          "test_beta.py" not in run.seen, str(run.seen))

    print("\n[3] a floor marked done whose gate FAILS also refuses")
    # The check that earns this file. Trusting the checkbox catches only honest mistakes; the plan
    # forbids relabeling precisely because the dishonest one is what puts a broken system live.
    done_only = [t for t in tasks if t["done"]]
    check("all-green floors produce no blockers", U.evaluate(done_only, run=_runner({})) == [])
    blockers = U.evaluate(done_only, run=_runner({"test_alpha.py": (False, "3 checks failed")}))
    check("a passing checkbox over a failing gate is a blocker", bool(blockers), str(blockers))
    check("and the reason names the gate, not just the task",
          any("test_alpha.py" in b for b in blockers), str(blockers))

    print("\n[4] the other two ways a floor is not really evidence")
    no_gate = [{"id": "9.F1", "done": True, "title": "done, but unverifiable", "gates": []}]
    check("a floor marked done that names no gate refuses",
          any("names no gate" in b for b in U.evaluate(no_gate, run=_runner({}))))
    ok, detail = U.run_gate("test_this_does_not_exist_anywhere.py")
    check("a gate whose file is missing FAILS rather than skipping",
          not ok and "no such test" in detail,
          "absent and passing must never reach the same verdict")

    print("\n[5] a shared gate runs once, not once per floor that cites it")
    run = _runner({})
    U.evaluate(tasks, run=run)
    check(f"test_alpha.py is cited by two floors and ran once ({run.seen})",
          run.seen.count("test_alpha.py") == 1)

    print("\n[6] it never unparks anything itself")
    src = (ROOT / "scripts" / "unpark_check.py").read_text(encoding="utf-8")
    check("the checklist contains no unlink/remove of the lock",
          not any(s in src for s in ("unlink(", "os.remove", "shutil.rmtree", "LOCK.unlink")),
          "unparking is the owner's act; this only says whether it would be defensible")
    check("and it reports the lock's own path rather than rebuilding one",
          "maintenance" in src and "LOCK" in src,
          "a second spelling of the lock path is how a park switch stops being honoured (J3.6)")

    print("\n[7] preflight.sh actually exposes it")
    pf = ROOT / "scripts" / "preflight.sh"
    raw = pf.read_bytes()
    text = raw.decode("utf-8")
    check("--unpark is a recognised flag", "--unpark" in text and "UNPARK_CHECKS=1" in text)
    check("it invokes the checklist", "scripts/unpark_check.py" in text)
    check("a refusal is a preflight failure, not a warning",
          "unpark REFUSED" in text and "fail " in text.split("UNPARK_CHECKS")[-1],
          "a warning would let a broken unpark proceed with a note")
    # L3f: this repo has broken shell scripts twice by editing them from Windows. A CRLF preflight
    # dies with `$'\r': command not found` the moment anything but Git Bash invokes it.
    # Counted outside the f-string: a backslash inside an f-string expression is a 3.12+ feature,
    # and this project targets 3.11. It parsed fine on the machine it was written on, which is
    # exactly why the lint gate is the thing that decides rather than "it ran".
    crs = raw.count(b"\r")
    check("preflight.sh is still LF-only", b"\r\n" not in raw,
          f"{crs} CR byte(s) — the script will not run outside Git Bash")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
