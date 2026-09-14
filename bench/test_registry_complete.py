"""Every test on disk is accounted for in the runner (TODO J6.2).

THE BUG THIS CLOSES, hit twice in one session before it existed:
`bench/run_all_tests.py:TESTS` is a hand-maintained list. Nothing asserted that every
`bench/test_*.py` on disk appears in it, so a test could be written, pass locally, and then
**silently never run again** — which is indistinguishable from a passing test, and worse than
having no test at all, because its presence implies coverage.

When this guard was first run there were **13 unregistered files**.

It does not demand that everything be registered. Some tests are deliberately out of the
hermetic gate because they need live credentials, real network, or a running VPS — running
those on every commit would make the gate flaky and slow, which is how gates get ignored.
So the rule is the same one used for PC-op routing (J4.2): every file must be **accounted
for** — registered, or exempted here with a stated reason. Adding a test forces a one-line
decision instead of allowing a silent omission.

    uv run python bench/test_registry_complete.py
"""
from __future__ import annotations

import re
from pathlib import Path

BENCH = Path(__file__).resolve().parent
RUNNER = BENCH / "run_all_tests.py"

# Deliberately NOT in the hermetic gate. Each needs something the gate cannot assume.
# An entry without a real reason is indistinguishable from the oversight this test exists
# to catch, so keep the reasons specific.
EXEMPT = {
    "test_brain_llm.py": "live — hits freellmapi for streaming/tool-calling/failover",
    "test_composio_accounts.py": "live — lists real connected Composio accounts",
    "test_composio_connect.py": "live — real MCP transport to a running server",
    "test_latency_budget.py": "live — asserts a WS round-trip budget against the real brain",
    "test_live_integrations.py": "live — green/red readout of every external service",
    "test_live_task_reminder_e2e.py": "live — Notion + a reminder announced through the edge",
    "test_model_tiers.py": "benchmark — compares TTFT across providers; costs tokens, not pass/fail",
    "test_voice_io.py": "live — real STT/TTS engines behind the phone voice path",
    # This file: it validates the registry, so registering it in the registry it validates
    # would be circular. It is run by the deploy preflight instead.
    "test_registry_complete.py": "self-referential — guards the registry itself",
}

results: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), label, detail))
    print(("PASS  " if ok else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))


def main() -> int:
    runner_src = RUNNER.read_text(encoding="utf-8")
    # Every "<name>.py" string literal in the runner counts as registered.
    registered = set(re.findall(r'"(test_[a-z0-9_]+\.py)"', runner_src))
    on_disk = {p.name for p in BENCH.glob("test_*.py")}

    check("the runner registers some tests", bool(registered), f"{len(registered)} entries")
    check("there are tests on disk", bool(on_disk), f"{len(on_disk)} files")

    unaccounted = sorted(on_disk - registered - set(EXEMPT))
    check("every test file is registered or explicitly exempted",
          not unaccounted,
          f"unregistered and unexplained: {unaccounted}" if unaccounted
          else "no silently-skipped tests")

    # A registry entry naming a file that no longer exists is the mirror failure: the runner
    # reports it, and a renamed-away test looks like it is still covering something.
    phantom = sorted(registered - on_disk)
    check("no registry entry names a missing file", not phantom, f"phantom: {phantom}")

    stale_exempt = sorted(set(EXEMPT) - on_disk)
    check("no stale exemptions", not stale_exempt, f"exempt but absent: {stale_exempt}")

    overlap = sorted(registered & set(EXEMPT) - {"test_registry_complete.py"})
    check("nothing is both registered and exempted", not overlap, f"both: {overlap}")

    # Being registered is not the same as being RUN. Four files defined pytest-style
    # `def test_*()` functions and were executed as scripts by the runner, so the module was
    # imported, no function was ever called, and exit 0 was recorded as a pass — a green tick
    # over zero assertions (TODO J6.5, logged as "passes silently"; it was worse than silent).
    # Parse rather than import: importing a bench file runs it.
    import ast  # noqa: PLC0415

    orphans: list[str] = []
    for path in sorted(BENCH.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        defined = {n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test")}
        if not defined:
            continue          # check()-style script, asserts at module level — fine
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for name in sorted(defined - called):
            orphans.append(f"{path.name}::{name}")
    check("every test function is actually invoked when the file is run",
          not orphans,
          f"defined but never called: {orphans}" if orphans else "no phantom passes")

    # --- 01.R4: the self-model comes from the registry and the config, never from prose --------
    # "What can you do / what can't you do" was answered from `personality/operating-rules.md`,
    # which names sixteen tools by hand. Every one is a promise that rots the moment a tool is
    # renamed or removed, and the failure is not cosmetic: a self-model assembled from prose lets
    # Afon claim a capability he does not have, and the owner finds out by asking for it.
    import sys as _sys

    _sys.path.insert(0, str(BENCH.parent / "src"))
    from afon.brain import selfmodel as _SM
    from afon.brain.context import build_system_prompt as _prompt
    from afon.brain.tools import tool_handlers as _handlers

    _known = set(_handlers())

    # (a) the prose may no longer name a tool that does not exist.
    _rules = (BENCH.parent / "personality/operating-rules.md").read_text(encoding="utf-8")
    _claimed = _SM.named_tools(_rules)
    _ghosts = sorted(n for n in _claimed if n not in _known)
    check("no tool named in operating-rules.md has been renamed or removed out from under it",
          not _ghosts, f"prose promises tools that do not exist: {_ghosts}")
    check("...and the prose does name real tools, so the check above is not vacuous",
          len(_claimed & _known) >= 5, f"{len(_claimed & _known)} recognised")

    # (b) the CANNOT half is generated from the live config.
    _ints = _SM.integrations()
    check("every integration that declares a dependency is discovered by introspection",
          len(_ints) >= 8, f"{len(_ints)} found")
    check("each one reports a real readiness, not an assumption",
          all(isinstance(i.ready, bool) for i in _ints))
    check("each one advertises only tools something actually handles",
          all(set(i.tools) <= _known for i in _ints),
          str([i.name for i in _ints if not set(i.tools) <= _known]))
    _dark = _SM.unavailable()
    _blk = _SM.prompt_block()
    check("the prompt block names every dark integration",
          all(i.name in _blk for i in _dark), f"dark={[i.name for i in _dark]}")
    check("...and names no working one, which would just restate the catalogue",
          all(i.name not in _blk for i in _ints if i.ready),
          "the per-turn cost 03.R5 exists to defend")
    check("...and does not carry the per-turn cost of the `needs` prose, which is for the owner",
          all(i.needs not in _blk for i in _dark) if _dark else True,
          "a per-turn cost that grows when someone writes a friendlier _NEEDS is unwatched")
    check("...while the SPOKEN form does say what each one needs, so the owner can act on it",
          all(i.needs in _SM.spoken() for i in _dark) if _dark else True)
    check("a fully-configured install produces no block at all",
          bool(_blk) == bool(_dark),
          "a block present on every turn stops being read")

    # (c) generated means generated: change the registry, and the answer changes.
    _before = _SM.prompt_block()
    _saved = _SM.integrations
    try:
        _SM.integrations = lambda: [_SM.Integration("planted", ("x",), False, "a planted key")]
        check("planting an unconfigured integration changes the generated block",
              "planted" in _SM.prompt_block() and "planted" not in _before,
              "if this passes with prose, the block was written rather than generated")
        check("...and the spoken form changes with it", "planted" in _SM.spoken())
    finally:
        _SM.integrations = _saved
    check("the block is restored once the plant is removed", _SM.prompt_block() == _before)

    # (d) it actually reaches the model.
    _sys_prompt = _prompt()
    check("the generated gaps reach the system prompt",
          (not _dark) or _blk in _sys_prompt,
          "generated and never sent is the same as not generated")

    passed = sum(1 for ok, _, _ in results if ok)
    for ok, label, detail in results:
        if not ok:
            print(f"  FAILED: {label}  [{detail}]")
    print(f"=== {passed}/{len(results)} checks passed ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
