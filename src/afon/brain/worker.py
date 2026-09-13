"""TaskWorker — a bounded autonomous work loop (Phase 4.1).

`delegate_to_fleet` hands work to the OpenClaw team; this is Afon doing focused work HIMSELF: a
small, bounded tool loop for research, drafting, summarising, light coding, and verification, using
the tools he already has (web/scrape/vault/Notion/coding/utilities). It is deliberately NOT the live
conversation loop:

  * It runs to a step budget and then must produce a written result — it can't loop forever.
  * It is SAFE by construction: any confirm-gated / outward-facing / destructive tool (send_email,
    file delete, run_powershell, push, …) is NOT executed here. There is no human in the loop to say
    "yes" mid-background-task, so such a step is DEFERRED — recorded and handed back for the owner to
    approve. The worker still does all the safe research/draft work around it.
  * It holds no conversation history and no shared agent state, so it's safe to run in the background
    (via the TaskQueue) alongside live turns, and is hermetically testable with a fake LLM.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from afon.brain.proactive import confirm_required
from afon.brain.tools.base import bound_tool_result

_TAG_RE = re.compile(r"<\s*/?\s*(tool_call|function|tool_response|arg_key|arg_value)\s*>|<\|[^>]*\|>",
                     re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


_WORKER_SYSTEM = (
    "You are Afon working a task autonomously in the background for the owner. Work step by step "
    "using your tools: research with web/scrape/vault/Notion, draft and summarise, and for coding "
    "read source, make changes, and run tests to verify. Rules: (1) Do the SAFE work yourself. "
    "(1a) GROUND FACTS, NOT MEMORY: your training knowledge is stale, so before asserting anything "
    "time-sensitive (dates, releases, prices, availability, current events) VERIFY it with web_search "
    "across a couple of sources — never guess from memory. Open any link or note you're given and base "
    "your work on what it actually says, not on assumptions about the topic. "
    "(2) Do NOT take outward-facing or destructive actions (sending email/messages, deleting files, "
    "running shell, pushing code) — you have no way to get confirmation here; note any such step as "
    "something that needs the owner's approval and keep going. (3) When the task is done or you've gone "
    "as far as safely possible, STOP calling tools and write a concise result: what you found or "
    "produced, and a short 'Needs your approval:' list if any outward step remains. Be honest about "
    "what you could not verify."
)


class TaskWorker:
    """A bounded tool loop. Reuses the agent's LLM client + tool registry, but owns no shared state."""

    def __init__(
        self,
        llm: Any,
        registry: dict[str, Callable[[dict], Awaitable[str]]],
        tools: list[dict[str, Any]],
        max_steps: int = 6,
        max_seconds: float = 300.0,
        system: str | None = None,
        allow_confirmed: set[str] | None = None,
        origin: str = "",
        approvals: Any = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._tools = tools
        self._max_steps = max(1, max_steps)
        # 41.F3 — a step budget alone does not bound a research task: six steps of a slow scrape is
        # several minutes of silence. The wall clock is the budget the owner actually feels.
        # Floored, not clamped to a sane minimum: a caller that wants a two-second budget for a
        # test or a probe should get one. Zero or negative is the only value that makes no sense.
        self._max_seconds = max(0.01, max_seconds)
        #: Why the loop ended, so the RESULT can say it. Before this, a task that ran out of steps
        #: was handed back as a finished answer — the model was told to write its summary, and
        #: wrote one, and nothing anywhere said it was a truncated answer to a bigger question.
        self.stopped_early = ""
        self._system = system or _WORKER_SYSTEM   # override for specialised loops (e.g. code self-improve)
        # Phase 4.2 — where deferred outward steps go so the owner can actually approve/execute them
        # later (not just read about them). ``origin`` labels what produced them (e.g. an objective id).
        self._origin = origin
        self._approvals = approvals
        # Tools that are normally confirm-gated but PRE-AUTHORISED for this loop, so they run instead of
        # deferring. Used only by the opt-in code self-improve loop (local edits + local commits on a
        # branch, which the owner armed). Push/outward tools are NOT put here — they still defer.
        self._allow_confirmed = allow_confirmed or set()

    def _queue_approval(self, name: str, args: dict) -> None:
        """Record a deferred outward step as an APPROVABLE action. Fail-quiet: a queue problem must
        never break the task the worker is doing."""
        try:
            queue = self._approvals
            if queue is None:
                from afon.brain.approvals import APPROVALS

                queue = APPROVALS
            queue.enqueue(name, args, origin=self._origin)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"could not queue approval for {name}: {type(e).__name__}")

    async def run(self, objective: str, on_progress: Callable[[str], None] | None = None) -> str:
        objective = (objective or "").strip()
        if not objective:
            return "There was no task to work on, sir."
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": objective},
        ]
        deferred: list[str] = []
        last_text = ""
        self.stopped_early = ""
        started = time.monotonic()
        if on_progress:
            # Declared before the work, not after: a budget the owner hears only in the postmortem
            # is a excuse, not a budget.
            on_progress(f"budget: up to {self._max_steps} steps and "
                        f"{int(self._max_seconds / 60) or 1} minutes")
        for step in range(self._max_steps):
            spent = time.monotonic() - started
            if spent >= self._max_seconds:
                self.stopped_early = (f"I stopped after {int(spent)}s of the "
                                      f"{int(self._max_seconds)}s I gave myself")
                break
            msg = await self._llm.complete(messages, tools=self._tools, tool_choice="auto")
            last_text = _clean(getattr(msg, "content", "") or "")
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                break  # the worker produced its written result
            messages.append({
                "role": "assistant",
                "content": getattr(msg, "content", "") or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                    for tc in calls
                ],
            })
            used: list[str] = []
            for tc in calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if confirm_required(name, args) and name not in self._allow_confirmed:
                    note = f"{name}({', '.join(f'{k}={v}' for k, v in list(args.items())[:3])})"
                    deferred.append(note)
                    self._queue_approval(name, args)
                    result = ("DEFERRED: this is an outward/destructive action that needs the owner's "
                              "approval. It has been queued for him to approve — do not retry it; "
                              "continue the safe work.")
                else:
                    fn = self._registry.get(name)
                    if fn is None:
                        result = f"unknown tool {name}"
                    else:
                        try:
                            result = str(await fn(args))
                        except Exception as e:  # noqa: BLE001 — a tool error must not kill the task
                            logger.warning(f"work_on_task tool {name} failed: {type(e).__name__}")
                            result = f"tool {name} errored ({type(e).__name__}); work around it."
                    used.append(name)
                # Bounded before it enters the list (J1.4): this loop KEEPS the messages, so an
                # unbounded result is paid again on every later step of an unattended task.
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": bound_tool_result(result, name)})
            if on_progress and used:
                on_progress(f"step {step + 1}: used {', '.join(used)}")
        else:
            self.stopped_early = (f"I used all {self._max_steps} steps I gave myself")

        if self.stopped_early:
            # Budget exhausted without a natural stop — force a final written summary, no tools.
            messages.append({"role": "system", "content":
                             "Stop using tools now and write your concise result for the owner."})
            msg = await self._llm.complete(messages)
            last_text = _clean(getattr(msg, "content", "") or "")

        result = last_text or "I worked on it, sir, but couldn't produce a clear result."
        if self.stopped_early:
            # The honest half of a budget: an answer that ran out of room reads exactly like a
            # complete one unless it says otherwise, and the owner acts on it either way.
            result += (f" {self.stopped_early}, sir, so this is as far as I got — "
                       "say the word and I'll keep going.")
        if deferred:
            uniq = list(dict.fromkeys(deferred))
            result += " Needs your approval: " + "; ".join(uniq[:5]) + "."
        return result
