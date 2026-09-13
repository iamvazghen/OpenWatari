"""Autonomous work_on_task — Phase 4.1 (hermetic, no network).

Verifies the bounded background worker: it uses safe tools, DEFERS outward/destructive tools (never
runs them without approval), produces a written result with a 'Needs your approval' note, respects
its step budget, and is wired into the agent as a background task that returns a task id immediately.

    uv run python bench/test_work_on_task.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _tool_msg(calls):
    tcs = [SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a)) for i, n, a in calls]
    return SimpleNamespace(content="", tool_calls=tcs)


def _text_msg(text):
    return SimpleNamespace(content=text, tool_calls=None)


class FakeLLM:
    def __init__(self, msgs):
        self._msgs = msgs
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        m = self._msgs[min(self.calls, len(self._msgs) - 1)]
        self.calls += 1
        return m

    async def warmup(self):
        pass


class StatelessLLM:
    """Always replies with one final text message (no tool calls) — so each fresh worker in a
    backlog pass produces a written result immediately. Lets us test the backlog ORCHESTRATION
    (fetch -> per-task worker -> comment) without scripting per-worker tool sequences."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        return _text_msg(self._reply)

    async def warmup(self):
        pass


async def test_worker_defers_outward_actions() -> None:
    print("[1] worker runs safe tools, DEFERS outward ones, writes a result (4.1)")
    from afon.brain.worker import TaskWorker

    ran = {"web_search": 0, "send_email": 0}

    async def web_search(_a):
        ran["web_search"] += 1
        return "found: rabbits eat hay and pellets"

    async def send_email(_a):
        ran["send_email"] += 1
        return "sent"

    registry = {"web_search": web_search, "send_email": send_email}
    # step 1: safe research; step 2: tries to email (must be DEFERRED); step 3: final write-up.
    llm = FakeLLM([
        _tool_msg([("c1", "web_search", '{"query": "rabbit diet"}')]),
        _tool_msg([("c2", "send_email", '{"to": "vet@x.com", "body": "diet plan"}')]),
        _text_msg("Rabbits eat hay and pellets; I drafted a note for the vet."),
    ])
    worker = TaskWorker(llm, registry, tools=[], max_steps=6)
    out = await worker.run("research rabbit diet and email the vet")
    check("safe research tool ran", ran["web_search"] == 1, str(ran))
    check("outward send_email was NOT executed", ran["send_email"] == 0, str(ran))
    check("result includes the written findings", "hay and pellets" in out, out)
    check("result flags the deferred action for approval", "Needs your approval" in out
          and "send_email" in out, out)


async def test_worker_respects_step_budget() -> None:
    print("\n[2] worker stops at its step budget and forces a written result (4.1)")
    from afon.brain.worker import TaskWorker

    calls = {"n": 0}

    async def loop_tool(_a):
        calls["n"] += 1
        return "still going"

    # The model keeps calling a tool forever; the budget must cut it off and the final no-tool pass
    # produces the result.
    llm = FakeLLM([
        _tool_msg([("c", "loop_tool", "{}")]),  # repeated until budget hits
        _tool_msg([("c", "loop_tool", "{}")]),
        _text_msg("Here is what I gathered, sir."),  # the forced final summary
    ])
    worker = TaskWorker(llm, {"loop_tool": loop_tool}, tools=[], max_steps=2)
    out = await worker.run("keep going")
    check("tool calls capped at the step budget", calls["n"] == 2, str(calls))
    check("a written result is still produced", out.startswith("Here is what I gathered, sir."), out)
    # 41.F3 — and it no longer reads like a COMPLETE result. A truncated answer and a finished one
    # look identical to the owner unless the difference is said out loud.
    check("...and it says the budget ran out", "all 2 steps" in out, out)


async def test_agent_backgrounds_the_work() -> None:
    print("\n[3] the agent's work_on_task tool backgrounds it and returns a task id (4.1)")
    from afon.brain.agent import AfonAgent
    from afon.brain.tasks import TASKS

    agent = AfonAgent()
    check("work_on_task is registered", "work_on_task" in agent._registry)
    check("work_on_task is advertised in the core tool surface",
          any(s["function"]["name"] == "work_on_task" for s in agent._core_tools))
    check("worker tools exclude self-recursion + blocking fleet",
          not any(s["function"]["name"] in {"work_on_task", "delegate_to_fleet"}
                  for s in agent._worker_tools()))

    out = await agent._tool_work_on_task({"task": "look into something"})
    check("returns immediately with a backgrounded task id", "BACKGROUNDED" in out and "task id" in out, out)
    check("a task is now tracked as active", any(t.title == "look into something" for t in TASKS.active()),
          str([t.title for t in TASKS.active()]))
    # let the background runner settle (no LLM configured -> it fails quietly, never crashes)
    await asyncio.sleep(0.05)


async def test_backlog_attempts_and_comments() -> None:
    print("\n[4] backlog pass: pulls tasks, worker attempts each, posts a Notion comment (3.1)")
    from afon.brain.backlog import attempt_backlog

    tasks = [{"id": "p1", "title": "draft Q3 report"},
             {"id": "p2", "title": "research suppliers"},
             {"id": "p3", "title": "overflow task"}]

    async def fetch(limit=5):
        return tasks[:limit]

    posted: list[tuple[str, str]] = []

    async def comment(page_id, text):
        posted.append((page_id, text))

    llm = StatelessLLM("I drafted the outline and noted next steps.")
    done = await attempt_backlog(llm, registry={}, worker_tools=[], max_tasks=2,
                                 fetch=fetch, comment=comment)
    check("only max_tasks attempted (capped)", len(done) == 2, str(done))
    check("each attempt produced a result", all(d["result"] for d in done), str(done))
    check("a comment was posted per task, in order",
          len(posted) == 2 and posted[0][0] == "p1" and posted[1][0] == "p2", str(posted))
    check("comment carries the worker result", "drafted the outline" in posted[0][1], str(posted))
    check("attempts flagged as commented", all(d["commented"] for d in done), str(done))


async def test_backlog_graceful_when_empty() -> None:
    print("\n[5] backlog pass is a clean no-op when there are no tasks (3.1)")
    from afon.brain.backlog import attempt_backlog

    async def fetch(limit=5):
        return []

    done = await attempt_backlog(StatelessLLM("x"), {}, [], fetch=fetch, comment=None)
    check("no tasks -> empty result, no crash", done == [], str(done))


async def main() -> None:
    await test_worker_defers_outward_actions()
    await test_worker_respects_step_budget()
    await test_agent_backgrounds_the_work()
    await test_backlog_attempts_and_comments()
    await test_backlog_graceful_when_empty()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
