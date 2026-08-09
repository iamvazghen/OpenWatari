# Debugging — finding and fixing what's wrong

When something misbehaves, Afon, diagnose before you change. Guessing wastes commits.

## Process
1. **Reproduce.** Get the exact failing case. For a tool, call it the way it failed. For a test, run
   just that file: the suite runs each `bench/test_phase*.py` as a subprocess, so run it directly.
2. **Read the error.** The traceback's last frame names the file + line. `read_source` it. `loguru`
   logs (the `WARNING`/`tool '<x>' failed:` lines) tell you which tool degraded and why.
3. **Check the audit trail.** Recent tool calls are in `audit/<date>.jsonl` (via `brain/audit.py`,
   `recent()`), with redacted args and clipped results — a fast way to see what you actually did.
4. **Form one hypothesis, make the smallest test of it,** then fix. Don't shotgun multiple changes.
5. **Verify the fix with `run_tests()`,** then commit. Add a regression test if a bug slipped past
   the suite — that's how the suite gets stronger.

## Common shapes in this codebase
- **A tool "isn't configured"** when you expect it to work → its credential/env isn't set; check
  `config.py` + `.env`. That's graceful degradation, not a bug.
- **`uv sync --extra X` removed something** → syncing prunes extras you didn't list. Re-sync the FULL
  set the suite needs (edge, cloud-voice, local-voice, brain, channels, browse, identity, dev).
- **A network tool 403/301s** → check the User-Agent / redirect handling in `tools/base.py::_client`
  (some providers, e.g. Wikipedia, demand a descriptive UA).
- **Latency feels high** → run `bench/efficiency_report.py`; the usual culprit is system-prompt size
  or the model choice, both in `docs/BENCHMARKS.md`.

## When you're stuck
Revert to the last green commit (`git_log` → `git_revert`), tell Vazghen exactly what you observed
and what you tried, and ask. A clean revert + a clear question beats a half-broken state.
