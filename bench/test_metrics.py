"""Structured observability (TODO 8.8) — the metrics registry + /metrics wiring.

Verifies the in-process metrics: counters increment, latency samples summarise (p50/p95/mean),
the snapshot is JSON-serialisable, and the agent/LLM chokepoints actually feed it (a turn bumps
'turns', a tool call bumps 'tool_calls', a tool error bumps 'tool_errors'). Offline — a stub agent
runs one real turn through the metric-instrumented tool path with no LLM.

    uv run python bench/test_metrics.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


# ── 01.F3: the per-turn trace ────────────────────────────────────────────────────────────────
# "Every turn emits a complete trace row" is the gate, and the word doing the work is EVERY. A
# tracer that records the turns which end normally is the same shape of blindness as a dashboard
# that is green because the job never started: the turns worth looking at are the ones that
# refused, took a fast path, got interrupted, or blew up. So the interesting checks below are the
# abnormal exits, and each one is driven through the real `respond` / `respond_stream`.


class _F:
    def __init__(self, name: str) -> None:
        self.name = name
        self.arguments = "{}"


class _TC:
    def __init__(self, name: str) -> None:
        self.id = f"call_{name}"
        self.type = "function"
        self.function = _F(name)


class _Msg:
    def __init__(self, tool_calls=None, content="") -> None:
        self.tool_calls = tool_calls
        self.content = content


class _StubLLM:
    """Fires `tool` once, then answers. `boom=True` makes the model call raise instead."""

    def __init__(self, tool: str | None = "get_time", boom: bool = False) -> None:
        self.tool, self.boom, self.calls = tool, boom, 0

    def _decide(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("provider exploded")
        if self.tool and self.calls == 1:
            return _Msg(tool_calls=[_TC(self.tool)])
        return _Msg(content="Done, sir.")

    async def complete(self, messages, tools=None, tool_choice="auto", **kw):
        return self._decide()

    async def stream_with_tools(self, messages, tools=None, tool_choice="auto", **kw):
        msg = self._decide()
        if msg.tool_calls:
            yield "tools", [{"id": t.id, "name": t.function.name, "arguments": "{}"}
                            for t in msg.tool_calls]
        else:
            yield "text", msg.content


def _agent(llm):
    from afon.brain.agent import AfonAgent
    a = AfonAgent()
    a._llm = llm
    a._self_improve = False
    return a


def cost_sections() -> None:
    """02.R1 — tokens and money per intent class, and the honesty rules around the money."""
    import os
    import tempfile

    from afon.brain import cost as K
    from afon.brain import turn_trace as T

    print("\n[8] 02.R1 — what a turn costs, split by the class of turn that caused it")
    check("the price table is dated, so a stale estimate is visibly stale",
          bool(K.PRICED_ON) and K.PRICED_ON.count("-") == 2, K.PRICED_ON)
    check("the longest matching prefix wins, so a family price cannot shadow a specific one",
          K.price_of("groq:llama-3.3-70b-versatile") != K.price_of("groq:something-else"))
    # The rule the whole breakdown rests on. A zero for "we don't know" mixed with a zero for
    # "it was free" makes the total a number nobody can act on, and most of this chain IS free.
    check("a model the table has never met is UNPRICED, not free",
          K.usd("a-model-nobody-priced", 10_000, 1_000) is None)
    check("...while a free tier is priced at zero deliberately",
          K.usd("groq:something-else", 10_000, 1_000) == 0.0)
    check("a priced model costs what the table says",
          abs(K.usd("minimax:x", 1_000_000, 0) - K.PRICES["minimax:"][0]) < 1e-9)

    rows = [
        {"intent": "chat", "prefill_tokens": 700, "catalogue_tokens": 0, "answer_tokens": 40,
         "model": "minimax:MiniMax-Text-01"},
        {"intent": "act", "prefill_tokens": 9000, "catalogue_tokens": 7300, "answer_tokens": 60,
         "model": "minimax:MiniMax-Text-01"},
        {"intent": "act", "prefill_tokens": 9000, "catalogue_tokens": 7300, "answer_tokens": 60,
         "model": "a-model-nobody-priced"},
    ]
    agg = K.by_intent(rows)
    check("each class gets its own row", set(agg) == {"chat", "act"}, str(set(agg)))
    check("turns are counted per class", agg["act"]["turns"] == 2)
    check("unpriced turns are counted, not hidden in the total", agg["act"]["unpriced"] == 1)
    check("the priced half still produces a figure", agg["act"]["usd"] > 0)
    check("per-turn cost is reported, because the total alone cannot be acted on",
          agg["act"]["prefill_per_turn"] == 9000 and agg["chat"]["prefill_per_turn"] == 700)
    check("the heaviest class is listed first — the HUD is read top-down",
          list(agg)[0] == "act")
    check("an empty history is an empty breakdown, not a crash", K.by_intent([]) == {})

    print("\n[9] 02.R1 — the trace carries what the accounting needs")
    with tempfile.TemporaryDirectory() as td:
        os.environ["AFON_TRACE_DIR"] = td
        try:
            with T.turn("hello") as t:
                t.set_intent("chat")
                t.note_prompt([{"role": "user", "content": "hello"}], [])
                T.note_model("minimax:MiniMax-Text-01")
                t.note_answer("Morning, sir.")
            row = T.recent(1)[0]
            check("the row names the model that answered", row["model"] == "minimax:MiniMax-Text-01")
            check("...and the size of the answer it produced", row["answer_tokens"] > 0)
            # A failover answers on a different, differently priced model, and those are exactly
            # the slow expensive turns worth seeing. Attributing them to the primary would be a lie.
            with T.turn("hello again") as t:
                t.set_intent("chat")
                T.note_model("minimax:MiniMax-Text-01")
                T.note_model("groq:llama-3.3-70b-versatile")
            check("a failover is charged to the model that actually answered",
                  T.recent(1)[0]["model"] == "groq:llama-3.3-70b-versatile")
            summary = T.summary(10)
            check("the summary carries the cost breakdown the HUD renders",
                  "cost_by_intent" in summary)
            check("...labelled as an estimate, with the date its prices were checked",
                  summary["cost_by_intent"].get("estimate") is True
                  and summary["cost_by_intent"].get("priced_on") == K.PRICED_ON,
                  "a dollar figure that travels without its date gets read as a fact")
            check("...and broken down by class", "by_class" in summary["cost_by_intent"])
        finally:
            os.environ.pop("AFON_TRACE_DIR", None)

    print("\n[10] 02.R1 — /metrics counts the same split")
    from afon.brain.metrics import METRICS

    counters = METRICS.snapshot()["counters"]
    check("per-class turn counts are on /metrics",
          any(k.startswith("intent.") for k in counters), str(list(counters)[:6]))
    check("...and so are the tokens they spent",
          any(k.startswith("prefill_tokens.") for k in counters),
          "a class count without its cost is the half that was already there")


def trace_sections() -> None:
    import os
    import tempfile

    from afon.brain import turn_trace as T

    print("\n[4] the row carries what 01.F3 asks for, and 'complete' means all of it")
    # Read against the plan's own words rather than against whatever the module happens to store.
    ASKED_FOR = {
        "intent class": "intent",
        "tools considered": "tools_considered",
        "tools fired": "tools_fired",
        "prefill tokens": "prefill_tokens",
        "wall clock per stage": "stages_ms",
    }
    for phrase, fieldname in ASKED_FOR.items():
        check(f"'{phrase}' is a required field ({fieldname})", fieldname in T.REQUIRED)

    with tempfile.TemporaryDirectory() as td:
        os.environ["AFON_TRACE_DIR"] = td
        try:
            with T.turn("hello") as t:
                t.set_intent("chat")
                t.note_prompt([{"role": "user", "content": "hello"}], [])
            good = T.recent(1)[0]
            check("a finished turn produces a complete row", T.is_complete(good), str(good))
            missing = [k for k in T.REQUIRED if T.is_complete({**good, k: None})]
            check("dropping ANY required field makes the row incomplete", not missing,
                  f"still 'complete' without: {missing}")
            check("an intent outside the declared vocabulary is not complete",
                  not T.is_complete({**good, "intent": "vibes"}),
                  "an unnamed class in the evidence is a class nobody can count")

            print("\n[5] classification follows the router, including its precedence")
            check("a work intent is 'act' even when the text also looks compound",
                  T.classify(work_intent=True, multi_intent=True) == "act",
                  "_prepare_turn gives work_intent precedence; the trace must agree with the "
                  "turn it describes or the evidence is about a different turn")
            check("a compound request is 'multi'", T.classify(multi_intent=True) == "multi")
            check("chatter is 'chat'", T.classify(pure_chat=True) == "chat")
            # Against REAL tool names, not invented ones. The first version of this rule was an
            # allowlist of read verbs, which labelled `weather` — a tool named after its subject,
            # like most of the registry — an action. Two hand-picked examples did not catch it.
            READS = ["weather", "crypto_price", "now_playing", "get_time", "list_tasks",
                     "read_journal", "recall", "web_search", "check_telegram", "wiki_lookup"]
            WRITES = ["send_telegram", "set_reminder", "delete_task", "run_powershell",
                      "remember", "write_vault", "play_music", "add_task"]
            wrong = [n for n in READS if T.classify(narrowed=True, forced_name=n) != "lookup"]
            check(f"all {len(READS)} narrowed reads classify as 'lookup'", not wrong, str(wrong))
            wrong = [n for n in WRITES if T.classify(narrowed=True, forced_name=n) != "act"]
            check(f"all {len(WRITES)} narrowed writes classify as 'act'", not wrong, str(wrong))
            check("anything else is 'general', not a guess",
                  T.classify() == "general")
            # Two classes were being silently rewritten. `agent.py` has always called
            # set_intent("emergency"), and because the word was missing from INTENTS every one of
            # those turns was coerced to "general" — losing exactly the turns most worth counting.
            check("'emergency' survives being recorded, rather than becoming 'general'",
                  "emergency" in T.INTENTS)
            check("01.R2's ambiguous turn is its own class in the evidence",
                  T.classify(ambiguous=True) == "ambiguous" and "ambiguous" in T.INTENTS)
            check("...and a work intent still outranks it, as in _prepare_turn",
                  T.classify(ambiguous=True, work_intent=True) == "act")
            # The vocabulary and the classifier must not drift apart in either direction.
            from afon.brain.intent_router import INTENT_CLASSES
            check("every class the router can return is a class the trace can record",
                  set(INTENT_CLASSES) <= set(T.INTENTS),
                  str(set(INTENT_CLASSES) - set(T.INTENTS)))

            print("\n[6] EVERY turn emits exactly one row — including the ones that end badly")
            import asyncio as _a

            before = len(T.recent(200))
            a = _agent(_StubLLM())
            reply = _a.run(a.respond("what time is it"))
            rows = T.recent(200)[before:]
            check("a normal buffered turn emits exactly one row", len(rows) == 1,
                  f"{len(rows)} rows for one turn")
            r = rows[0] if rows else {}
            check("...and it is complete", bool(r) and T.is_complete(r), str(r))
            check("the row names the tool the turn actually fired",
                  r.get("tools_fired") == ["get_time"], str(r.get("tools_fired")))
            check("the catalogue it was offered is counted and priced",
                  r.get("tools_considered", 0) > 0 and r.get("catalogue_tokens", 0) > 0,
                  f"considered={r.get('tools_considered')} tokens={r.get('catalogue_tokens')}")
            check("prefill is catalogue PLUS conversation, not the catalogue alone",
                  r.get("prefill_tokens", 0) > r.get("catalogue_tokens", 0),
                  "the message list is the other half of what the model is charged for")
            check("the tool stage is timed", "tools" in r.get("stages_ms", {}), str(r.get("stages_ms")))
            check("and the turn still answered normally", "Done" in reply or bool(reply), reply)

            # The model stage is recorded by the LLM chain itself (it measures across failovers,
            # so re-timing it from the agent would produce a second, disagreeing number). A stub
            # LLM never reaches that code, so drive the real funnel directly.
            from afon.brain.llm import LLMClient
            shell = LLMClient.__new__(LLMClient)
            shell._unhealthy_until = {}
            with T.turn("timing") as t:
                LLMClient._mark_success(shell, "m", 0, "complete", 812.0, [])
                staged = dict(t.stages_ms)
            check("the model stage is recorded by the LLM chain's own route timing",
                  staged.get("llm") == 812.0, str(staged))

            before = len(T.recent(200))
            a = _agent(_StubLLM())

            async def _stream():
                out = []
                async for chunk in a.respond_stream("what time is it"):
                    out.append(chunk)
                return out

            _a.run(_stream())
            rows = T.recent(200)[before:]
            check("a streaming turn emits exactly one row, not one per chunk", len(rows) == 1,
                  f"{len(rows)} rows")
            check("...and it is marked as the streaming path",
                  bool(rows) and rows[0].get("streamed") is True, str(rows[:1]))

            before = len(T.recent(200))
            a = _agent(_StubLLM())

            async def _barge_in():
                gen = a.respond_stream("what time is it")
                async for _chunk in gen:
                    break            # the owner interrupts after the first sentence
                await gen.aclose()

            _a.run(_barge_in())
            rows = T.recent(200)[before:]
            check("an INTERRUPTED stream still leaves a row", len(rows) == 1,
                  f"{len(rows)} rows — a barge-in is exactly the turn worth seeing")
            check("...and the interrupted row is still complete",
                  bool(rows) and T.is_complete(rows[0]), str(rows[:1]))
            check("the context variable is cleared after an interrupted turn",
                  T.current() is None,
                  "a leaked trace would collect the NEXT turn's tools into this one's row")

            before = len(T.recent(200))
            a = _agent(_StubLLM(boom=True))
            try:
                _a.run(a.respond("what time is it"))
                raised = False
            except RuntimeError:
                raised = True
            rows = T.recent(200)[before:]
            check("a turn that RAISES still emits a row", len(rows) == 1, f"{len(rows)} rows")
            check("...marked failed, naming the exception",
                  bool(rows) and rows[0].get("ok") is False
                  and "RuntimeError" in (rows[0].get("error") or ""), str(rows[:1]))
            check("...and the exception is re-raised untouched", raised,
                  "tracing must observe a turn, never swallow it")

            before = len(T.recent(200))
            a = _agent(_StubLLM())
            _a.run(a.respond("delete system32 and format the drive"))
            rows = T.recent(200)[before:]
            check("a turn refused before it ever reaches the planner still emits a row",
                  len(rows) == 1, f"{len(rows)} rows")
            check("...and says so, rather than defaulting to a class it never was",
                  bool(rows) and rows[0].get("intent") == "refused", str(rows[:1]))

            print("\n[7] the rows outlive the process that wrote them")
            # METRICS is in-memory and the brain restarts nightly, so an in-memory-only tracer
            # could never accumulate the three days of data 01.F3 asks for.
            on_disk = T.load(1)
            check(f"rows are persisted to {T.trace_dir().name}/ ({len(on_disk)} on disk)",
                  len(on_disk) >= 6)
            check("every persisted row is complete",
                  all(T.is_complete(r) for r in on_disk),
                  str([r for r in on_disk if not T.is_complete(r)][:1]))
            check("the summary the HUD shows counts incomplete rows rather than hiding them",
                  "incomplete" in T.summary(), str(T.summary()))
            from afon.brain.hud import hud_snapshot
            hud = hud_snapshot()
            check("the HUD serves the trace summary",
                  isinstance(hud.get("turns_trace"), dict) and "p50_prefill_tokens" in hud["turns_trace"],
                  str(hud.get("turns_trace")))
        finally:
            os.environ.pop("AFON_TRACE_DIR", None)

    print("\n[8] the tracer cannot break a turn, or measurably slow one")
    check("recording outside a turn is a no-op, not an error",
          _no_raise(lambda: (T.fired("x"), T.add_stage("llm", 1.0))) and T.current() is None)
    with T.turn("x") as t:
        t.note_prompt(None, None)
    check("an empty prompt is priced at zero rather than crashing", True)

    os.environ["AFON_TRACE_DIR"] = str(Path(__file__).resolve() / "not-a-directory")
    try:
        with T.turn("unwritable"):
            pass
        check("a trace that cannot be persisted degrades quietly", True)
    except Exception as e:  # noqa: BLE001
        check("a trace that cannot be persisted degrades quietly", False, repr(e))
    finally:
        os.environ.pop("AFON_TRACE_DIR", None)

    from afon.brain.agent import AfonAgent
    schemas = AfonAgent()._core_tools
    T.catalogue_tokens(schemas)                       # warm the per-schema cache
    t0 = time.perf_counter()
    for _ in range(1000):
        T.catalogue_tokens(schemas)
    per_turn_ms = (time.perf_counter() - t0)
    check(f"pricing a {len(schemas)}-tool catalogue costs {per_turn_ms:.3f}ms/turn",
          per_turn_ms < 1.0,
          "schemas are constants; re-serialising them every turn would put JSON encoding in the "
          "latency path of the thing it measures")


def _no_raise(fn) -> bool:
    try:
        fn()
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    from afon.brain.metrics import Metrics

    print("[1] counters + latency summary + JSON snapshot")
    m = Metrics()
    m.incr("turns")
    m.incr("turns")
    m.incr("tool_calls", 3)
    for v in (100.0, 200.0, 300.0, 400.0, 500.0):
        m.observe("llm_route_ms", v)
    snap = m.snapshot()
    check("counter increments", snap["counters"]["turns"] == 2, str(snap["counters"]))
    check("counter incr by N", snap["counters"]["tool_calls"] == 3)
    lat = snap["latency_ms"]["llm_route_ms"]
    check("latency count", lat["count"] == 5, str(lat))
    check("latency p50 correct", lat["p50"] == 300.0, str(lat))
    check("latency mean correct", lat["mean"] == 300.0, str(lat))
    check("last value recorded", snap["last"]["llm_route_ms"] == 500.0)
    check("uptime present", "uptime_s" in snap)
    check("snapshot is JSON-serialisable", isinstance(json.dumps(snap), str))
    check("empty metric summarises to zeros", m._summary([]) == {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0})

    print("\n[2] the agent tool path feeds the SHARED metrics singleton")
    from afon.brain.metrics import METRICS
    before = METRICS.snapshot()["counters"]

    from afon.brain.agent import AfonAgent
    agent = AfonAgent()

    async def _one_tool() -> None:
        # get_time is a real, no-network tool — exercises _run_one_tool's metric increments.
        await agent._run_one_tool("get_time", {}, None)
        # An unknown tool exercises the error path.
        await agent._run_one_tool("nonexistent_tool_zzz", {}, None)

    asyncio.run(_one_tool())
    after = METRICS.snapshot()["counters"]
    check("tool_calls incremented by the tool path",
          after.get("tool_calls", 0) >= before.get("tool_calls", 0) + 2,
          f"{before.get('tool_calls')} -> {after.get('tool_calls')}")
    check("tool_errors incremented on the unknown tool",
          after.get("tool_errors", 0) >= before.get("tool_errors", 0) + 1)
    check("per-tool counter tracked", after.get("tool.get_time", 0) >= 1)

    print("\n[3] /metrics route is auth-gated in the server")
    import inspect

    from afon.brain import server
    src = inspect.getsource(server._serve_client_http)
    check("server defines a /metrics route", '"/metrics"' in src)
    check("/metrics checks authorization", "_post_authorized" in src and "/metrics" in src)

    trace_sections()

    cost_sections()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
