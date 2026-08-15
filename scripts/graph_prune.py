"""Drop the inferred edges that point at the wrong file (TODO J0.2).

J0.2 said: 307 INFERRED edges at 0.54 confidence, "roughly a third are wrong and nobody knows
which third". The second half of that was the fixable part, and the first half turned out to be
wrong. Measured on the 2026-08-15 graph:

  * **307 of 307 are grounded.** The target's name appears literally at the source line each edge
    cites. Not one is invented; 0.5 is graphify's prior for a *category* of evidence (a name passed
    as an argument, a name in a collection literal), not a per-edge probability of being false.
  * **15 of 307 resolve to the wrong file.** `llm.py:339` calls a nested `_push()` and the graph
    points it at `deploy/vps/afon_ticker.py`'s `push`. `agent.py` calls a nested `_run()` three
    times and lands on `tools/coding.py`. `autostart.py` calls its own `install()` and `status()`
    and lands on `shared/errors.py` and `brain/modes.py`. All 15 are bare local names colliding
    with a same-named symbol elsewhere in the tree.

So the honest fix is not "verify 307 semantic guesses" but one mechanical rule:

    an INFERRED edge that crosses a file boundary the source file never imports is unsupported.

A cross-file call needs a binding — an import of the name, an alias, or the module. Every one of
the 61 cross-file inferred edges that has one is kept; the 15 without one are dropped. Same-file
edges (231) are never touched, and no EXTRACTED edge is ever touched: this only removes claims the
extractor itself scored as guesses.

The bug is graphify's name resolution, not Afon's code, and graphify is not this repo's to fix —
so this runs from the post-commit hook after every rebuild, like the routing manifest, because
`graphify update` regenerates the graph from the AST and puts them all back.

    uv run python scripts/graph_prune.py            # prune graph.json
    uv run python scripts/graph_prune.py --check     # list what would go, touch nothing
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graphify-out" / "graph.json"


def bound_names(path: Path) -> set[str]:
    """Every name an import statement binds in a file, plus the module stems it names.

    Deliberately generous — `from x import y`, `import x.y as z`, and the module's own stem all
    count. The rule this feeds is meant to catch edges with NO possible binding, so a false
    "bound" keeps an edge and a false "unbound" deletes one. Erring toward keeping is correct.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1])
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
                names.add(a.name.split(".")[-1])
    return names


def unsupported(graph: dict) -> list[dict]:
    """The INFERRED edges that cross into a file the source never imports."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    cache: dict[str, set[str]] = {}
    out = []
    for lk in graph["links"]:
        if lk.get("confidence") != "INFERRED":
            continue
        src, tgt = lk.get("source_file"), (nodes.get(lk["target"]) or {}).get("source_file")
        # Only Python is in scope: the binding rule below is an import rule, and a shell script
        # naming another file is the ops layer, which routing_manifest.py handles on its own terms.
        if not src or not tgt or src == tgt or not src.endswith(".py") or not tgt.endswith(".py"):
            continue
        if src not in cache:
            cache[src] = bound_names(ROOT / src)
        name = re.sub(r"\(\)$", "", (nodes[lk["target"]].get("label") or "").split(".")[-1])
        candidates = {name, name.lstrip("_"), Path(tgt).stem}
        if candidates & cache[src]:
            continue
        out.append(lk)
    return out


def main() -> int:
    if not GRAPH.exists():
        print("graph prune: graph.json missing — run `graphify update .` first")
        return 1
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    doomed = unsupported(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    inferred = sum(1 for lk in graph["links"] if lk.get("confidence") == "INFERRED")
    print(f"graph prune: {len(doomed)} unsupported of {inferred} inferred "
          f"({len(graph['links'])} edges total)")
    for lk in doomed[:20]:
        tgt = nodes[lk["target"]]
        print(f"    {lk['source_file']}:{lk['source_location']} [{lk['relation']}] -> "
              f"{tgt.get('label')} @ {tgt.get('source_file')}")

    if "--check" in sys.argv:
        return 1 if doomed else 0
    if doomed:
        drop = {id(lk) for lk in doomed}
        graph["links"] = [lk for lk in graph["links"] if id(lk) not in drop]
        GRAPH.write_text(json.dumps(graph, indent=2), encoding="utf-8")
        print(f"graph prune: {len(graph['links'])} edges remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
