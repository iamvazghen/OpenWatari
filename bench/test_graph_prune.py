"""Inferred edges that point at the wrong file are gone, and only those (TODO J0.2).

`scripts/graph_prune.py` deletes edges from the graph, which is a dangerous thing for a script to
do quietly. Three properties make it safe, and each can rot without a sound:

  1. it only ever touches INFERRED edges — the extractor's own guesses, never an EXTRACTED fact;
  2. an edge with a real import binding survives, so the rule cannot eat the 61 legitimate
     cross-file inferences;
  3. an edge without one does not, so the 15 misresolutions stay gone after each rebuild.

The prune exists because the target's *evidence* was never the problem: all 307 inferred edges are
grounded — the target's name is literally at the line each cites. 15 resolved that name to the
wrong file. `_push()` nested inside `llm.py` was pointed at the VPS ticker's `push`.

Run:
    uv run python bench/test_graph_prune.py
"""

from __future__ import annotations

import ast
import copy
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import graph_prune as GP  # noqa: E402

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
    if not GP.GRAPH.exists():
        check("graph.json present", False, "run `graphify update .`")
        print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
        return 1
    graph = json.loads(GP.GRAPH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    inferred = [lk for lk in graph["links"] if lk.get("confidence") == "INFERRED"]

    print("[1] the graph on disk carries no unsupported inferred edge")
    left = GP.unsupported(graph)
    check(f"nothing left to prune ({len(inferred)} inferred edges remain)", not left,
          f"{len(left)} unsupported — run `python scripts/graph_prune.py`: "
          f"{[(lk['source_file'], lk['source_location']) for lk in left][:3]}")

    print("\n[2] every inferred edge is grounded at the line it cites")
    # The half of J0.2 that turned out to be false — "roughly a third are wrong" — and the reason
    # the fix is a resolution rule rather than a cull. If this ever fails, the extractor has begun
    # inventing evidence and the prune rule is the wrong tool for it.
    # A file edited AFTER the graph was built has moved the lines the graph cites, so checking a
    # line reference against it measures staleness, not grounding. Found by deleting 26 lines from
    # gmail.py: three edges "lost" their target on a file the graph had not been rebuilt for. The
    # graph rebuilds on commit (post-commit hook) and this repo commits rarely by policy, so
    # skipping those is the same call J8.2's gate makes about its own stale entries.
    built = GP.GRAPH.stat().st_mtime
    ungrounded, stale = [], 0
    for lk in inferred:
        m = re.match(r"L(\d+)", lk.get("source_location") or "")
        path = ROOT / (lk.get("source_file") or "")
        if not m or not path.is_file():
            continue
        if path.stat().st_mtime > built:
            stale += 1
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        i = int(m.group(1)) - 1
        window = "\n".join(lines[max(0, i - 1):i + 2])
        name = re.sub(r"\(\)$", "", (nodes[lk["target"]].get("label") or "").split(".")[-1])
        if name and not re.search(rf"\b{re.escape(name)}\b", window):
            ungrounded.append((lk["source_file"], lk["source_location"], name))
    check(f"the target's name appears at every cited line "
          f"({len(inferred) - stale} checked, {stale} skipped as newer than the graph)",
          not ungrounded, str(ungrounded[:3]))

    print("\n[3] the rule keeps what it should and drops what it should not")
    same = [lk for lk in inferred
            if lk.get("source_file") == (nodes.get(lk["target"]) or {}).get("source_file")]
    check(f"same-file edges are never candidates ({len(same)})",
          not [lk for lk in GP.unsupported(graph) if lk in same])
    # A fabricated edge into a module this file does not import: the exact shape of the 15.
    planted = copy.deepcopy(graph)
    victim = next(n for n in planted["nodes"]
                  if (n.get("source_file") or "").endswith("src/afon/brain/coaching.py"))
    origin = next(n for n in planted["nodes"]
                  if (n.get("source_file") or "").endswith("src/afon/edge/pc_agent.py"))
    planted["links"].append({"relation": "calls", "context": "call", "confidence": "INFERRED",
                             "source_file": "src/afon/edge/pc_agent.py", "source_location": "L1",
                             "confidence_score": 0.5, "source": origin["id"],
                             "target": victim["id"]})
    check("an edge into an unimported module is caught",
          any(lk["source"] == origin["id"] and lk["target"] == victim["id"]
              for lk in GP.unsupported(planted)),
          "pc_agent.py does not import brain/coaching.py — nothing binds that name")
    # Same fabrication, but EXTRACTED. The prune must not touch a fact the extractor stood behind.
    planted["links"][-1] = dict(planted["links"][-1], confidence="EXTRACTED", confidence_score=1.0)
    check("the same edge marked EXTRACTED is left alone",
          not any(lk["target"] == victim["id"] and lk["source"] == origin["id"]
                  for lk in GP.unsupported(planted)))

    print("\n[4] a real import binding is what saves an edge")
    # Not a synthetic pair: pick a file that genuinely imports another and confirm the rule reads
    # the import. Asserting on invented files would only test the invention.
    kept = [lk for lk in inferred if lk not in same]
    check(f"cross-file edges survive on their imports ({len(kept)})", bool(kept))
    sample = kept[0]
    src = ROOT / sample["source_file"]
    tgt_name = re.sub(r"\(\)$", "", (nodes[sample["target"]].get("label") or "").split(".")[-1])
    bound = GP.bound_names(src)
    check("and the binding is a real import statement in the source file",
          bool({tgt_name, tgt_name.lstrip("_"),
                Path(nodes[sample["target"]]["source_file"]).stem} & bound),
          f"{sample['source_file']} -> {tgt_name}")
    check("bound_names reads imports, not any occurrence of the word",
          "json" in GP.bound_names(ROOT / "scripts" / "graph_prune.py")
          and "unsupported" not in GP.bound_names(ROOT / "scripts" / "graph_prune.py"))
    check("a file that cannot be parsed binds nothing rather than raising",
          GP.bound_names(ROOT / "graphify-out" / "graph.json") == set())

    print("\n[5] it runs after every rebuild, or the edges come straight back")
    hook = (ROOT / "scripts" / "githooks" / "post-commit").read_text(encoding="utf-8")
    lines = [ln for ln in hook.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    call = [i for i, ln in enumerate(lines) if "graph_prune.py" in ln]
    update = [i for i, ln in enumerate(lines) if "graphify update" in ln]
    cluster = [i for i, ln in enumerate(lines) if "cluster-only" in ln]
    check("post-commit runs the prune", len(call) == 1 and update and update[0] < call[0],
          f"{len(call)} invocation lines among {len(lines)} command lines")
    check("and re-clusters afterwards, so the report and the page show the pruned graph",
          bool(cluster) and call and cluster[0] > call[0],
          "otherwise GRAPH_REPORT.md and graph.html render the edges that were just removed")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
