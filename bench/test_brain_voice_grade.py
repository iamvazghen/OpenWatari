"""Voice-grade brain behaviour — Phase 2.3 / 2.4 / 2.5 (hermetic, no network).

Verifies the latency/correctness work that makes the brain voice-grade:

  * 2.3 single-tool short-circuit — a turn that calls exactly ONE speakable read-only tool speaks
    the tool result directly and SKIPS the second "summarise" LLM pass (one round trip saved).
  * 2.4 force tools for data-read intents — weather/price/email/calendar/tasks/web/vault questions
    are recognised as data reads (so the first pass forces a tool call, not a fabricated claim),
    while opinion/chat/arithmetic still answer freely.
  * 2.5 parallel independent tool calls — several tool calls in one model turn run concurrently
    (finish in max-latency, not the sum), while results stay in call order and confirm-gated tools
    are never run in this fast path.

    uv run python bench/test_brain_voice_grade.py
"""

from __future__ import annotations

import asyncio
import sys
import time
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


def _tool_msg(calls: list[tuple[str, str, str]]):
    """Build a fake assistant message that calls tools. calls = [(id, name, json_args), …]."""
    tcs = [SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a)) for i, n, a in calls]
    return SimpleNamespace(content="", tool_calls=tcs)


def _text_msg(text: str):
    return SimpleNamespace(content=text, tool_calls=None)


class FakeCompleteLLM:
    """Scripted ``complete``: returns the next queued message and counts calls."""

    def __init__(self, msgs: list) -> None:
        self._msgs = msgs
        self.complete_calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        m = self._msgs[self.complete_calls]
        self.complete_calls += 1
        return m

    async def warmup(self) -> None:
        pass


async def test_single_tool_short_circuit() -> None:
    print("[1] single speakable tool short-circuits the summary pass (2.3)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()

    async def fake_weather(_args: dict) -> str:
        return "In Paris, France it's 18°C (feels like 17°C), wind 9 km/h, sir."

    agent._registry["weather"] = fake_weather
    # Second message would be a summary pass — it must NOT be consumed.
    agent._llm = FakeCompleteLLM([
        _tool_msg([("c1", "weather", '{"location": "Paris"}')]),
        _text_msg("SHOULD-NOT-BE-USED"),
    ])
    reply = await agent.respond("what's the weather in Paris")
    check("only ONE LLM round trip (no summary pass)", agent._llm.complete_calls == 1,
          f"complete_calls={agent._llm.complete_calls}")
    check("the tool result is spoken verbatim", reply.startswith("In Paris"), repr(reply))
    check("reply persisted to history", agent._history[-1]["content"] == reply)


async def test_multi_tool_keeps_summary() -> None:
    print("\n[2] a multi-tool turn still summarises (no short-circuit) (2.3)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()

    async def fake_weather(_args: dict) -> str:
        return "In Paris it's 18°C, sir."

    async def fake_time(_args: dict) -> str:
        return "Tuesday, 24 June 2026, 14:00 (Europe/Berlin)"

    agent._registry["weather"] = fake_weather
    agent._registry["get_time"] = fake_time
    agent._llm = FakeCompleteLLM([
        _tool_msg([("c1", "weather", '{"location": "Paris"}'),
                   ("c2", "get_time", "{}")]),
        _text_msg("It's 2 PM and 18°C in Paris, sir."),
    ])
    reply = await agent.respond("what's the time and the weather in Paris")
    check("two LLM round trips (tools + summary)", agent._llm.complete_calls == 2,
          f"complete_calls={agent._llm.complete_calls}")
    check("the summarised reply is spoken", reply == "It's 2 PM and 18°C in Paris, sir.", repr(reply))


async def test_read_intents_force_tools() -> None:
    print("\n[3] data-read intents are recognised; chat/math are not (2.4)")
    from jarvis.brain.agent import _wants_forced_tool

    should_force = [
        "what's the weather in Berlin",
        "what's the price of bitcoin",
        "how much is ETH worth",
        "what's the exchange rate from USD to EUR",
        "do I have any unread email",
        "check my telegram",
        "what's on my calendar today",
        "what's due today",
        "any overdue tasks",
        "search my vault for the rabbit farm",
        "what are the latest headlines",
        # A bare "news" is still a data read. "any tech news" used to force nothing, so the model
        # answered a right-now question out of its own training data.
        "any tech news",
        "what's the news in Armenia",
    ]
    should_not = [
        "how are you today",
        "tell me a joke",
        "what do you think about Armenia",
        "what's two plus two",
        "thanks, that's all",
        "good morning",
        # "news" as ordinary speech, not a request for the news.
        "that's good news",
        "no news is good news",
    ]
    miss = [t for t in should_force if not _wants_forced_tool(t)]
    over = [t for t in should_not if _wants_forced_tool(t)]
    check("all data-read intents force a tool", not miss, f"missed: {miss}")
    check("chat/opinion/math do NOT force a tool", not over, f"over-forced: {over}")


async def test_parallel_independent_tools() -> None:
    print("\n[4] independent tool calls run concurrently, in order (2.5)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()
    order: list[str] = []

    span: dict[str, float] = {}

    async def slow_a(_args: dict) -> str:
        span["a_start"] = time.perf_counter()
        await asyncio.sleep(0.25)
        span["a_end"] = time.perf_counter()
        order.append("a")
        return "alpha"

    async def slow_b(_args: dict) -> str:
        span["b_start"] = time.perf_counter()
        await asyncio.sleep(0.25)
        span["b_end"] = time.perf_counter()
        order.append("b")
        return "beta"

    agent._registry["tool_a"] = slow_a
    agent._registry["tool_b"] = slow_b
    messages: list = []
    calls = [
        {"id": "c1", "name": "tool_a", "arguments": "{}"},
        {"id": "c2", "name": "tool_b", "arguments": "{}"},
    ]
    t0 = time.perf_counter()
    outcomes = await agent._execute_calls(messages, calls)
    dt = time.perf_counter() - t0
    # Assert the OVERLAP, not the stopwatch. "dt < 0.4" was measuring the machine's mood: two 0.25s
    # sleeps land at 0.29s each under Windows' ~16ms timer granularity, _execute_calls adds ~0.15s of
    # its own book-keeping, and the total sat right on the 0.4s line — so a busy box failed a build
    # while the tools were provably running side by side (both started and finished the same
    # instant). Whether B began before A finished is the actual property, and it can't be bribed by
    # load. Elapsed is still reported so a real slowdown remains visible.
    overlapped = span.get("b_start", 0) < span.get("a_end", 0)
    check("both tools ran concurrently (B starts before A ends)", overlapped,
          f"a={span.get('a_start', 0):.3f}->{span.get('a_end', 0):.3f} "
          f"b={span.get('b_start', 0):.3f}->{span.get('b_end', 0):.3f} elapsed={dt:.2f}s")
    check("outcomes preserve call order", [o["name"] for o in outcomes] == ["tool_a", "tool_b"],
          repr(outcomes))
    check("tool result messages match call ids in order",
          [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["c1", "c2"])


async def test_confirm_gated_not_run_in_parallel() -> None:
    print("\n[5] a confirm-gated tool is held (never run) even alongside a safe one (2.5)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()
    agent._confirm_granted = False
    ran = {"send_email": 0, "get_time": 0}

    async def fake_send_email(_args: dict) -> str:
        ran["send_email"] += 1
        return "sent"

    async def fake_time(_args: dict) -> str:
        ran["get_time"] += 1
        return "14:00"

    agent._registry["send_email"] = fake_send_email
    agent._registry["get_time"] = fake_time
    messages: list = []
    calls = [
        {"id": "c1", "name": "send_email", "arguments": '{"to": "x@y.com", "body": "hi"}'},
        {"id": "c2", "name": "get_time", "arguments": "{}"},
    ]
    await agent._execute_calls(messages, calls)
    check("the confirm-gated send_email did NOT run", ran["send_email"] == 0, str(ran))
    check("the safe get_time still ran", ran["get_time"] == 1, str(ran))
    check("send_email was held pending confirmation", agent._pending_confirm is not None
          and agent._pending_confirm["name"] == "send_email")


async def test_multi_intent_detection() -> None:
    print("\n[6] multi-intent connectors are detected; single intents are not (4.2)")
    from jarvis.brain.agent import _is_multi_intent

    multi = [
        "look up the capital of Japan and remember it",
        "what's the time, and also remember I prefer tea",
        "search for the news then summarise it",
        "set a reminder for 5pm and send John a message",
    ]
    single = [
        "what's the weather in Paris",
        "I'd like fish and chips for dinner",
        "keep it nice and quiet",
        "who are you",
    ]
    miss = [t for t in multi if not _is_multi_intent(t)]
    over = [t for t in single if _is_multi_intent(t)]
    check("compound action requests are detected", not miss, f"missed: {miss}")
    check("single intents / non-action 'and' are not", not over, f"over-detected: {over}")


async def test_multi_intent_no_short_circuit() -> None:
    print("\n[7] a multi-intent turn does BOTH parts (short-circuit suppressed) (4.2)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()
    ran = {"get_time": 0, "remember": 0}

    async def fake_time(_a):
        ran["get_time"] += 1
        return "Tuesday, 24 June 2026, 14:00 (Europe/Berlin)"

    async def fake_remember(_a):
        ran["remember"] += 1
        return "Noted, sir."

    agent._registry["get_time"] = fake_time
    agent._registry["remember"] = fake_remember
    # get_time is speakable (would normally short-circuit) — but the second intent must still run.
    agent._llm = FakeCompleteLLM([
        _tool_msg([("c1", "get_time", "{}")]),
        _tool_msg([("c2", "remember", '{"text": "likes tea"}')]),
        _text_msg("It's 2 PM, and I've noted you like tea, sir."),
    ])
    reply = await agent.respond("tell me the time and remember that I like tea")
    check("get_time ran (first intent)", ran["get_time"] == 1, str(ran))
    check("remember ran too (second intent, no short-circuit)", ran["remember"] == 1, str(ran))
    check("final reply summarises both", "tea" in reply.lower(), repr(reply))


async def test_multi_intent_completion_retry() -> None:
    print("\n[8] multi-intent: a premature stop after ONE tool triggers a forced completion pass (4.2)")
    from jarvis.brain.agent import JarvisAgent

    agent = JarvisAgent()
    ran = {"web_search": 0, "remember": 0}

    async def fake_web(_a):
        ran["web_search"] += 1
        return "Tokyo is the capital of Japan."

    async def fake_remember(_a):
        ran["remember"] += 1
        return "Noted, sir."

    agent._registry["web_search"] = fake_web
    agent._registry["remember"] = fake_remember
    # pass 1: web_search fires; pass 2: the model tries to STOP with text (only 1 tool so far) -> the
    # completion retry forces pass 3; pass 3: remember fires; pass 4: final text.
    agent._llm = FakeCompleteLLM([
        _tool_msg([("c1", "web_search", '{"query": "capital of Japan"}')]),
        _text_msg("It's Tokyo, sir."),
        _tool_msg([("c2", "remember", '{"text": "next destination Tokyo"}')]),
        _text_msg("Noted as your next destination, sir."),
    ])
    reply = await agent.respond(
        "Look up the capital of Japan and remember it as my next travel destination.")
    check("web_search fired (part 1)", ran["web_search"] == 1, str(ran))
    check("remember fired via the forced completion retry (part 2)", ran["remember"] == 1, str(ran))
    check("final reply is the completion", "noted" in reply.lower() or "tokyo" in reply.lower(),
          repr(reply))


async def test_channel_read_short_circuit_flag() -> None:
    print("\n[9] channel-read direct-speak is flag-gated + length-capped (speed/UX A/B)")
    from jarvis.brain.agent import _direct_speakable
    from jarvis.config import settings

    short = [{"name": "list_events", "result": "Nothing on your calendar today, sir.", "ok": True}]
    long = [{"name": "read_email", "result": "You have 9 messages, sir. " + "From X: subject. " * 30,
             "ok": True}]
    util = [{"name": "get_time", "result": "It's 2 PM, sir.", "ok": True}]

    prev = settings.direct_speak_channel_reads
    try:
        settings.direct_speak_channel_reads = False
        check("flag OFF -> channel read is summarised (no short-circuit)",
              _direct_speakable([], short) is None)
        settings.direct_speak_channel_reads = True
        check("flag ON + short -> spoken directly (summary skipped)",
              _direct_speakable([], short) == "Nothing on your calendar today, sir.")
        check("flag ON + long -> still summarised (length cap keeps polish)",
              _direct_speakable([], long) is None)
        check("utility read short-circuits regardless of the flag",
              _direct_speakable([], util) == "It's 2 PM, sir.")
    finally:
        settings.direct_speak_channel_reads = prev


async def main() -> None:
    await test_single_tool_short_circuit()
    await test_multi_tool_keeps_summary()
    await test_read_intents_force_tools()
    await test_parallel_independent_tools()
    await test_confirm_gated_not_run_in_parallel()
    await test_multi_intent_detection()
    await test_multi_intent_no_short_circuit()
    await test_multi_intent_completion_retry()
    await test_channel_read_short_circuit_flag()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
