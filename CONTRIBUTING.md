# Contributing to OpenAfon

Thanks for your interest. OpenAfon is a from-scratch, local-first framework for building your own
voice-first AI companion (**Afon**). Contributions — bug reports, fixes, new tools, docs — are
welcome.

## Ground rules

- By contributing you agree your contribution is licensed under the project's [MIT License](LICENSE)
  and that you follow [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- **Naming:** the project is *OpenAfon*, the assistant is *Afon*, *Afon* is only the
  blueprint/inspiration. The Python package stays `afon` (code identifier). Keep user-facing
  strings as Afon.

## Dev setup

```bash
uv sync --extra edge --extra cloud-voice --extra brain --extra channels --extra identity --extra dev
uv run afon-setup          # writes a .env for local testing
```

## Before you open a PR

1. **Tests green:** `uv run python bench/run_all_tests.py` (the single gate — offline, no keys needed
   to pass; network/fleet tests SKIP when unreachable).
2. **Lint clean:** `uv run ruff check src`.
3. **Add a hermetic test** for any new capability — a `bench/test_*.py` that prints
   `=== N/N checks passed ===` and is registered in `bench/run_all_tests.py`'s `TESTS` list. Tests
   must be offline and deterministic (no real network/keys).
4. **Efficiency:** if you touch the hot path (prompt, tools, memory, streaming), run
   `uv run python bench/efficiency_report.py` and keep the targets met.
5. **Don't regress safety:** outward-facing/destructive tools belong in `proactive.CONFIRM_TIER`
   (enforced by `agent._execute_calls`); never add a git tool that rewrites history; never read or
   write secrets from a tool.

## Adding a tool

Drop a module in `src/afon/brain/tools/` exposing `SCHEMAS` (OpenAI function schemas) and
`HANDLERS` (`name -> async handler(args) -> str`), list it in `tools/__init__.py::_MODULES`, and have
it degrade gracefully when unconfigured (`tools/base.py::not_configured`). Put low-frequency tools in
a lazy group so the per-turn surface stays lean. See `skills/adding-a-tool.md`.

## Style

Match the surrounding code: terse comments that say *why*, not *what*; 100-col lines; type hints;
fail-quiet on best-effort paths. Keep the persona/knowledge in Markdown (`personality/`, `memory/`,
`skills/`) editable without code changes.

## Security issues

Report privately to the repository owner (not as a public issue) — see [SECURITY.md](SECURITY.md) §10.
