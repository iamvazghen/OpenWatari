"""37.F1 — "what do you know about me" has an answer, and it cannot go stale.

There are forty-odd stores under the state root, each created by whichever module needed one, and
the only place their existence was written down was the code that opened them. A hand-written
privacy document would have been wrong the week after it was written — the plan declined exactly
that — so the inventory is generated and this gate walks the disk.

The load-bearing check is [2]: a store present on disk that nothing declares FAILS the build. That
is what stops the inventory going stale, and it earned its place on the first run by finding three
undeclared things, one of which (the error journal) holds the tail of whatever a failing tool was
handed and is therefore personal data whether or not anyone meant it to be.

What this asserts:

  * every declared store says what it holds, how long, and who can read it;
  * anything on disk that is not declared fails — that is the anti-staleness mechanism;
  * retention has three honest values and "we'll decide later" is not one of them;
  * a document replaced in place is NOT given a day count it would be wrong to enforce;
  * the spoken answer never implies it is complete when something is unaccounted for;
  * asking it costs nothing on a turn that is not about privacy.

Hermetic: reads the real declarations, and a temporary state root for the disk walk.

    uv run python bench/test_data_inventory.py
"""

from __future__ import annotations

import sys
import tempfile
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


def main() -> None:
    from afon.brain import inventory as I
    from afon.config import settings

    print("[1] every store answers all three questions")
    check("something is declared", len(I.STORES) > 20, len(I.STORES))
    check("no store is declared twice",
          len({s.name for s in I.STORES}) == len(I.STORES))
    for s in I.STORES:
        if not s.holds:
            check(f"{s.name} says what it holds", False)
    check("every store says what it holds in the owner's terms",
          all(s.holds and len(s.holds.split()) >= 3 for s in I.STORES),
          [s.name for s in I.STORES if len(s.holds.split()) < 3])
    check("every store says who can read it",
          all(s.readable_by in (I.THIS_HOST, I.OWNER_VAULT, I.BIOMETRIC) for s in I.STORES))
    check("the biometric stores are marked as never leaving the laptop",
          {s.name for s in I.STORES if s.readable_by == I.BIOMETRIC} >= {"voiceprint.json", "faces"},
          [s.name for s in I.STORES if s.readable_by == I.BIOMETRIC])

    print("\n[2] a store on disk that nothing declares FAILS — the anti-staleness rule")
    real = settings.state_dir
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        try:
            settings.state_dir = d
            check("a clean root has nothing undeclared", I.undeclared() == [], I.undeclared())
            (Path(d) / "afon_secrets.sqlite").write_text("x", encoding="utf-8")
            check("a new store is caught immediately",
                  I.undeclared() == ["afon_secrets.sqlite"], I.undeclared())
            (Path(d) / "objectives.json").write_text("{}", encoding="utf-8")
            check("...while a DECLARED store is not flagged",
                  I.undeclared() == ["afon_secrets.sqlite"], I.undeclared())
            (Path(d) / ".lock").write_text("", encoding="utf-8")
            check("a dotfile is not treated as a store",
                  ".lock" not in I.undeclared(), I.undeclared())

            # The report must not read as complete while something is unaccounted for.
            (Path(d) / "objectives.json").write_text('{"a": 1}', encoding="utf-8")
            said = I.spoken()
            check("the spoken answer admits what it cannot account for",
                  "can't account for" in said, said)
            check("...and names it", "afon_secrets.sqlite" in said, said)
            check("the long report says where to declare it",
                  "NOT DECLARED" in I.report() and "inventory.STORES" in I.report())
        finally:
            settings.state_dir = real

    print("\n[3] retention has three honest values, and 'decide later' is not one")
    for s in I.STORES:
        ok = s.retention_days in (I.KEEP_FOREVER, I.WHILE_CURRENT) or s.retention_days > 0
        if not ok:
            check(f"{s.name}: retention is a real value", False, s.retention_days)
    check("every retention is forever, while-current, or a positive number of days", True)
    check("'forever' is spelled out, not left as a missing value", I.KEEP_FOREVER == -1)
    check("'while current' is a distinct third answer",
          I.WHILE_CURRENT != I.KEEP_FOREVER and I.WHILE_CURRENT == 0)
    check("a store that accumulates is marked rolling",
          all(s.rolling == (s.retention_days > 0) for s in I.STORES))

    print("\n[4] a document replaced in place gets no day count it would be wrong to enforce")
    # The first version of the sweep proposed deleting the pending-approvals file and two live pid
    # files because nothing had written to them lately. File mtime is not age for current state.
    for name in ("approvals.json", "run", "day_plan.json", "afon_session.json", "health_probe.json"):
        st = I.by_name(name)
        check(f"{name} is declared while-current, not on a timer",
              st is not None and st.retention_days == I.WHILE_CURRENT,
              st.retention_days if st else "not declared")
    for name in ("audit", "traces", "patterns.jsonl", "errors.jsonl"):
        st = I.by_name(name)
        check(f"{name} accumulates, so it DOES expire",
              st is not None and st.rolling, st.retention_days if st else "not declared")
    check("the sweep is offered only the rolling stores",
          all(s.rolling for s in I.rolling_stores()))
    check("...and every rolling store is offered",
          {s.name for s in I.rolling_stores()} == {s.name for s in I.STORES if s.rolling})

    print("\n[5] the rows are data a caller can act on")
    rows = I.rows()
    check("one row per store", len(rows) == len(I.STORES))
    for key in ("name", "holds", "retention_days", "readable_by", "path", "present", "size_bytes"):
        check(f"each row carries `{key}`", all(key in r for r in rows))
    check("paths are absolute", all(Path(r["path"]).is_absolute() for r in rows))
    check("a store that isn't there is marked absent, not sized",
          all(r["size_bytes"] == 0 for r in rows if not r["present"]))

    print("\n[6] asking costs nothing on a turn that is not about privacy")
    from afon.brain.tools import core_tool_schemas, groups_for_text, tool_handlers

    core = {s["function"]["name"] for s in core_tool_schemas()}
    check("it is not advertised on every turn", "what_you_know" not in core)
    check("...but it is registered", "what_you_know" in tool_handlers())
    check("...and reachable", "data_retention" in tool_handlers())
    for phrase in ("what do you know about me", "what are you storing",
                   "how long do you keep my audit log", "where does my data live"):
        check(f"'{phrase}' reaches it", "privacy" in groups_for_text(phrase))
    check("an ordinary turn does not load it", "privacy" not in groups_for_text("what's the time"))

    print("\n[7] the presence store still honours the setting it already had")
    pres = I.by_name("afon_presence.sqlite")
    old = settings.presence_retention_days
    try:
        settings.presence_retention_days = 7
        check("a configured retention wins over the declared default",
              I.retention_days(pres) == 7, I.retention_days(pres))
    finally:
        settings.presence_retention_days = old

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
