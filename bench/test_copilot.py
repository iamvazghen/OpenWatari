"""Phase 3 companion — the task co-pilot (clarify -> plan -> confirm -> execute -> report).

Proves Afon can PLAN a task with the owner (a dry-run: steps + clarifying questions + who does it +
which steps need approval) and then EXECUTE it in the background via the bounded worker or the fleet,
linking progress back to the to-do — while the defer-outward invariant stays sacred and auto-pilot is
opt-in. Hermetic: fake LLMs, a temp SQLite task queue, no network, no real fleet.

    uv run python bench/test_copilot.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
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


class JsonLLM:
    """Returns a fixed content string (a plan JSON) once, then plain text for any later call."""

    def __init__(self, content: str, later: str = "worker done, sir.") -> None:
        self._content = content
        self._later = later
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.calls += 1
        text = self._content if self.calls == 1 else self._later
        return SimpleNamespace(content=text, tool_calls=None)

    async def warmup(self):
        pass


class TextLLM:
    """Always a plain-text final message (no tool calls) — a worker using it stops in one step."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        return SimpleNamespace(content=self._reply, tool_calls=None)

    async def warmup(self):
        pass


class BoomLLM:
    async def complete(self, *a, **k):
        raise RuntimeError("model down")

    async def warmup(self):
        pass


_GOOD_JSON = (
    '{"questions": [], "steps": ["Outline the sections", "Draft each section", "Proof-read"], '
    '"outward": ["Email the final report to the board"], "executor": "self", '
    '"summary": "Draft the Q3 report end to end."}')


async def test_build_plan() -> None:
    print("[1] build_plan: parse, executor clamp, fallback")
    from afon.brain.copilot import Plan, build_plan, execution_objective, plan_from_meta, plan_to_meta, spoken_plan

    plan = await build_plan("write the Q3 report", "for the board", JsonLLM(_GOOD_JSON), fleet_available=True)
    check("steps parsed", plan.steps == ["Outline the sections", "Draft each section", "Proof-read"], str(plan.steps))
    check("outward step captured", plan.outward == ["Email the final report to the board"], str(plan.outward))
    check("no clarifying questions -> not needs_clarification", not plan.needs_clarification)
    check("summary parsed", "Q3 report" in plan.summary, plan.summary)

    # executor clamp: a model that says "fleet" is downgraded to "self" when the fleet isn't available.
    fleet_json = _GOOD_JSON.replace('"executor": "self"', '"executor": "fleet"')
    p_yes = await build_plan("analyse the crypto market", "", JsonLLM(fleet_json), fleet_available=True)
    check("executor 'fleet' honoured when fleet available", p_yes.executor == "fleet", p_yes.executor)
    p_no = await build_plan("analyse the crypto market", "", JsonLLM(fleet_json), fleet_available=False)
    check("executor clamped to 'self' when fleet unavailable", p_no.executor == "self", p_no.executor)

    # robustness: junk around the JSON, and a model that raises -> minimal fallback, never throws.
    fenced = "Sure! ```json\n" + _GOOD_JSON + "\n``` hope that helps"
    p_fence = await build_plan("x", "", JsonLLM(fenced), fleet_available=False)
    check("JSON extracted from a fenced/prose reply", len(p_fence.steps) == 3, str(p_fence.steps))
    p_boom = await build_plan("tidy the garage", "", BoomLLM(), fleet_available=False)
    check("LLM failure -> non-empty fallback plan, no raise", p_boom.steps and p_boom.executor == "self", str(p_boom))

    print("\n[2] questions gate + serialisation + spoken renderings")
    q_json = '{"questions": ["Which quarter?", "What format?"], "steps": ["ask, then do"], "executor": "self", "summary": "s"}'
    pq = await build_plan("make the report", "", JsonLLM(q_json), fleet_available=False)
    check("clarifying questions -> needs_clarification", pq.needs_clarification and len(pq.questions) == 2, str(pq.questions))

    round_tripped = plan_from_meta(plan_to_meta(plan))
    check("plan survives meta round-trip", round_tripped.steps == plan.steps and round_tripped.outward == plan.outward)
    check("plan_from_meta on empty -> None", plan_from_meta({}) is None and plan_from_meta(None) is None)

    spoken = spoken_plan(plan, "Q3 report")
    check("spoken plan reads back steps + who + approval + asks to proceed",
          "1)" in spoken and "myself" in spoken and "need your go-ahead" in spoken and "proceed" in spoken.lower(), spoken)
    spoken_q = spoken_plan(pq, "the report")
    check("spoken plan with questions asks them first", "Which quarter" in spoken_q and "just proceed" in spoken_q, spoken_q)

    obj = execution_objective("Q3 report", "for the board", plan)
    check("execution objective carries the plan + defer-outward instruction",
          "Agreed plan:" in obj and "Do NOT perform these outward steps" in obj and "board" in obj, obj)


async def test_agent_plan_and_execute() -> None:
    print("\n[3] agent.plan_task stores a plan on the to-do; execute_task runs + links back")
    import afon.brain.tasks as tasks_mod
    from afon.brain.tasks import TaskQueue

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    fresh = TaskQueue(db_path=Path(tmp.name) / "tasks.sqlite")
    tasks_mod.TASKS = fresh  # agent methods resolve the module global at call time

    from afon.brain.agent import AfonAgent
    from afon.brain.copilot import plan_from_meta

    agent = AfonAgent()
    check("plan_task + execute_task registered", "plan_task" in agent._registry and "execute_task" in agent._registry)
    check("both advertised in the core tool surface",
          {"plan_task", "execute_task"} <= {s["function"]["name"] for s in agent._core_tools})

    todo = fresh.add_todo("write the Q3 report", description="for the board")
    agent._llm = JsonLLM(_GOOD_JSON)
    out = await agent._tool_plan_task({"topic": "Q3"})
    check("plan_task reads the plan back to the owner", "plan for" in out.lower() and "1)" in out, out)
    stored = plan_from_meta(fresh.get(todo.id).meta.get("plan"))
    check("the plan was persisted on the to-do", stored is not None and len(stored.steps) == 3, str(stored))

    # Execute it: reuse the stored plan (self executor) -> a background job that links back to the to-do.
    agent._llm = TextLLM("I outlined the report and drafted the intro.")
    out = await agent._tool_execute_task({"topic": "Q3"})
    check("execute_task backgrounds the work with a task id", "BACKGROUNDED" in out and "task id" in out, out)
    check("does it 'myself' (self executor)", "myself" in out, out)
    check("an execute job is now active", any(t.title.startswith("execute:") for t in fresh.active()),
          str([t.title for t in fresh.active()]))
    check("the to-do links to the exec job", fresh.get(todo.id).meta.get("exec_job"), str(fresh.get(todo.id).meta))

    await asyncio.sleep(0.05)  # let the background worker settle
    check("outcome linked back onto the to-do", "drafted the intro" in (fresh.get(todo.id).last_progress or ""),
          fresh.get(todo.id).last_progress)

    print("\n[4] execute_task asks first when the task is underspecified (no auto-pilot)")
    from afon.brain.copilot import Plan, plan_to_meta

    vague = fresh.add_todo("plan the offsite")
    vague.meta["plan"] = plan_to_meta(Plan(steps=["ask, then book"], questions=["Where and when?"],
                                           executor="self", summary="s"))
    fresh.edit_todo(vague.id)
    before = len([t for t in fresh.active() if t.title.startswith("execute:")])
    out = await agent._tool_execute_task({"topic": "offsite"})
    check("underspecified + no go-ahead -> asks a question, doesn't run", "Where and when" in out, out)
    after = len([t for t in fresh.active() if t.title.startswith("execute:")])
    check("no background job was started", after == before, f"{before}->{after}")
    # ...but 'just proceed' (proceed_anyway) overrides and runs it.
    out = await agent._tool_execute_task({"topic": "offsite", "proceed_anyway": True})
    check("proceed_anyway overrides the question gate and runs it", "BACKGROUNDED" in out, out)
    await asyncio.sleep(0.05)

    print("\n[5] a 'fleet' plan downgrades to self (+notes it) when the fleet isn't authorized")
    fleet_todo = fresh.add_todo("deep crypto due-diligence")
    fleet_todo.meta["plan"] = plan_to_meta(Plan(steps=["research on-chain flows"], executor="fleet",
                                                summary="due diligence"))
    fresh.edit_todo(fleet_todo.id)
    agent.fleet_authorized = False
    agent._llm = TextLLM("Gathered what I safely could.")
    out = await agent._tool_execute_task({"topic": "crypto"})
    check("fleet plan + unauthorised -> downgraded to self, and says so",
          "BACKGROUNDED" in out and "isn't authorized" in out and "myself" in out, out)
    await asyncio.sleep(0.05)

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass


async def main() -> None:
    await test_build_plan()
    await test_agent_plan_and_execute()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
