"""The routing manifest actually connects the routing tables to the graph (TODO J8.2).

`scripts/routing_manifest.py` exists because three dict-valued routing tables were invisible to an
AST extractor — not as weak edges, as no edges. This gate holds the four things that make it worth
having, each of which can break silently:

  1. the manifest is READ FROM the live tables, never a copy of them;
  2. every entry resolves to a real graph node (the id scheme is reverse-engineered, so it is a
     guess until something checks it);
  3. injecting twice is the same as injecting once, since it runs on every commit;
  4. the post-commit hook runs it AFTER `graphify update`, which would otherwise drop the edges.

Run:
    uv run python bench/test_routing_manifest.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import routing_manifest as RM  # noqa: E402

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
    from afon.brain import tools as T
    from afon.edge import pc_agent

    edges = RM.build()

    print("[1] the manifest is the live tables, not a copy of them")
    pc = [e for e in edges if e["table"].endswith("LOCAL_HANDLERS")]
    tools = [e for e in edges if e["table"].endswith("tool_handlers()")]
    groups = [e for e in edges if e["table"].endswith("_LAZY_GROUPS")]
    check(f"one entry per PC op ({len(pc)})", len(pc) == len(pc_agent.LOCAL_HANDLERS))
    check(f"one entry per tool handler ({len(tools)})", len(tools) == len(T.tool_handlers()))
    check(f"one entry per lazy group membership ({len(groups)})",
          len(groups) == sum(len(m) for m in T._LAZY_GROUPS.values()))
    # A table that grew but whose ops all point at one module would pass the counts above while
    # meaning nothing, so check the op NAMES round-trip too.
    check("every PC op name appears as a context",
          {e["context"] for e in pc} == {f"pc_op:{op}" for op in pc_agent.LOCAL_HANDLERS})
    check("every tool name appears as a context",
          {e["context"] for e in tools} == {f"tool:{n}" for n in T.tool_handlers()})
    check("each PC op records the module that claimed it",
          all(e["owner"] for e in pc), "an op with no owner means HANDLER_OWNERS drifted")

    print("\n[2] the reverse-engineered id scheme resolves against the real graph")
    graph_path = ROOT / "graphify-out" / "graph.json"
    if not graph_path.exists():
        check("graph.json present", False, "run `graphify update .`")
        print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
        return 1
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    edges += RM.ops_edges(graph)
    parts = RM.resolve(edges, graph)
    # `stale` is NOT a failure: this repo commits rarely by policy, so a handler can legitimately
    # be newer than the graph. `unmapped` means the graph has never seen the module at all, which
    # is the gap J8.2 is about and the only condition worth being red over.
    check(f"no entry points at a module the graph has never seen ({len(parts['unmapped'])})",
          not parts["unmapped"],
          f"unmapped: {[e['context'] for e in parts['unmapped']][:5]}")
    check(f"most entries resolve outright ({len(parts['ok'])}/{len(edges)})",
          len(parts["ok"]) >= len(edges) * 0.9,
          f"{len(parts['stale'])} awaiting a rebuild — run `graphify update .`")
    check("slug folds __init__ and strips a leading underscore",
          RM.slug("src/afon/brain/tools/__init__") == "src_afon_brain_tools_init"
          and RM.slug("_run_op") == "run_op")

    print("\n[3] injection is idempotent and cannot overwrite an extracted edge")
    base = copy.deepcopy(graph)
    base["links"] = [lk for lk in base["links"] if lk.get("_origin") != RM.ORIGIN]
    before = len(base["links"])
    once = copy.deepcopy(base)
    added1, _ = RM.inject(once, parts["ok"])
    twice = copy.deepcopy(once)
    added2, _ = RM.inject(twice, parts["ok"])
    check("a second injection adds the same edges, not more",
          len(once["links"]) == len(twice["links"]) == before + added1 and added1 == added2,
          f"{len(once['links'])} vs {len(twice['links'])}")
    check("injection removes only its own previous edges",
          len([lk for lk in twice["links"] if lk.get("_origin") != RM.ORIGIN]) == before)
    # Endpoint-sharing must be judged the way the GRAPH judges it. This one is undirected, so
    # (a, b) and (b, a) are one edge — and comparing ordered pairs is how the manifest's
    # `tool_handlers -> if_then` silently replaced the extracted `if_then -> tool_handlers` call.
    # A rebuild reporting 191 injected and 190 present is what surfaced it.
    pair = ((lambda s, t: (s, t)) if graph.get("directed")
            else (lambda s, t: frozenset((s, t))))
    check("no injected edge shares endpoints with an extracted one, as the graph counts endpoints",
          len({pair(lk["source"], lk["target"]) for lk in once["links"]}) == len(once["links"]),
          "an undirected graph collapses (a,b) with (b,a) — the loser is the extracted edge")
    check("injected edges are marked so they can be told from AST findings",
          all(lk["_origin"] == RM.ORIGIN and lk["source_location"] == "routing table"
              for lk in once["links"] if lk.get("_origin") == RM.ORIGIN))
    # The relation must stay one graphify's `affected` walks by default, or the edges are
    # truthful and unqueried — the state they were added to fix.
    check("dispatch edges use a relation the default traversal follows",
          all(e["relation"] == "indirect_call" for e in pc + tools))

    print("\n[4] the hook runs it after the rebuild that would otherwise drop it")
    hook = (ROOT / "scripts" / "githooks" / "post-commit").read_text(encoding="utf-8")
    # Match the INVOCATION, not a mention: the hook's comment block explains why the call is there,
    # so a substring search for the filename stays green after someone deletes the line it explains.
    # (Found by planting exactly that regression — the first version of this check did not bite.)
    code = [ln for ln in hook.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    call = [i for i, ln in enumerate(code) if "routing_manifest.py" in ln and "python" in ln]
    update = [i for i, ln in enumerate(code) if "graphify update" in ln]
    check("post-commit actually runs routing_manifest.py", len(call) == 1,
          f"found {len(call)} invocation lines among {len(code)} command lines")
    if call and update:
        check("it runs after `graphify update`, not before", update[0] < call[0])
        check("its output lands in the log a person can read",
              ".last-update.log" in code[call[0]])

    print("\n[5] the ops layer is connected by invocations, not by mentions (J0.4)")
    ops = RM.ops_edges(graph)
    by_src = {(e["table"], e["context"]) for e in ops}
    # The question J0.4 wanted answerable: what does a deploy touch? Both are shell COMMANDS, so
    # nothing in the graph connected them and the most dangerous file in the repo sat at degree 1.
    check("the deploy reaches the two gates it runs",
          ("scripts/deploy_vps.sh", "ops:preflight.sh") in by_src
          and ("scripts/deploy_vps.sh", "ops:verify_vps_sync.sh") in by_src, str(sorted(by_src)))
    check("the post-commit hook reaches what it invokes",
          ("scripts/githooks/post-commit", "ops:routing_manifest.py") in by_src)
    # NOT checked here: that `.py` files are excluded as sources. The exclusion is real and stays
    # in the script (a .py's edges come from its imports; reading it adds only docstring mentions),
    # but no .py under scripts/ currently names a prefixed path outside a comment, so an assertion
    # about it passes whether the filter exists or not. Planting the regression proved that. A
    # green tick over nothing is what J6.3 was about, so there is no tick.
    #
    # A path named in a comment is a cross-reference, not an invocation. These files explain
    # themselves at length, so admitting comments would put unverified edges on the ops layer —
    # the exact defect J0.2 is about — purely to inflate a count.
    check("a path mentioned only in a comment produces no edge",
          "scripts/graph_fresh.py" in hook
          and ("scripts/githooks/post-commit", "ops:graph_fresh.py") not in by_src,
          "post-commit discusses graph_fresh.py at length without running it")
    check("no self-edges", all(e["source"] != e["target"] for e in ops))
    # `deploy_vps.env` and `deploy_vps.sh` slug identically once the extension is dropped, and the
    # first version of this drew preflight -> the deploy SCRIPT off a mention of the env FILE.
    # Resolution therefore goes through an exact path lookup, and the property to assert is that
    # every edge lands on the file it names — not that one known-bad pair is absent. Checking the
    # pair was the first attempt here and it did not bite when the bug was planted back in: the
    # fabricated edge carries the env file's basename in its context, so it never matched.
    src_of = {n["id"]: n.get("source_file") for n in graph["nodes"]}
    wrong = [(e["table"], e["ref"], src_of.get(e["target"])) for e in ops
             if src_of.get(e["target"]) != e["ref"]]
    check("every ops edge lands on the file it actually names", not wrong, str(wrong[:3]))

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
