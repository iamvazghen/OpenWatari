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

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
