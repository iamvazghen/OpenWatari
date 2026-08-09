"""B6 — a compound request completes BOTH parts, on both response paths.

What this closes (TODO Workstream 0, the last open lever):

`clause_tools()` was written, tested and then called from NOWHERE. Multi-intent turns still
routed on whichever clause matched first, and the completion pass — the one extra turn granted
when only a single tool fired — appended a PROSE nudge ("now call the tool for the remaining
part") and hoped the model picked the right one. That is the same hope that fails everywhere
else in this file: the non-thinking primary narrates instead of calling. Combination scored
**51.5 against ~96** in every other category.

Now the completion pass asks the clause router which tool has not fired and forces THAT ONE by
name.

The second half of this file matters as much as the first: `respond()` and
`_respond_stream_impl()` are separate loops, so a fix landing in one and not the other produces
a defect that depends on how the turn arrived. These tests run the SAME request through both
and assert the same tools fire.

    uv run python bench/test_clause_completion.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.agent import AfonAgent, _completion_force  # noqa: E402
from afon.brain.intent_router import clause_tools  # noqa: E402

_ok = 0
_fail = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _fail += 1
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


class _F:
    def __init__(self, name):
        self.name = name
        self.arguments = "{}"


class _TC:
    _n = 0

    def __init__(self, name):
        _TC._n += 1
        self.id = f"call_{_TC._n}"
        self.function = _F(name)


class _M:
    def __init__(self, tool_calls=None, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _OneToolThenStopLLM:
    """Fires the FIRST tool it is offered, once, then answers in prose.

    This is a faithful model of the real failure: the primary satisfies one clause and then
    narrates the rest. Without B6 the turn ends with the second clause unhandled.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fired: list[str] = []

    async def complete(self, messages, tools=None, tool_choice="auto", skip_primary=False,
                       **kw):
        self.calls.append({"tools": [t["function"]["name"] for t in (tools or [])],
                           "tool_choice": tool_choice})
        return self._decide(tools)

    async def stream_with_tools(self, messages, tools=None, tool_choice="auto",
                                skip_primary=False, **kw):
        """The streaming path calls this instead of `complete`, yielding ("text"|"tools", x).

        Modelled separately because that difference is exactly how a fix lands on one response
        path and not the other — the defect this test exists to catch.
        """
        self.calls.append({"tools": [t["function"]["name"] for t in (tools or [])],
                           "tool_choice": tool_choice})
        msg = self._decide(tools)
        if msg.tool_calls:
            yield "tools", [{"id": tc.id, "name": tc.function.name,
                             "arguments": tc.function.arguments or "{}"} for tc in msg.tool_calls]
        else:
            yield "text", msg.content

    def _decide(self, tools):
        offered = [t["function"]["name"] for t in (tools or [])]
        # Fire once per distinct tool we are offered — so a NARROWED completion pass fires the
        # outstanding tool, while an un-narrowed one just talks (the pre-B6 behaviour).
        for name in offered:
            if name not in self.fired:
                # Only fire on the first pass, or when the surface has been narrowed to one
                # tool (i.e. something deliberately forced it).
                if len(self.calls) == 1 or len(offered) == 1:
                    self.fired.append(name)
                    return _M(tool_calls=[_TC(name)])
        return _M(content="Done.")


def _agent(llm):
    a = AfonAgent()
    a._llm = llm
    a._self_improve = False
    return a


# ── the router itself ──────────────────────────────────────────────────────
# A phrase BOTH of whose clauses route. Deliberately not the "what time is it and remember X"
# case from the benchmark: `forced_tools("what time is it")` returns [] — there is no time route
# — so clause_tools sees one routed clause, hits its `>= 2` guard and returns []. That scenario
# needs a router change, not a wiring change; see TODO B6.1.
combo = "check my email and remember to call mum"
plan = clause_tools(combo)
check(len(plan) >= 2, "the clause router sees BOTH parts of a compound request", str(plan))

# ── _completion_force picks the OUTSTANDING tool, not the one already run ──
if plan:
    already = {plan[0]}
    tools, forced = _completion_force(plan, already, [{"function": {"name": "irrelevant"}}])
    check(forced == plan[1],
          "the completion pass forces the tool that has NOT fired yet",
          f"already ran {plan[0]} -> forcing {forced}")
    check(len(tools) == 1 and tools[0]["function"]["name"] == forced,
          "...and narrows the surface to exactly that tool",
          str([t["function"]["name"] for t in tools]))

    tools2, forced2 = _completion_force(plan, set(plan), [{"function": {"name": "keep"}}])
    check(forced2 is None and tools2[0]["function"]["name"] == "keep",
          "nothing is forced once every clause has fired",
          "the turn must be allowed to end")

check(_completion_force([], {"x"}, [{"function": {"name": "keep"}}])[1] is None,
      "a single-intent turn is left entirely alone",
      "clause_plan is empty -> existing narrowing path unchanged")


# ── end to end, through the real agent, on BOTH response paths ────────────
async def _run() -> None:
    global _ok, _fail

    buffered_llm = _OneToolThenStopLLM()
    a = _agent(buffered_llm)
    await a.respond(combo)
    buffered_fired = list(buffered_llm.fired)
    # The assertion that matters is not "two tools fired" — an earlier version of this test
    # passed on that while the tool the request actually named never ran. Assert the PLAN.
    check(all(n in buffered_fired for n in plan),
          "buffered path: every tool the clause plan names actually fires",
          f"plan={plan} fired={buffered_fired}")

    stream_llm = _OneToolThenStopLLM()
    b = _agent(stream_llm)
    async for _ in b.respond_stream(combo):
        pass
    stream_fired = list(stream_llm.fired)
    check(all(n in stream_fired for n in plan),
          "streaming path: every tool the clause plan names actually fires",
          f"plan={plan} fired={stream_fired}")

    # The point of unifying the decision path: the two modes must not disagree.
    check(set(buffered_fired) == set(stream_fired),
          "both response paths fire the SAME tools for the same request",
          f"buffered={buffered_fired} stream={stream_fired}")

    # The completion pass must narrow — proving it forced by name rather than nudging in prose.
    narrowed_passes = [c for c in buffered_llm.calls[1:] if len(c["tools"]) == 1]
    check(bool(narrowed_passes),
          "the completion pass narrows to one tool instead of nudging in prose",
          f"{len(narrowed_passes)} narrowed pass(es)")

    # A single-intent turn must be untouched by any of this.
    plain = _OneToolThenStopLLM()
    c = _agent(plain)
    await c.respond("what time is it")
    check(len(plain.fired) <= 1,
          "a single-intent turn still fires exactly one tool",
          f"fired {plain.fired}")


asyncio.run(_run())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
