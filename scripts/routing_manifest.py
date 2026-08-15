"""Emit Afon's routing tables as graph edges instead of leaving them to inference (TODO J8.2).

Three tables decide, at runtime, which function actually handles a request:

    afon.edge.pc_agent.LOCAL_HANDLERS   op name    -> the laptop function that runs it
    afon.brain.tools.tool_handlers()    tool name  -> the handler the model's call lands on
    afon.brain.tools._LAZY_GROUPS       group name -> the modules it advertises

All three are dict *values*, and an AST extractor cannot follow a dict value to its callee. The
consequence is not a low-confidence edge — it is NO edge: measured on the 2026-08-15 graph,
`pc_agent` had 38 outbound edges, of which 10 were calls, and not one of them reached any of the
23 PC ops it dispatches. "What happens when the brain sends `screenshot`?" was unanswerable from
the graph, and "what breaks if I change `_screenshot_local`?" answered "nothing".

(The TODO framed this as a slice of J0.2's 607 INFERRED edges. Not so — those are a different
thing: 307 of them survive, every one AST-produced (a function passed as an argument, scored 0.5),
and none of them is a routing table. The tables are not guessed at badly; they are absent.
Same fix, stronger reason. J0.2 stays open on its own merits.)

So: read the tables from the LIVE objects — importing them, not re-typing them — resolve each
function to its graph node, and write the result both as a readable manifest and as edges injected
into graph.json. A hand-maintained copy of a routing table is the one thing worse than no manifest,
because it goes wrong silently; this file cannot disagree with the code because it imports it.

    uv run python scripts/routing_manifest.py            # write manifest + inject into graph.json
    uv run python scripts/routing_manifest.py --check     # resolve only, report, touch nothing

Runs from the post-commit hook right after `graphify update`, because an update rewrites graph.json
and drops the injected edges.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graphify-out" / "graph.json"
MANIFEST = ROOT / "graphify-out" / "routing-manifest.json"
ORIGIN = "manifest"  # marks our edges so a re-inject can remove exactly its own previous output

sys.path.insert(0, str(ROOT / "src"))


def slug(text: str) -> str:
    """graphify's id slug: every run of non-alphanumerics becomes one underscore, ends trimmed.

    Reproducing someone else's id scheme is a guess until it is checked, so nothing here relies on
    it being right — `resolve()` looks every id up in the graph and the gate fails on a miss. This
    is also why `__init__` must fold to `init` and a leading `_private` must lose its underscore:
    both are observable in graph.json, neither is documented.
    """
    return re.sub(r"_+", "_", re.sub(r"[^0-9A-Za-z]+", "_", text)).strip("_")


def file_id(obj: Any) -> str:
    """The node id of the FILE an object is defined in — what tells staleness from a real gap."""
    rel = Path(inspect.getfile(obj)).resolve().relative_to(ROOT).with_suffix("")
    return slug(rel.as_posix())


def node_id(obj: Any) -> str:
    """The graph node for a module (its file) or a function (its file, then its bare name)."""
    base = file_id(obj)
    return base if inspect.ismodule(obj) else f"{base}_{slug(obj.__name__)}"


def file_nodes(graph: dict) -> dict[str, str]:
    """Repo-relative path -> the graph node for that FILE (not the symbols inside it).

    A file node is the one whose id is the slug of its own path. Using our own slug on both sides
    keeps the two derivations honest about each other.
    """
    return {n["source_file"]: n["id"] for n in graph["nodes"]
            if n.get("source_file")
            and n["id"] == slug(Path(n["source_file"]).with_suffix("").as_posix())}


# A repo-relative path mentioned inside an ops script. Requires an extension, because dropping it
# would make `scripts/deploy_vps.env` and `scripts/deploy_vps.sh` slug to the same id — which is
# how the first version of this drew an edge from preflight to the deploy script off a mention of
# the env file. Resolution goes through file_nodes() by exact path for the same reason.
_OPS_REF = re.compile(
    r"(?<![\w/.\-])((?:scripts|deploy|src|bench|clients|skills|personality)[/\\][\w./\\-]+\.\w+)")


def ops_edges(graph: dict) -> list[dict[str, str]]:
    """What the ops layer touches — the other thing an AST extractor structurally cannot see.

    `deploy_vps.sh` invokes `preflight.sh` and `verify_vps_sync.sh` as SHELL COMMANDS, so nothing
    in the graph connected them: the most dangerous file in the repo sat at degree 1, and "what
    does a deploy touch" — the question J0.4 wanted — had no answer. Only ops files are read as
    sources; a path mentioned in application code is already an import or nothing.
    """
    by_path = file_nodes(graph)
    edges: list[dict[str, str]] = []
    for rel, src_id in sorted(by_path.items()):
        # Shell and hooks only. A `.py` under scripts/ gets its real edges from its imports, and
        # reading it here adds nothing but docstring mentions.
        if not rel.startswith(("scripts/", "deploy/")) or rel.endswith(".py"):
            continue
        try:
            body = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Comment lines are dropped: these files explain themselves at length, and a path named in
        # a comment is a cross-reference, not an invocation. Keeping them would put unverified
        # edges on the ops layer — the exact defect J0.2 is about — to inflate a count.
        body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        for ref in sorted(set(_OPS_REF.findall(body))):
            target = ref.replace("\\", "/")
            tgt_id = by_path.get(target)
            if not tgt_id or tgt_id == src_id:
                continue
            edges.append({
                "source": src_id, "target": tgt_id, "target_file": tgt_id,
                "relation": "references", "context": f"ops:{Path(target).name}",
                "table": rel, "owner": "", "ref": target,
            })
    return edges


def build() -> list[dict[str, str]]:
    """The routing tables, read from the live objects, as edge descriptions."""
    from afon.brain import tools as T
    from afon.edge import pc_agent

    # `indirect_call` rather than a new `routes_to`: a dispatch through a table IS an indirect
    # call, and — the deciding reason — it is one of the relations graphify's `affected` traverses
    # by default. A bespoke relation would be truthful and unqueried, which is barely better than
    # the missing edge it replaces. The `context` field keeps the op name.
    edges: list[dict[str, str]] = []
    dispatch = node_id(pc_agent._run_op)
    for op, fn in sorted(pc_agent.LOCAL_HANDLERS.items()):
        edges.append({
            "source": dispatch, "target": node_id(fn), "target_file": file_id(fn),
            "relation": "indirect_call", "context": f"pc_op:{op}",
            "table": "afon.edge.pc_agent.LOCAL_HANDLERS",
            "owner": pc_agent.HANDLER_OWNERS.get(op, ""),
        })

    registry = node_id(T.tool_handlers)
    for name, fn in sorted(T.tool_handlers().items()):
        edges.append({
            "source": registry, "target": node_id(fn), "target_file": file_id(fn),
            "relation": "indirect_call", "context": f"tool:{name}",
            "table": "afon.brain.tools.tool_handlers()", "owner": "",
        })

    groups = node_id(T.group_tool_schemas)
    for group, mods in sorted(T._LAZY_GROUPS.items()):
        for mod in mods:
            edges.append({
                "source": groups, "target": node_id(mod), "target_file": file_id(mod),
                "relation": "advertises", "context": f"group:{group}",
                "table": "afon.brain.tools._LAZY_GROUPS", "owner": "",
            })
    return edges


def resolve(edges: list[dict[str, str]], graph: dict) -> dict[str, list[dict[str, str]]]:
    """Split the edges by whether the graph knows both endpoints.

    The distinction that matters is WHY a target is missing, because only one of the two answers
    is a defect here. If the target's *file* is in the graph, the graph is simply older than the
    function — ordinary staleness in a repo that commits rarely, and not something this script can
    or should fix. If the file is absent too, the graph has never seen that module at all: a real
    gap, and the thing worth failing on.
    """
    ids = {n["id"] for n in graph["nodes"]}
    files = set(file_nodes(graph).values())
    out: dict[str, list[dict[str, str]]] = {"ok": [], "stale": [], "unmapped": []}
    for e in edges:
        if e["source"] in ids and e["target"] in ids:
            out["ok"].append(e)
        elif e["target_file"] in files:
            out["stale"].append(e)
        else:
            out["unmapped"].append(e)
    return out


def inject(graph: dict, edges: list[dict[str, str]]) -> tuple[int, int]:
    """Replace this script's previous edges with the current ones. Returns (added, collapsed).

    `graph.json` is not a multigraph, so an edge whose endpoints already have one would be
    collapsed on load — silently overwriting a real extracted `calls` edge with ours. Those are
    skipped and counted rather than added: the routing fact is already represented.

    The pair key follows the graph's own `directed` flag. This graph is UNDIRECTED, where (a, b)
    and (b, a) are the same edge — and the first version of this compared ordered pairs, so the
    manifest's `tool_handlers -> if_then` quietly replaced the extracted `if_then -> tool_handlers`
    call. Found because a rebuild reported 191 edges injected and 190 present afterwards.
    """
    directed = bool(graph.get("directed"))

    def key(s: str, t: str):
        return (s, t) if directed else frozenset((s, t))

    links = [lk for lk in graph["links"] if lk.get("_origin") != ORIGIN]
    pairs = {key(lk["source"], lk["target"]) for lk in links}
    added = collapsed = 0
    for e in edges:
        if key(e["source"], e["target"]) in pairs:
            collapsed += 1
            continue
        links.append({
            "relation": e["relation"], "context": e["context"], "confidence": "EXTRACTED",
            "source_file": e["table"], "source_location": "routing table",
            "weight": 1.0, "_origin": ORIGIN,
            "source": e["source"], "target": e["target"], "confidence_score": 1.0,
        })
        pairs.add(key(e["source"], e["target"]))
        added += 1
    graph["links"] = links
    return added, collapsed


def main() -> int:
    check = "--check" in sys.argv
    if not GRAPH.exists():
        print("routing manifest: graph.json missing — run `graphify update .` first")
        return 0 if check else 1

    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    edges = build() + ops_edges(graph)
    parts = resolve(edges, graph)
    print(f"routing manifest: {len(edges)} entries — {len(parts['ok'])} resolved, "
          f"{len(parts['stale'])} awaiting a rebuild, {len(parts['unmapped'])} unmapped")
    for e in parts["unmapped"][:10]:
        print(f"    UNMAPPED {e['context']} -> {e['target']} ({e['table']})")

    if check:
        return 1 if parts["unmapped"] else 0

    MANIFEST.write_text(json.dumps({"tables": sorted({e["table"] for e in edges}), "edges": edges},
                                   indent=1) + "\n", encoding="utf-8")
    added, collapsed = inject(graph, parts["ok"])
    GRAPH.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(f"routing manifest: injected {added} edges into graph.json "
          f"({collapsed} already present as extracted edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
