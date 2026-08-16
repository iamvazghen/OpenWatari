"""J8.4 — the layering, written down and checked, so "no import cycles" stops being luck.

graphify found no import cycles, which is genuinely good and worth keeping. But the graph has no
concept of a LAYER: an edge module importing a brain store, or a tool importing the agent, is just
another edge to it. So the property everyone relies on is currently a lucky snapshot that nothing
defends.

The layers, bottom to top, and why the boundary is real rather than aesthetic:

    config.py / bench_metrics.py / setup_wizard.py   root: read by everything
    shared/, protocols/                              base: wire formats + cross-process helpers
    brain/                                           the 24/7 orchestrator (VPS, headless)
    brain/tools/                                     capabilities the agent calls
    edge/                                            the laptop: mic, speakers, camera, PC_LINK

The load-bearing fact is that **edge and brain run on different machines**. The brain is a headless
VPS with no PortAudio, no camera and no Windows; the edge is the laptop. So a cross-boundary import
is not a style question — at module level it decides whether the process can start at all.

Four rules, each one describing what is true today:

  R1  base and root import nothing upward. Verified: zero such imports. This is what keeps
      `config` importable from anywhere without a cycle.
  R2  brain/tools may import edge ONLY lazily, inside a function. `tools/audioout.py` legitimately
      drives the laptop's speakers through PC_LINK, and does it inside `try:` at call time — at
      module level the same import would take the whole tool registry down on the VPS, turning
      "this capability is unavailable here" into "the brain does not boot".
  R3  every edge<->brain import is a DECLARED seam. There are 16, and each is one of three
      deliberate arrangements (local-brain mode, PC_LINK handlers, shared brain helpers reused on
      the laptop). A seventeenth appearing without a decision is what this catches.
  R4  no module-level import cycle among afon modules. Lazy imports inside functions are excluded
      on purpose: they are the documented technique this codebase uses to keep the graph acyclic,
      and counting them would report a cycle for code that imports fine.

Hermetic: pure AST over src/afon. Nothing is imported, so it runs on a machine with no audio stack.

    uv run python bench/test_layering.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "afon"

BASE = ("shared", "protocols")
ROOT_MODULES = ("config", "bench_metrics", "setup_wizard", "__init__")

#: The edge<->brain seams that exist by decision. Grouped by the arrangement that justifies them,
#: because the reason is the point: an entry here says "someone chose this", not "this compiles".
DECLARED_SEAMS = {
    # Local-brain mode: the edge can run the brain in-process instead of over the WS link.
    "edge.brain_bridge -> brain.agent",
    "edge.brain_bridge -> brain.scheduler",
    "edge.remote_brain -> brain.agent",
    # Brain helpers reused on the laptop side (pure logic, no VPS state).
    "edge.affect_tts -> brain.affect",
    "edge.proper_nouns -> brain.contacts",
    # PC_LINK: pc_agent merges the tools' LOCAL_HANDLERS so laptop-only ops execute on the laptop.
    "edge.pc_agent -> brain.tools.audioout",
    "edge.pc_agent -> brain.tools.browser",
    "edge.pc_agent -> brain.tools.camera",
    "edge.pc_agent -> brain.tools.coding",
    "edge.pc_agent -> brain.tools.documents",
    "edge.pc_agent -> brain.tools.localplay",
    "edge.pc_agent -> brain.tools.system",
    # The speaker gate's borderline-score camera check (I1 second factor).
    "edge.speaker_gate -> brain.tools.camera",
    # The other direction: laptop audio, driven from a tool, lazily (see R2).
    "brain.tools.audioout -> edge.audio_devices",
    "brain.tools.audioout -> edge.switch_audio",
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


def layer_of(module: str) -> str:
    head = module.split(".")[0]
    if head == "brain" and module.split(".")[1:2] == ["tools"]:
        return "brain.tools"
    if head in ("brain", "edge") or head in BASE:
        return head
    return "root"


def imports(path: Path) -> list[tuple[str, int, bool]]:
    """(imported afon module, line, is_module_level) for every intra-package import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {id(n) for n in tree.body}          # statements directly in the module body
    found = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            if name.startswith("afon."):
                found.append((name[len("afon."):], node.lineno, id(node) in top))
    return found


def modules() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(PKG.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append((p.relative_to(PKG).with_suffix("").as_posix().replace("/", "."), p))
    return out


def find_cycles(graph: dict[str, set[str]]) -> list[str]:
    """Every cycle reachable in `graph`, rendered as "a -> b -> a".

    Iterative DFS with an explicit stack. The recursive form would be shorter, but the explicit path
    is what lets the cycle be PRINTED — "there is a cycle somewhere in 144 modules" is not a finding
    anyone can act on.
    """
    cycles: list[str] = []
    state: dict[str, int] = {}          # 1 = on the current path, 2 = finished
    for start in sorted(graph):
        if state.get(start):
            continue
        state[start] = 1
        path_list = [start]
        stack = [iter(sorted(graph[start]))]
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                stack.pop()
                state[path_list.pop()] = 2
                continue
            if state.get(nxt) == 1:
                cycles.append(" -> ".join(path_list[path_list.index(nxt):] + [nxt]))
            elif not state.get(nxt):
                state[nxt] = 1
                path_list.append(nxt)
                stack.append(iter(sorted(graph.get(nxt, ()))))
    return cycles


def main() -> None:
    mods = modules()
    check(len(mods) > 100, f"the package is present and parseable ({len(mods)} modules)")

    print("\n[R1] base and root layers import nothing upward")
    for mod, path in mods:
        head = mod.split(".")[0]
        if head not in BASE and mod not in ROOT_MODULES:
            continue
        up = [f"{mod}:{ln} -> {tgt}" for tgt, ln, _ in imports(path)
              if layer_of(tgt) in ("brain", "brain.tools", "edge")]
        check(not up, f"{mod} imports nothing from brain/edge", "; ".join(up))

    print("\n[R2] brain/tools reaches the laptop only lazily")
    eager = []
    for mod, path in mods:
        if layer_of(mod) != "brain.tools":
            continue
        eager += [f"{mod}:{ln} -> {tgt}" for tgt, ln, top in imports(path)
                  if layer_of(tgt) == "edge" and top]
    check(not eager, "no module-level edge import in any tool",
          f"{eager} — this fails the VPS at import, not at call")

    print("\n[R3] every edge<->brain crossing is a declared seam")
    undeclared = []
    for mod, path in mods:
        src = layer_of(mod)
        for tgt, ln, _ in imports(path):
            dst = layer_of(tgt)
            crossing = (src == "edge" and dst in ("brain", "brain.tools")) or \
                       (src in ("brain", "brain.tools") and dst == "edge")
            if not crossing:
                continue
            seam = f"{mod} -> {tgt}"
            if seam not in DECLARED_SEAMS:
                undeclared.append(f"{seam} ({mod.split('.')[0]}:{ln})")
    check(not undeclared, f"all crossings are among the {len(DECLARED_SEAMS)} declared",
          "\n        " + "\n        ".join(sorted(set(undeclared))))
    # And the reverse, so the list cannot rot into a licence for imports nobody makes any more.
    live = {f"{mod} -> {tgt}" for mod, path in mods for tgt, _, _ in imports(path)
            if (layer_of(mod) == "edge" and layer_of(tgt) in ("brain", "brain.tools"))
            or (layer_of(mod) in ("brain", "brain.tools") and layer_of(tgt) == "edge")}
    stale = sorted(DECLARED_SEAMS - live)
    check(not stale, "no declared seam has gone stale", f"delete these: {stale}")

    print("\n[R4] no module-level import cycle")
    # Prove the detector before trusting its silence: a green cycle check is indistinguishable from
    # a broken cycle check, and this one is the only non-trivial algorithm in the file.
    planted = find_cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set()})
    check(planted and "a -> b -> c -> a" in planted[0],
          "the detector finds a planted cycle", f"got {planted}")

    known = {m for m, _ in mods}
    graph = {mod: {t for t, _, top in imports(path) if top and t in known} for mod, path in mods}
    cycles = find_cycles(graph)
    check(not cycles, f"{len(graph)} modules, no module-level cycle",
          "\n        " + "\n        ".join(sorted(set(cycles))[:6]))

    recall_facade()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


# ── 30.F2: one recall facade, one connection owner ───────────────────────────────────────────
# The plan's words: "Seven stores, five holding their own SQLite handle, no unified entry point.
# A single recall() fans out and merges; callers stop touching stores directly."
#
# The layering test is where this belongs because both halves are structural: a fifth store
# opening its own connection, or a caller reaching past the facade into a store, are both things
# you can only catch by reading the whole tree — which is precisely what nobody does before
# adding the sixth.
def recall_facade() -> None:
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "afon"
    OWNER = "brain/dbconn.py"

    print("\n[30.F2] one module opens memory databases")
    offenders = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel == OWNER:
            continue
        # Comments and docstrings necessarily name the thing they forbid; parse instead of grep.
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"):
                offenders.append(f"{rel}:{node.lineno}")
    check(not offenders, f"only {OWNER} calls sqlite3.connect", str(offenders))

    stores = ["coaching", "graph", "presence", "semantic", "tasks"]
    routed = [s for s in stores
              if "from afon.brain.dbconn import connect"
              in (root / "brain" / f"{s}.py").read_text(encoding="utf-8")]
    check(len(routed) == len(stores),
          f"all {len(stores)} memory stores open through it ({', '.join(routed)})",
          f"not routed: {sorted(set(stores) - set(routed))}")

    print("\n[30.F2] one entry point for 'what do we know about X?'")
    facade = (root / "brain" / "recall.py").read_text(encoding="utf-8")
    check("the facade declares every layer it fans out to",
          all(f'"{lay}"' in facade for lay in ("L1", "L2", "L3", "L5", "L5b", "L4")),
          "a store that is not in the table is a store nobody remembers to ask")
    check("it carries a per-store timeout",
          "LAYER_TIMEOUT_S" in facade and "wait_for" in facade,
          "without one, the slowest layer decides the recall budget (S30: p95 <= 300ms)")

    # The load-bearing check: a caller must not fan out for itself. `fused_recall` is the L1/L2
    # store's own multi-layer method — reaching it from outside memory.py is exactly the "callers
    # touching stores directly" this floor removes, and it silently skips the tasks layer.
    users = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel in ("brain/memory.py", "brain/recall.py"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fused_recall"):
                users.append(f"{rel}:{node.lineno}")
    check(not users, "no caller outside the facade fans out over the stores itself", str(users))


if __name__ == "__main__":
    main()
