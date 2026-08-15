"""The cohesion gate measures what the report measures, and bites only on real regressions (J8.1).

`scripts/cohesion_baseline.py` is only worth having if three things hold, and each fails silently:

  1. its cohesion is graphify's cohesion — not a lookalike that has drifted from the number
     GRAPH_REPORT.md prints and Part J's ten findings quote;
  2. a community that genuinely gets mushier is caught;
  3. a community that merely re-clustered is NOT called a regression, because a gate that cries
     wolf on Louvain reshuffling is one nobody leaves switched on.

(3) is the one that decides whether this survives. Louvain communities are not stable objects:
`Presence` went from 17 nodes at 0.104 to 53 nodes at 0.07 between two graphs with no code
getting worse.

Run:
    uv run python bench/test_cohesion_baseline.py
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cohesion_baseline as CB  # noqa: E402

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


def main() -> int:
    if not CB.GRAPH.exists():
        check("graph.json present", False, "run `graphify update .`")
        print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
        return 1
    graph = json.loads(CB.GRAPH.read_text(encoding="utf-8"))
    cur = CB.measure(graph)

    print("[1] the metric is graphify's own, not a lookalike")
    # The whole point of the gate is to defend the numbers Part J quotes. If this file's formula
    # and graphify's ever diverge, every threshold in J1/J2 silently stops meaning what it says.
    report = (ROOT / "graphify-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    printed = re.findall(r'### Community (\d+) - ".*?"\nCohesion: ([0-9.]+)', report)
    by_index = {n.get("community"): None for n in graph["nodes"]}
    comm_of = {}
    for entry in cur["communities"]:
        comm_of[frozenset(entry["members"])] = entry
    index_members = {}
    for n in graph["nodes"]:
        index_members.setdefault(n.get("community"), set()).add(n["id"])
    mismatch = []
    for idx, printed_value in printed:
        entry = comm_of.get(frozenset(index_members.get(int(idx), set())))
        if entry is None or abs(round(entry["cohesion"], 2) - float(printed_value)) > 0.0051:
            mismatch.append((idx, printed_value, entry["cohesion"] if entry else None))
    check(f"every cohesion GRAPH_REPORT.md prints is reproduced ({len(printed)} communities)",
          printed and not mismatch, f"{len(mismatch)} differ: {mismatch[:3]}")
    check("the report does not cover every community, which is why the baseline exists (J0.5)",
          len(printed) < len(cur["communities"]),
          f"{len(printed)} printed of {len(cur['communities'])}")
    check("cohesion of a lone node is 0, not a division by zero",
          all(c["cohesion"] == 0.0 for c in cur["communities"] if c["size"] < 2))

    print("\n[2] the committed baseline describes the graph on disk")
    check("baseline lives under bench/, which .graphifyignore excludes",
          CB.BASELINE.parent.name == "bench",
          "a baseline stored inside the graph's own scan would perturb what it measures")
    check("baseline exists and is tracked, unlike graphify-out/", CB.BASELINE.exists(),
          "run `python scripts/cohesion_baseline.py --record`")
    if not CB.BASELINE.exists():
        print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
        return 1
    base = json.loads(CB.BASELINE.read_text(encoding="utf-8"))
    regressions, _, orphans = CB.compare(base, cur)
    # Stale is not failure here: the graph rebuilds on commit and this repo commits rarely, so the
    # baseline can legitimately trail. Regressions are the signal.
    check("no community has lost cohesion beyond tolerance", not regressions,
          str([(b["name"], b["cohesion"], c["cohesion"]) for b, c, _, _ in regressions][:3]))
    check("measuring twice gives the same numbers",
          CB.measure(json.loads(CB.GRAPH.read_text(encoding="utf-8"))) == cur)

    print("\n[3] a real regression is caught")
    worst = max(cur["communities"], key=lambda c: c["size"] * c["cohesion"])
    members = set(worst["members"])
    thinned = copy.deepcopy(graph)
    # Delete half the community's internal edges: exactly the shape of "this module got mushier".
    inside = [lk for lk in thinned["links"]
              if lk["source"] in members and lk["target"] in members]
    drop = {id(lk) for lk in inside[: len(inside) // 2]}
    thinned["links"] = [lk for lk in thinned["links"] if id(lk) not in drop]
    hurt = CB.measure(thinned)
    regs, _, _ = CB.compare(cur, hurt)
    check(f"halving {worst['name']!r}'s internal edges is reported as a regression",
          any(b["name"] == worst["name"] for b, _, _, _ in regs),
          f"regressions: {[b['name'] for b, _, _, _ in regs][:3]}")
    check("the aggregate notices too, so a wholesale reshuffle cannot hide it",
          hurt["internal_edge_share"] < cur["internal_edge_share"])

    print("\n[4] a re-cluster is not a regression")
    # Merge the two largest communities — the classic Louvain move between graph versions, and the
    # one that scores exactly 0.5 Jaccard when the sizes match, which is why the floor is 0.6.
    big = sorted(cur["communities"], key=lambda c: -c["size"])[:2]
    merged = copy.deepcopy(cur)
    merged["communities"] = [c for c in merged["communities"] if c not in big] + [{
        "name": big[0]["name"], "size": big[0]["size"] + big[1]["size"],
        "cohesion": min(c["cohesion"] for c in big) / 2,
        "members": sorted(set(big[0]["members"]) | set(big[1]["members"])),
    }]
    regs, _, orph = CB.compare(cur, merged)
    merged_names = {b["name"] for b, *_ in orph}
    check("merging two communities yields NOT COMPARABLE, not a regression",
          not any(b["name"] in {c["name"] for c in big} for b, _, _, _ in regs)
          and {c["name"] for c in big} <= merged_names,
          f"regressions {[b['name'] for b, _, _, _ in regs][:3]}, orphans {sorted(merged_names)[:3]}")

    # The third reshuffle shape, and the only one the SIZE rule cannot see: a community's members
    # disperse into several others, each of which stays a normal size. Planting `MIN_OVERLAP = 0.0`
    # is what showed the merge case above was being caught by size alone, leaving the overlap floor
    # untested — a constant nothing exercises is a constant nobody can trust.
    # Built from synthetic communities on purpose: `compare()` reads only name/size/cohesion/
    # members, and hand-picked sizes are the only way to breach one rule while staying clear of
    # the other. Doing it on the real graph kept tripping both at once.
    def synth(name, ids, cohesion):
        return {"name": name, "size": len(ids), "cohesion": cohesion, "members": sorted(ids)}

    gone = [f"a{i}" for i in range(40)]
    others = {f"r{r}": [f"r{r}n{i}" for i in range(36)] for r in range(4)}
    small = {"internal_edge_share": 0.8,
             "communities": [synth("A", gone, 0.30)] + [synth(k, v, 0.30) for k, v in others.items()]}
    dispersed = {"internal_edge_share": 0.8,
                 "communities": [synth(k, v + gone[i * 10:(i + 1) * 10], 0.05)
                                 for i, (k, v) in enumerate(others.items())]}
    regs, _, orph = CB.compare(small, dispersed)
    reasons = {b["name"]: why for b, _, _, why in orph}
    check("a community whose members dispersed is caught by membership, not called a regression",
          reasons.get("A") == "membership" and not any(b["name"] == "A" for b, *_ in regs),
          f"reasons {reasons}, regressions {[b['name'] for b, *_ in regs]}")
    check("and every recipient is a size the size rule would have accepted for A",
          all(abs(c["size"] - 40) <= 40 * CB.MAX_SIZE_DRIFT for c in dispersed["communities"]),
          f"sizes {[c['size'] for c in dispersed['communities']]} vs A's 40 — if one is outside "
          "the size rule, that rule could be doing the work instead of the overlap floor")

    # The other reshuffle shape, and the one only the SIZE rule catches: a community keeps its
    # members — overlap stays well above the floor — but absorbs enough new ones that density
    # falls on arithmetic alone, since it is 2E/(N(N-1)). `Presence` did exactly this, 17 nodes at
    # 0.104 to 53 at 0.07. Calling that a regression is the false alarm that gets a gate disabled.
    grown = copy.deepcopy(cur)
    target = grown["communities"][0]
    donor = next(c for c in grown["communities"][1:]
                 # Big enough to breach the size rule, small enough that membership overlap stays
                 # above the floor — otherwise the two rules are not being told apart.
                 if target["size"] * 0.3 < c["size"] < target["size"] * 0.6)
    target["members"] = sorted(set(target["members"]) | set(donor["members"]))
    target["size"] = len(target["members"])
    target["cohesion"] = round(target["cohesion"] * 0.6, 4)
    was = cur["communities"][0]
    regs, _, orph = CB.compare(cur, grown)
    check("absorbing nodes is judged on shape, not on the density that dilutes with size",
          was["name"] in {b["name"] for b, *_ in orph}
          and not any(b["name"] == was["name"] for b, _, _, _ in regs),
          f"size {was['size']} -> {target['size']}")
    check("and it got there on size, not because membership stopped matching",
          CB.overlap(was["members"], target["members"]) >= CB.MIN_OVERLAP,
          "the membership rule would have caught it anyway, so this proves nothing about size")

    print("\n[5] the check is wired to run")
    runner = (ROOT / "bench" / "run_all_tests.py").read_text(encoding="utf-8")
    check("registered in the suite", "test_cohesion_baseline.py" in runner)

    print("\n[6] the thin communities are named in the report, not just counted (J0.5)")
    # Match the INVOCATION among non-comment lines, not a mention: the hook explains itself at
    # length, so a substring search stays green after someone deletes the line it explains. That
    # exact false pass was found in the J8.2 gate by planting it.
    hook = (ROOT / "scripts" / "githooks" / "post-commit").read_text(encoding="utf-8")
    lines = [ln for ln in hook.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    call = [i for i, ln in enumerate(lines) if "cohesion_baseline.py" in ln and "--thin" in ln]
    update = [i for i, ln in enumerate(lines) if "graphify update" in ln]
    check("post-commit re-names them after every rebuild", len(call) == 1 and update[0] < call[0],
          f"{len(call)} invocation lines among {len(lines)} command lines")
    thin = [c for c in cur["communities"] if c["size"] < CB.MIN_REPORTED]
    tail = report.split(CB.MARKER)[-1] if CB.MARKER in report else ""
    missing = [c["name"] for c in thin if c["name"] not in tail]
    check(f"GRAPH_REPORT.md names all {len(thin)} of them", bool(tail) and not missing,
          f"missing {missing[:3]} — run `python scripts/cohesion_baseline.py --thin`"
          if tail else "no thin section — run `python scripts/cohesion_baseline.py --thin`")
    check("the report still says it omitted them, so the two halves agree",
          "thin omitted" in report and str(len(thin)) in report.split(CB.MARKER)[0])

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
