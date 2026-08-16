"""The 50-system master plan has to stay honest, or it becomes the next MASTER-PLAN.md.

`docs/SYSTEMS.md` makes three kinds of claim, and each one rots in a different way:

  * **coverage** — fifty systems, numbered, no gaps. A missing section is a system nobody is
    tracking, and it goes missing silently because nobody counts to fifty by hand.
  * **a gate per task** — the document's own rule is "a task without a named gate is not a task; it
    is a wish". Unenforced, that degrades to prose within a month.
  * **the scoreboard** — a summary table sitting 1,500 lines below the checkboxes it summarises.
    Hand-edited tables and the thing they summarise always drift, and the table is what gets read.

The fourth check is the one J5.1 bought at cost: a plan may cite a test that does not exist yet, but
only if it SAYS so. `docs/MASTER-PLAN.md` asserted a `bench/train_wakeword.py` that had never
existed and the claim propagated to four files. So a cited `test_*.py` must either be in bench/ or
be introduced as `new \\`test_x.py\\`` — planned work, marked planned. (test_doc_paths.py cannot
catch these: the plan deliberately writes gate names bare, without the `bench/` prefix, precisely so
that planned tests are not phantom path claims.)

What this does NOT assert: that the statuses are true. No test can check "built, not well
structured" — that judgement is the owner's and the gates named in each section are what move it.

Hermetic — reads one markdown file and lists a directory.

    uv run python bench/test_systems_plan.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "SYSTEMS.md"

_SECTION = re.compile(r"^## (S(\d\d)) · (.+)$")
_TASK = re.compile(r"^- \[([x ~])\] (\d\d)\.([FRE])(\d+) ")
_ROW = re.compile(r"^\| (S\d\d) \| ([^|]+) \| ([^|]+) \| (\d+)/(\d+) \| (\d+)/(\d+) \| ([^|]+) \|")
_TESTFILE = re.compile(r"`(test_[a-z0-9_]+\.py)`")
_TIER = {"**Floor": "F", "**Raise**": "R", "**Elite": "E"}

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _tier_of(line: str) -> str | None:
    if line.startswith("**Raise / Elite"):
        return None                      # S49 defers both tiers deliberately
    for prefix, tier in _TIER.items():
        if line.startswith(prefix):
            return tier
    return None


def main() -> None:
    if not DOC.exists():
        check(False, "docs/SYSTEMS.md exists")
        sys.exit(1)
    lines = DOC.read_text(encoding="utf-8").splitlines()

    # ---- parse -------------------------------------------------------------------------------
    sections: list[str] = []
    counts: dict[str, dict[str, list[int]]] = {}
    tasks: list[tuple[str, str, str, list[str]]] = []   # (sid, tier, id, block lines)
    sid = tier = None
    block: list[str] | None = None

    def _close_block() -> None:
        if block is not None and tasks:
            tasks[-1] = (*tasks[-1][:3], list(block))

    for line in lines:
        m = _SECTION.match(line)
        if m:
            _close_block()
            block = None
            sid, tier = m.group(1), None
            sections.append(sid)
            counts[sid] = {"F": [0, 0], "R": [0, 0], "E": [0, 0]}
            continue
        if sid is None:
            continue
        t = _tier_of(line)
        if t or line.startswith("**Raise / Elite"):
            _close_block()
            block = None
            tier = t
            continue
        mt = _TASK.match(line)
        if mt and tier:
            _close_block()
            counts[sid][tier][1] += 1
            if mt.group(1) == "x":
                counts[sid][tier][0] += 1
            tasks.append((sid, tier, f"{mt.group(2)}.{mt.group(3)}{mt.group(4)}", []))
            block = [line]
        elif block is not None:
            if line.startswith(("- [", "**", "#", "|")):
                _close_block()
                block = None
            else:
                block.append(line)
    _close_block()

    print("[1] fifty systems, numbered, in order, none missing")
    check(len(sections) == 50, f"exactly 50 system sections (found {len(sections)})")
    expected = [f"S{i:02d}" for i in range(1, 51)]
    check(sections == expected, "S01…S50 present and in order",
          str([s for s, e in zip(sections, expected) if s != e][:6]))

    print("\n[2] every task carries a named gate — the document's own rule")
    ungated = [f"{sid} {tid}" for sid, _t, tid, blk in tasks if "*gate:*" not in " ".join(blk)]
    check(not ungated, f"all {len(tasks)} tasks name a gate", str(ungated[:8]))
    misnumbered = [f"{sid}:{tid}" for sid, tier, tid, _b in tasks
                   if not tid.startswith(sid[1:] + ".") or tid.split(".")[1][0] != tier]
    check(not misnumbered, "task ids match their section and tier", str(misnumbered[:8]))

    print("\n[3] a cited test either exists, or is declared as new")
    have = {p.name for p in (ROOT / "bench").glob("test_*.py")}
    body = "\n".join(lines)
    phantom = []
    for sid, _t, tid, blk in tasks:
        text = " ".join(blk)
        for name in _TESTFILE.findall(text):
            if name in have:
                continue
            if f"new `{name}`" in body:
                continue        # planned, and the plan says so
            phantom.append(f"{sid} {tid} -> {name}")
    check(not phantom, "no gate cites a test that neither exists nor is marked new",
          str(phantom[:8]))

    print("\n[4] the scoreboard agrees with the checkboxes 1,500 lines above it")
    rows = {}
    for line in lines:
        m = _ROW.match(line)
        if m:
            rows[m.group(1)] = (int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)))
    check(len(rows) == 50, f"scoreboard has 50 rows (found {len(rows)})")
    drift = []
    for s in expected:
        if s not in rows:
            drift.append(f"{s} missing from scoreboard")
            continue
        fn, fd, rn, rd = rows[s]
        c = counts[s]
        if [fn, fd] != c["F"] or [rn, rd] != c["R"]:
            drift.append(f"{s}: table F{fn}/{fd} R{rn}/{rd} vs real F{c['F']} R{c['R']}")
    check(not drift, "every row matches its section's real counts", "\n        ".join(drift[:8]))

    tot = [sum(counts[s][t][i] for s in counts) for t in ("F", "R", "E") for i in (0, 1)]
    # "at the moment of writing" was in this pattern until the totals first MOVED, which is the one
    # event it was guaranteed to see. The counts are meant to be re-derived, so the phrase anchoring
    # them cannot be one that only makes sense before anything is finished.
    stated = re.search(r"\*\*Totals(?:[^:]*)?: (\d+) of (\d+) floor tasks green, "
                       r"(\d+) of (\d+) raise tasks, (\d+) of (\d+) elite", body)
    check(stated is not None, "the totals line is present and parseable")
    if stated:
        check([int(g) for g in stated.groups()] == tot,
              "stated totals match the counted totals",
              f"stated {stated.groups()} vs counted {tot}")

    print("\n[5] the two roadmaps point at each other")
    todo = (ROOT / "TODO.md").read_text(encoding="utf-8")
    check("docs/SYSTEMS.md" in todo[:4000], "TODO.md names the systems plan up front")
    check("TODO.md" in body[:2000], "SYSTEMS.md names TODO.md up front")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
