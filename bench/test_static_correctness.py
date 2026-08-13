"""A name that does not exist must fail at LINT time, not silently at 03:00 on a Sunday.

`brain/scheduler.py` called `get_scheduler()`. No such function exists in that module, or anywhere
in the repo — the call was the only occurrence of the name. It sat inside `try: ... except
Exception: pass`, so the `NameError` was swallowed every single week and the weekly memory review
fell through to a fallback that did nothing. Nothing failed. Nothing was logged as wrong. 140
passing tests did not care, because no test called that job.

ruff was already a declared dev dependency and its `F821` rule catches exactly this in
milliseconds — it was simply never run by anything. That is the actual defect: not the typo, but
that a whole class of error had no gate.

This gates the CORRECTNESS subset only, and deliberately not the style rules (`F401` unused-import,
`F841` unused-variable, `F541` — 29 findings across the tree today). A gate that starts red is a
gate someone switches off in a week. These are green as of 2026-08-12, so red means new.

    uv run python bench/test_static_correctness.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

#: rule -> what a violation would actually cost, in this codebase's own terms
GATED = {
    "F821": "an undefined name — the get_scheduler() bug, swallowed by a bare except for weeks",
    "F811": "a redefinition — the second def silently wins, so a whole function stops existing",
    "F822": "a name in __all__ that isn't defined — an import that fails only for the caller",
    "F823": "a local used before assignment",
    "F402": "an import shadowed by a loop variable",
    "F632": "`is` against a literal — true today, false after a refactor, never an error",
    "F631": "assert on a tuple — always true, so the assertion never fires",
    "B018": "a useless expression — code that looks like it acts and does not",
}

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def ruff(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "ruff", *args], cwd=str(cwd),
                          capture_output=True, text=True)


def main() -> None:
    probe = ruff(["--version"])
    check(probe.returncode == 0, f"ruff is available ({(probe.stdout or '').strip()})",
          (probe.stderr or "")[:120] + " — the gate cannot run, which is worse than a red gate")
    if probe.returncode != 0:
        print(f"\n=== {passed}/{passed + failed} checks passed ===")
        sys.exit(1)

    select = ",".join(sorted(GATED))

    print(f"\n[1] the correctness rules are clean across src/ and bench/")
    r = ruff(["check", "--select", select, "src", "bench"])
    check(r.returncode == 0, f"ruff --select {select}: clean",
          "\n" + (r.stdout or r.stderr or "").strip()[:1500])

    print("\n[2] each gated rule is actually a rule ruff still has")
    # Rules get removed between ruff versions (E999 was, in the version bump before this file was
    # written). A silently-dropped rule is a gate that reports green because it checks nothing.
    for rule, why in sorted(GATED.items()):
        r = ruff(["check", "--select", rule, "--isolated", "--no-cache", "-"])
        check("cannot be selected" not in (r.stderr or ""),
              f"{rule} exists — {why}", (r.stderr or "").strip()[:120])

    print("\n[3] the gate really catches the bug it was built for")
    # Prove it end to end rather than trusting the rule name: this is the exact shape that survived
    # in scheduler.py — an undefined call, inside a try, under a bare except.
    # Unique per process: a fixed name in the repo root means two concurrent runs delete each
    # other's probe, and the failure looks like a flaky lint gate rather than a test collision.
    tmp = ROOT / f".tmp-f821-probe-{os.getpid()}.py"
    tmp.write_text(
        "def f():\n"
        "    try:\n"
        "        return get_scheduler()\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8")
    try:
        r = ruff(["check", "--select", "F821", "--no-cache", str(tmp.name)])
        check(r.returncode != 0 and "F821" in (r.stdout or ""),
              "the real bug's shape is rejected", (r.stdout or "")[:200])
    finally:
        tmp.unlink(missing_ok=True)

    print("\n[4] the style rules are NOT gated (on purpose)")
    # If these ever come clean, gate them too — but say so deliberately rather than discovering it.
    r = ruff(["check", "--select", "F401,F841,F541", "--statistics", "src", "bench"])
    n = (r.stdout or "").strip().splitlines()
    check(True, f"style findings today: {n[-1] if n else 'none'} (not gated — a gate that starts "
                "red gets switched off)")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
