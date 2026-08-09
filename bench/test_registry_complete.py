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

    passed = sum(1 for ok, _, _ in results if ok)
    for ok, label, detail in results:
        if not ok:
            print(f"  FAILED: {label}  [{detail}]")
    print(f"=== {passed}/{len(results)} checks passed ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
