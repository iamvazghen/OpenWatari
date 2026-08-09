# Python conventions for this codebase

Match what's already here, Afon — consistency matters more than personal taste.

## Style
- Python 3.11, `from __future__ import annotations` at the top of every module.
- Type hints on function signatures. Prefer `str | None` over `Optional[str]`.
- Ruff is the linter, line length **100**. Run `lint()` before committing. Rule `E741`
  (ambiguous names like `l`) is active — use `lbl`, not `l`.
- `loguru`'s `logger` for logging, not `print` (except in `bench/` test scripts, which print).
- Module docstring at the top explaining *why* the module exists, not just what. Comments explain
  intent and non-obvious decisions, not the obvious.

## Async
- The brain is async. Tool handlers are `async def handler(args: dict) -> str`.
- HTTP via the helpers in `tools/base.py` (`http_get`/`http_post`) — they set timeouts, follow
  redirects, and send a User-Agent. Don't hand-roll `httpx` clients in tools.
- Subprocesses: `asyncio.create_subprocess_exec` with a timeout (see `tools/coding.py::_run`).

## The graceful-degradation rule (non-negotiable)
A tool must NEVER crash the brain. If a dependency/credential is missing, return
`not_configured(what, needs)`. On error, `return tool_error(what, e)`. Always return a `str` the
model can speak. This is why the whole tool set can be registered safely even when half of it is
unconfigured.

## Settings
- Every configurable value goes in `config.py` as a field with a sensible default and a comment.
- Read it via `from afon.config import settings`. Never read `os.environ` directly in tools.
- New secrets: add the field, add a commented block to `.env.example`, never hard-code.

## Tests
- Hermetic and offline by default. Use temp dirs (`tempfile.mkdtemp`), inject fakes (stub embedders,
  fake clocks, fake websockets) rather than hitting the network.
- The house pattern is a `check(name, ok, detail)` helper that counts pass/fail and prints
  `=== N/N checks passed ===`, exiting non-zero on failure. Copy an existing `bench/test_phase*.py`.
- Register every new test in `bench/run_all_tests.py` so the one-command gate covers it.
