"""PC-op routing completeness and collisions (TODO J4.2, J4.3).

Two failure modes this closes, both VERIFIED present before it existed:

  J4.2  `pc_agent.py` merged LOCAL_HANDLERS from seven HARDCODED imports and nothing
        checked that list against reality. A new tool module exporting LOCAL_HANDLERS was
        silently unrouted until someone remembered to edit `pc_agent.py`, and the failure
        surfaced as "unknown PC op" at runtime on the owner's laptop rather than as a red
        test. Nothing in the repo could tell you it had happened.

  J4.3  The merge was `{**a, **b, ...}`. Two modules claiming one op name is
        last-import-wins: no error, no warning, no test. The op then runs a different
        module's code depending on import order.

The routing list is deliberately explicit — it encodes "these ops must run on the laptop",
not "these modules happen to export handlers". So this does not assert that every module is
routed; it asserts that every module is *accounted for*, either routed or listed below as
deliberately brain-side. Adding a module forces a one-line decision instead of allowing a
silent omission.

    uv run python bench/test_pc_agent_routing.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.edge.pc_agent import HANDLER_OWNERS, LOCAL_HANDLERS, _HANDLER_SOURCES  # noqa: E402

# Tool modules that export LOCAL_HANDLERS but are deliberately NOT routed to the laptop.
# Empty today. If you add a module here, say why in the comment — an unexplained entry is
# indistinguishable from the oversight this test exists to catch.
DELIBERATELY_BRAIN_SIDE: dict[str, str] = {}

TOOLS_DIR = ROOT / "src" / "afon" / "brain" / "tools"

results: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), label, detail))
    print(("PASS  " if ok else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))


def modules_exporting_handlers() -> set[str]:
    """Find tool modules that define a module-level LOCAL_HANDLERS.

    Parsed rather than imported: importing every tool module here would pull in cameras,
    browsers and audio devices, which is exactly what a test must not do.
    """
    found = set()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            if any(isinstance(t, ast.Name) and t.id == "LOCAL_HANDLERS" for t in targets):
                found.add(path.stem)
                break
    return found


def main() -> int:
    exporting = modules_exporting_handlers()
    routed = {name for name, _ in _HANDLER_SOURCES}

    check("some tool modules export LOCAL_HANDLERS", bool(exporting),
          f"{len(exporting)} found")

    # J4.2 — the completeness guard.
    unaccounted = exporting - routed - set(DELIBERATELY_BRAIN_SIDE)
    check("every module exporting LOCAL_HANDLERS is accounted for",
          not unaccounted,
          f"unrouted and unexplained: {sorted(unaccounted)}"
          if unaccounted else "routed or explicitly brain-side")

    # The reverse: pc_agent importing something that no longer exports handlers.
    phantom = routed - exporting
    check("every routed module still exports handlers", not phantom,
          f"routed but no LOCAL_HANDLERS: {sorted(phantom)}")

    stale = set(DELIBERATELY_BRAIN_SIDE) - exporting
    check("the brain-side exemption list has no stale entries", not stale,
          f"listed but no longer exports handlers: {sorted(stale)}")

    # J4.3 — collisions. The merge itself raises, so reaching this import at all proves
    # there are none today; this asserts the invariant explicitly so the reason is recorded.
    total = sum(len(h) for _, h in _HANDLER_SOURCES)
    check("no two modules claim the same op name",
          len(LOCAL_HANDLERS) == total,
          f"{len(LOCAL_HANDLERS)} merged vs {total} declared")

    check("every op has a recorded owner",
          set(HANDLER_OWNERS) == set(LOCAL_HANDLERS),
          f"{len(HANDLER_OWNERS)} owners for {len(LOCAL_HANDLERS)} ops")

    # And prove the guard actually fires, rather than trusting that it would.
    from afon.edge.pc_agent import _merge_handlers
    try:
        _merge_handlers((("mod_a", {"same_op": lambda: None}),
                         ("mod_b", {"same_op": lambda: None})))
        check("a duplicate op name is refused", False, "merge accepted a collision")
    except RuntimeError as exc:
        check("a duplicate op name is refused",
              "same_op" in str(exc) and "mod_a" in str(exc) and "mod_b" in str(exc),
              "the error names the op and both modules")

    passed = sum(1 for ok, _, _ in results if ok)
    for ok, label, detail in results:
        if not ok:
            print(f"  FAILED: {label}  [{detail}]")
    # House marker — run_all_tests.py greps for this exact string, so a suite that
    # prints its own format is reported as failing even when every check passed.
    print(f"=== {passed}/{len(results)} checks passed ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
