"""Record per-community cohesion and fail when it gets worse (TODO J8.1).

"Improve cohesion" is unfalsifiable without a number to beat, and Part J spends ten entries
naming communities by their cohesion score. This records those scores so a change that makes a
community mushier can be told so, instead of being noticed a month later in a report nobody
diffed.

**Cohesion here is graphify's own metric, not a lookalike.** It is internal edge density,
2E/(N(N-1)) over a community's members. That was reverse-engineered from graph.json and then
checked against every cohesion line graphify prints in GRAPH_REPORT.md — 147 of 147 agree to the
2 decimals it reports. `bench/test_cohesion_baseline.py` re-runs that comparison, because a gate
on a metric that has quietly drifted from the one the report shows is worse than no gate.

**The hard part is not the number, it is identity.** Louvain communities are not stable objects:
they carry an index and a name derived from their dominant node, and both move when the graph
changes. `Presence` was 17 nodes at 0.104 in the 2026-08-02 report and 53 nodes at 0.07 today,
having absorbed two neighbours — no code got worse. So communities are matched to the baseline by
membership overlap, and a community that reshuffled past MIN_OVERLAP is reported as NOT COMPARABLE
rather than as a regression. A gate that cries wolf on re-clustering gets switched off.

**And the red light is internal degree, not cohesion.** Density falls with size by arithmetic, so
the first commit after this file landed produced four "regressions" that were communities growing
by two or three nodes while staying exactly as interconnected. Both numbers are recorded and both
are printed — cohesion because it is what the report and Part J quote — but only degree fails a
build. See MAX_DEGREE_DROP.

`internal_edge_share` — the fraction of edges that stay inside a community — is the number that
survives reshuffling, so it is gated too, and it is the one to watch if matching ever collapses.

    uv run python scripts/cohesion_baseline.py            # check against the baseline
    uv run python scripts/cohesion_baseline.py --record    # accept today's numbers as the baseline
    uv run python scripts/cohesion_baseline.py --thin      # the communities the report omits (J0.5)
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graphify-out" / "graph.json"
# Lives under bench/ for a reason: bench/ is in .graphifyignore (J8.3), so recording a baseline
# cannot add a node to the graph the baseline measures. It is also tracked, unlike graphify-out/,
# which is ignored wholesale — a gate whose baseline is untracked has nothing to say about a PR.
BASELINE = ROOT / "bench" / "cohesion_baseline.json"

# Two ticks of the 2 decimals GRAPH_REPORT.md prints. Used for the aggregate share, which is a
# ratio and does not drift with size.
TOLERANCE = 0.02
# Below this overlap, "the same community" is not a claim worth making about a Louvain partition.
# Two equal communities merging score exactly 0.5, so the floor sits above that. 0.7 rather than
# 0.6 was measured on the re-cluster this file's first commit caused: at 0.6 a community that shed
# 12 of its 38 members read as a regression, at 0.7 it is correctly NOT COMPARABLE, and coverage
# only falls from 170 to 161 of 192 communities.
MIN_OVERLAP = 0.7
# What the per-community gate actually compares: INTERNAL DEGREE, 2E/N — edges per member inside
# the community. Cohesion itself is DENSITY, 2E/(N(N-1)), which falls with size by construction,
# so gating on it fails on arithmetic. Measured, not assumed: the first commit after this landed
# grew four communities by 2-3 nodes each and the density gate called all four regressions —
# `voice_health.py` 18 -> 21 nodes read as 0.183 -> 0.148, while its internal degree moved 3.11 ->
# 2.96, under 5%. Their members had not become any less connected to each other.
# So cohesion is still recorded and reported, because it is the number GRAPH_REPORT.md prints and
# Part J's findings quote — but the red light is degree.
MAX_DEGREE_DROP = 0.15  # 3x the largest movement seen across a real re-cluster
# graphify's own cutoff for printing a community in GRAPH_REPORT.md, and the heading it drops them
# under ("33 thin omitted"). `--thin` names them beneath this marker (J0.5).
MIN_REPORTED = 3
MARKER = "## Thin communities (omitted above)"


def measure(graph: dict) -> dict:
    """Per-community size, cohesion and membership, plus the aggregate that survives reshuffling."""
    comm = {n["id"]: n.get("community") for n in graph["nodes"]}
    names: dict[int, str] = {}
    members: dict[int, list[str]] = defaultdict(list)
    for n in graph["nodes"]:
        c = n.get("community")
        if c is None:
            continue
        names.setdefault(c, n.get("community_name") or f"community {c}")
        members[c].append(n["id"])

    intra: Counter[int] = Counter()
    spanning = 0
    for lk in graph["links"]:
        a, b = comm.get(lk["source"]), comm.get(lk["target"])
        if a is None or b is None:
            continue
        if a == b:
            intra[a] += 1
        else:
            spanning += 1

    out = []
    for c, ids in members.items():
        n = len(ids)
        possible = n * (n - 1) / 2
        out.append({
            "name": names[c],
            "size": n,
            "cohesion": round(intra[c] / possible, 4) if possible else 0.0,
            "internal_edges": intra[c],
            "degree": round(2 * intra[c] / n, 4) if n else 0.0,
            "members": sorted(ids),
        })
    out.sort(key=lambda e: (-e["size"], e["name"]))
    inside = sum(intra.values())
    return {
        "graph": {"nodes": len(graph["nodes"]), "links": len(graph["links"]),
                  "communities": len(out), "commit": graph.get("built_at_commit", "")},
        "internal_edge_share": round(inside / (inside + spanning), 4) if inside + spanning else 0.0,
        "communities": out,
    }


def overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


def compare(base: dict, cur: dict) -> tuple[list, list, list]:
    """(regressions, improvements, not_comparable) — matched by membership, never by name.

    Names are not unique (two communities share one today) and indices renumber on every
    re-cluster, so both are labels for humans and neither is an identity.
    """
    pool = list(cur["communities"])
    regressions, improvements, orphans = [], [], []
    for b in base["communities"]:
        best, score = None, 0.0
        for c in pool:
            s = overlap(b["members"], c["members"])
            if s > score:
                best, score = c, s
        if best is None or score < MIN_OVERLAP:
            orphans.append((b, round(score, 2), best["name"] if best else "-", "membership"))
            continue
        pool.remove(best)
        was, now = degree(b), degree(best)
        delta = (now - was) / was if was else 0.0
        if delta < -MAX_DEGREE_DROP:
            regressions.append((b, best, round(delta, 4), round(score, 2)))
        elif delta > MAX_DEGREE_DROP:
            improvements.append((b, best, round(delta, 4), round(score, 2)))
    return regressions, improvements, orphans


def degree(entry: dict) -> float:
    """Internal edges per member. Derived when absent so an older baseline still compares."""
    if "degree" in entry:
        return entry["degree"]
    n = entry["size"]
    return entry["cohesion"] * (n - 1) if n > 1 else 0.0


def main() -> int:
    if not GRAPH.exists():
        print("cohesion baseline: graph.json missing — run `graphify update .` first")
        return 1
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    cur = measure(graph)

    if "--thin" in sys.argv:
        # J0.5: GRAPH_REPORT.md says "33 thin omitted" and never names them, so a reviewer working
        # from the report reads "not in the report" as "does not exist". Names them IN the report,
        # because a listing printed to a terminal that nobody re-runs is not visibility. Rewritten
        # in place rather than appended: `graphify update` regenerates the report, and this must be
        # safe to run after every one of them.
        thin = [c for c in cur["communities"] if c["size"] < MIN_REPORTED]
        block = [MARKER, "",
                 f"The {len(thin)} communities under {MIN_REPORTED} nodes that the section above "
                 "omits. Small is not the same as absent.", ""]
        block += [f"- **{c['name']}** — {c['size']} node(s), "
                  f"{', '.join(m for m in c['members'][:4])}" for c in thin]
        report = ROOT / "graphify-out" / "GRAPH_REPORT.md"
        text = report.read_text(encoding="utf-8").split(MARKER)[0].rstrip() if report.exists() else ""
        report.write_text(text + "\n\n" + "\n".join(block) + "\n", encoding="utf-8")
        print(f"thin communities: named {len(thin)} of {len(cur['communities'])} in "
              f"{report.name}")
        return 0

    if "--record" in sys.argv:
        BASELINE.write_text(json.dumps(cur, indent=1) + "\n", encoding="utf-8")
        print(f"cohesion baseline: recorded {len(cur['communities'])} communities, "
              f"internal edge share {cur['internal_edge_share']:.3f} -> {BASELINE.name}")
        return 0

    if not BASELINE.exists():
        print(f"cohesion baseline: {BASELINE.name} missing — run with --record")
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    regressions, improvements, orphans = compare(base, cur)

    share_drop = base["internal_edge_share"] - cur["internal_edge_share"]
    print(f"cohesion: {len(cur['communities'])} communities, internal edge share "
          f"{cur['internal_edge_share']:.3f} (baseline {base['internal_edge_share']:.3f})")
    for b, c, d, s in regressions:
        print(f"  WORSE  {b['name']!r} internal degree {degree(b):.2f} -> {degree(c):.2f} "
              f"({d:+.0%}), cohesion {b['cohesion']:.3f} -> {c['cohesion']:.3f}, "
              f"size {b['size']} -> {c['size']}, overlap {s}")
    for b, c, d, s in improvements:
        print(f"  better {b['name']!r} internal degree {degree(b):.2f} -> {degree(c):.2f} "
              f"({d:+.0%})")
    for b, s, near, why in orphans:
        print(f"  NOT COMPARABLE {b['name']!r} (size {b['size']}) — best overlap {s} with {near!r}, "
              f"{why} changed; re-clustered, not regressed")

    if regressions or share_drop > TOLERANCE:
        if share_drop > TOLERANCE:
            print(f"\nFAIL: edges leaving their community — internal share fell {share_drop:.3f}")
        print(f"\nFAIL: {len(regressions)} communities lost more than {MAX_DEGREE_DROP:.0%} of "
              f"their internal degree.\n"
              "If the change is deliberate, re-record with --record and say why in the commit.")
        return 1
    print("OK: no community lost cohesion beyond tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
