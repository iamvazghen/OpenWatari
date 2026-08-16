"""Regression checks for bench/run_all_tests.py result classification.

Run:
    uv run python bench/test_run_all_tests_classifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.run_all_tests import classify_result  # noqa: E402

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
    print("[1] regular pass/fail")
    check(
        "exit 0 + required output -> PASS",
        classify_result("offline", 0, "all checks passed", ["checks passed"]) == "PASS",
    )
    check(
        "missing required output -> FAIL",
        classify_result("offline", 0, "partial output", ["checks passed"]) == "FAIL",
    )

    print("\n[2] network skip only for unavailable dependency")
    check(
        "proxy connection refused -> SKIP",
        classify_result(
            "network",
            1,
            "httpx.ConnectError: [WinError 10061] No connection could be made",
            ["fleet sentinel not leaked"],
        )
        == "SKIP",
    )
    check(
        "name resolution failure -> SKIP",
        classify_result(
            "network",
            1,
            "getaddrinfo failed / NameResolutionError",
            ["fleet sentinel not leaked"],
        )
        == "SKIP",
    )

    print("\n[3] runnable model/tool failures stay red")
    check(
        "tool-call validation failure -> FAIL",
        classify_result(
            "network",
            1,
            "APIConnectionError earlier\nError code: 400 - {'error': {'message': "
            "'tool call validation failed', 'type': 'invalid_request_error'}}",
            ["fleet sentinel not leaked"],
        )
        == "FAIL",
    )
    check(
        "provider rate limit -> FAIL",
        classify_result(
            "network",
            1,
            "Error code: 429 - {'error': {'code': 'rate_limit_exceeded'}}",
            ["fleet sentinel not leaked"],
        )
        == "FAIL",
    )

    print("\n[4] whole failover chain exhausted to a connectivity failure -> SKIP (no LLM in this env)")
    check(
        "all models failed + Connection error -> SKIP even with a stray provider 400",
        classify_result(
            "network",
            1,
            "LLM marked 'groq:llama' unhealthy (BadRequestError)\n"
            "RuntimeError: all LLM models failed; last error: Connection error.",
            ["fleet sentinel not leaked"],
        )
        == "SKIP",
    )
    check(
        "but a tool-validation 400 (no chain-exhaustion marker) stays FAIL",
        classify_result(
            "network",
            1,
            "Connection error earlier from one provider\n"
            "Error code: 400 - {'error': {'message': 'tool call validation failed'}}",
            ["fleet sentinel not leaked"],
        )
        == "FAIL",
    )

    print("\n[5] a [network] test that TIMES OUT is a slow/absent LLM dependency -> SKIP")
    check(
        "network + timeout (code 124) -> SKIP",
        classify_result("network", 124, "TIMEOUT after 180s", ["checks passed"]) == "SKIP",
    )
    check(
        "but an OFFLINE test that times out is a real hang -> FAIL",
        classify_result("offline", 124, "TIMEOUT after 180s", ["checks passed"]) == "FAIL",
    )

    print("\n[6] the other half of the contract: a test that FAILS must exit non-zero (J3.1/J6.4)")
    # The runner passes a test on `exit == 0 AND every needle present`. The needle here is
    # "checks passed ===", which counter-style files print WHETHER OR NOT they failed — so on those,
    # the exit code is the only thing carrying the verdict. 112 of these files define their own
    # `check()`; that duplication is not itself a defect (each file must run standalone as a
    # subprocess, which is what makes a shared harness import the wrong shape here), but it means
    # the contract is re-implemented 112 times and one omission is invisible: failing checks would
    # be reported to the runner as a green tick, the same shape as J6.5's orphan test functions.
    #
    # Two sound styles exist and both are allowed: count-and-exit, or assert-based with a literal
    # summary printed only after the asserts (a failed assert exits non-zero by itself). What is not
    # allowed is counting failures and then exiting 0 regardless.
    import re as _re

    bench_dir = Path(__file__).resolve().parent
    runner_src = (bench_dir / "run_all_tests.py").read_text(encoding="utf-8")
    registered = set(_re.findall(r'"(test_[a-z0-9_]+\.py)"', runner_src))
    silent = []
    for name in sorted(registered):
        path = bench_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "checks passed ===" not in text:
            continue
        counts_failures = bool(_re.search(r"^\s*failed\s*\+=|^\s*FAIL\s*\+=", text, _re.MULTILINE))
        exits_on_failure = bool(_re.search(r"sys\.exit\(|SystemExit", text))
        if counts_failures and not exits_on_failure:
            silent.append(name)
    check(f"all {len(registered)} registered tests report failure through the exit code",
          not silent, f"count failures but always exit 0: {silent}")

    print("\n[7] every registered test exercises shipped code, or is a declared structural gate (J6.3)")
    # J6.3 asked whether the tiny, highly-cohesive test communities are hermetic *by design* or
    # hermetic *by accident* — a test that asserts against its own stub is a green tick over zero
    # shipped code. Audited: none of them are. Eleven never import the package, and every one of
    # those is a STRUCTURAL gate whose subject genuinely is text (documents, layering, shell
    # scripts, the registry itself); two more looked isolated only because they import
    # `deploy/uptime_watch.py`, which the graph does not treat as part of the codebase.
    #
    # What keeps that true: a registered test must import something the repo ships, or be named
    # here with a reason. Adding a test then forces a one-line decision instead of allowing a
    # stub-only test to arrive unnoticed.
    STRUCTURAL = {
        "check_public_clean.py": "scans the tree for secrets — its subject IS the files",
        "test_doc_paths.py": "asserts documents cite real paths",
        "test_systems_plan.py": "asserts the plan's own structure",
        "test_layering.py": "parses imports; importing the layers would defeat it",
        "test_ops_scripts.py": "parses shell scripts",
        "test_deploy_prune.py": "runs the deploy's shell guards; its subject is bash, not Python",
        "test_cohesion_baseline.py": "asserts the code graph's own metric; its subject is graph.json",
        "test_graph_prune.py": "asserts the code graph's edges; its subject is graph.json",
        "test_unpark_gate.py": "drives scripts/unpark_check.py and parses preflight.sh; its subject "
                               "is the release checklist, not the assistant",
        "test_static_correctness.py": "AST checks over the source",
        "test_client_endpoints.py": "compares HTML clients against the server's routes as text",
        "test_enroll_script_parse.py": "parses the markdown enrolment script",
        "test_run_all_tests_classifier.py": "this file — asserts the runner's contract",
    }
    import ast as _ast

    shipped_roots = (bench_dir.parent / "src", bench_dir.parent / "deploy")
    stub_only = []
    for name in sorted(registered):
        path = bench_dir / name
        if not path.exists() or name in STRUCTURAL:
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        touches = False
        for node in _ast.walk(tree):
            mods = []
            if isinstance(node, _ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            for mod in mods:
                head = mod.split(".")[0]
                if head in ("afon", "deploy") or any((r / f"{head}.py").exists() for r in shipped_roots):
                    touches = True
        if not touches:
            stub_only.append(name)
    check(f"all {len(registered)} registered tests touch shipped code or are declared structural",
          not stub_only, f"assert against nothing the repo ships: {stub_only}")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
