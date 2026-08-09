"""Code self-improvement (4.12) — Afon improves his OWN source, bounded and branch-only.

This is the controlled version of "the assistant edits itself": a bounded worker loop that, on a
FRESH branch, reads the relevant source, makes a minimal change, runs the test suite, and — only if
green — commits locally. It NEVER pushes. The push to GitHub stays a human decision; this loop hands
back a branch for the owner to review and approve.

Guard rails (why this is safe to have at all):
  * OFF by default (``settings.code_self_improve_enabled``). It edits real code, so it only runs when
    explicitly armed for a deploy. When off, calling it is a no-op that says so.
  * Branch-only. The orchestrator creates the branch itself before the model does anything, so edits
    never land on the working branch. ``git_push`` and ``create_github_issue`` are not even in the
    tool set handed to the model, and they aren't in the pre-authorised set — so a push attempt
    DEFERS (it can never happen autonomously).
  * Bounded. A hard step budget (``code_self_improve_max_steps``); then it must write a result.
  * Tests are the gate. The system prompt mandates run_tests before any commit, and revert on red.

ponytail: this reuses ``TaskWorker`` rather than growing a second agent loop. The only new machinery
is the branch bootstrap + a coding-only tool set + a pre-authorised write/commit allowlist.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from loguru import logger

from afon.config import settings

# Tools the loop may use (coding group minus the two that must never run autonomously).
_ALLOWED_TOOLS = {
    "read_source", "write_source", "list_source", "run_tests", "lint",
    "git_status", "git_diff", "git_log", "git_new_branch", "git_commit", "git_revert",
}
# Confirm-gated tools the ARMED loop is pre-authorised to run without deferring (local, reversible).
_PRE_AUTHORISED = {"write_source", "git_commit", "git_new_branch", "git_revert"}

_SYSTEM = (
    "You are Afon improving your OWN codebase, autonomously, on a dedicated branch that has ALREADY "
    "been created for you. Work in small, safe steps: (1) read the relevant source to understand it; "
    "(2) make the SMALLEST change that achieves the objective; (3) run_tests to verify — always test "
    "before you commit; (4) if the tests pass, git_commit with a clear message; if they FAIL, use "
    "git_revert or fix forward, never leave the branch broken. You must NOT push, open issues/PRs, or "
    "take any outward action — leave the committed branch for the owner to review and approve. When "
    "done (or blocked), STOP calling tools and write a concise report: what you changed, the test "
    "result, the branch name, and that it is awaiting the owner's approval to push. Be honest about "
    "anything you could not verify."
)


def _branch_name() -> str:
    return f"afon/selfimprove-{time.strftime('%Y%m%d-%H%M%S')}"


async def run_code_self_improve(
    objective: str,
    llm: Any,
    registry: dict[str, Callable[[dict], Awaitable[str]]],
    *,
    enabled: bool | None = None,
    max_steps: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Run one bounded, branch-only code self-improvement pass. Returns a spoken-style report."""
    from afon.brain.tools import group_tool_schemas
    from afon.brain.worker import TaskWorker

    objective = (objective or "").strip()
    if not objective:
        return "What would you like me to improve in my own code, sir?"

    on = settings.code_self_improve_enabled if enabled is None else enabled
    if not on:
        return ("Code self-improvement is switched off by default, sir — it edits my own source, so it "
                "only runs when you arm it (AFON_CODE_SELF_IMPROVE_ENABLED=true).")

    # Bootstrap the branch ourselves so no edit can ever touch the working branch.
    branch = _branch_name()
    make_branch = registry.get("git_new_branch")
    if make_branch is None:
        return "I can't self-improve without my coding tools available, sir."
    try:
        br = await make_branch({"name": branch})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"code self-improve: branch creation failed ({type(e).__name__})")
        return f"I couldn't create a working branch to improve safely, sir: {type(e).__name__}."
    if "new branch" not in br.lower():
        return f"I couldn't switch to a fresh branch, sir, so I stopped before editing: {br}"

    tools = [s for s in group_tool_schemas("coding") if s["function"]["name"] in _ALLOWED_TOOLS]
    steps = max_steps or settings.code_self_improve_max_steps
    worker = TaskWorker(llm, registry, tools, max_steps=steps, system=_SYSTEM,
                        allow_confirmed=_PRE_AUTHORISED)
    logger.info(f"code self-improve: armed run on '{branch}' ({steps} steps) — {objective[:80]}")
    result = await worker.run(objective, on_progress=on_progress)
    return f"On branch '{branch}': {result}"
