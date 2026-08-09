"""B1 hermetic test — intent→forced-tool narrowing (no network).

Asserts that a high-precision intent narrows the turn's advertised tools to exactly the right
tool, that multi-intent turns are NOT narrowed (full surface preserved), and that free-form chat
is left alone (regression guard for the already-strong categories).
"""
from afon.brain.intent_router import forced_tools
from afon.brain.tools import schemas_by_name, tool_names
from afon.brain.agent import _narrowed_tools

_ok = 0
_fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# --- 1) each high-precision intent routes to the one right tool ---------------------------
ROUTES = {
    "what's on my calendar today?": "list_events",
    "do I have any new emails?": "read_email",
    "any unread telegram messages?": "check_telegram",
    "what's overdue on my task list?": "notion_tasks",   # canonical = Notion
    "remember my flight is July 3rd": "remember",
    "forget that I told you about the party": "forget",
    "define perspicacious": "define_word",
    "look up the ferry schedule online": "web_search",
    "send an email to Bob saying hello": "send_email",
    "draft an email to the team": "draft_email",
    "reply on telegram saying I'm on my way": "send_telegram",
    "what do you remember about my sister?": "recall",
    "when is my flight to London?": "recall",                 # personal-fact recall (benchmark turn)
    "add a task called behavioral-suite-temp due today": "notion_create_task",  # Notion, not local add_task
    "search my vault for the rabbit-farm plan": "search_vault",
    "schedule a meeting tomorrow at 3pm": "create_event",
    "remind me to stretch in 90 minutes": "set_reminder",
    "open example.com and tell me the page heading": "scrape_url",
    "what's the current price of Bitcoin?": "crypto_price",
    "what's the share price of Apple?": "stock_price",
    "what's the weather in Yerevan today?": "weather",
}
for text, want in ROUTES.items():
    got = forced_tools(text)
    check(bool(got) and got[0] == want, f"{text!r} -> {got}, wanted {want}")

# --- 2) the narrowed name is a REAL registered tool ---------------------------------------
all_names = set(tool_names())
for want in set(ROUTES.values()):
    check(want in all_names, f"routed tool {want!r} exists in registry")

# --- 3) _narrowed_tools actually shrinks the surface to that one tool ----------------------
fake_surface = schemas_by_name(tool_names())  # pretend the whole registry was advertised
tools, narrowed, forced = _narrowed_tools("do I have any new emails?", fake_surface, multi_intent=False)
check(narrowed is True, "email intent flags narrowed=True")
check(forced == "read_email", f"single-tool intent forces read_email by name (got {forced!r})")
check(len(tools) == 1 and tools[0]["function"]["name"] == "read_email",
      f"email intent narrows to exactly read_email (got {[t['function']['name'] for t in tools]})")

# --- 4) multi-intent is NOT narrowed (compound turns need the full surface) ----------------
tools2, narrowed2, forced2 = _narrowed_tools("look up the weather and remember it", fake_surface, multi_intent=True)
check(narrowed2 is False and forced2 is None, "multi-intent skips narrowing")
check(len(tools2) == len(fake_surface), "multi-intent keeps the full tool surface")

# --- 5) free-form chat / arithmetic is left alone (regression guard) -----------------------
for chat in ("what do you think about this plan?", "what's two plus two",
             "how are you today", "tell me a joke", "thanks, that was helpful"):
    tools3, narrowed3, forced3 = _narrowed_tools(chat, fake_surface, multi_intent=False)
    check(narrowed3 is False and len(tools3) == len(fake_surface), f"free-form left alone: {chat!r}")

# --- 5b) B3 degrade scoping: live-data tools degrade on dodge, knowledge tools answer inline ---
from afon.brain.agent import _should_degrade
check(_should_degrade(True, set(), "check_telegram") is True, "live-data dodge degrades (telegram)")
check(_should_degrade(True, set(), "read_email") is True, "live-data dodge degrades (email)")
check(_should_degrade(True, set(), "define_word") is False, "knowledge dodge does NOT degrade (define)")
check(_should_degrade(True, set(), "recall") is False, "knowledge dodge does NOT degrade (recall)")
check(_should_degrade(True, {"check_telegram"}, "check_telegram") is False, "a fired tool never degrades")
check(_should_degrade(False, set(), "read_email") is False, "an un-narrowed turn never degrades")

# --- 6) schemas_by_name is well-formed and skips unknowns ----------------------------------
s = schemas_by_name(["read_email", "not_a_real_tool"])
check(len(s) == 1 and s[0]["function"]["name"] == "read_email", "schemas_by_name skips unknown names")

# --- 7) B3 anti-fabrication: a narrowed+forced intent that dodges the tool degrades honestly ---
# Fake model that REJECTS required (like a provider without forced choice) and narrates a made-up
# number on auto — the classic "you have 5 unread" hallucination. B3 must replace it.
import asyncio
from afon.brain.agent import AfonAgent, _FABRICATION_DEGRADE


class _FakeMsg:
    def __init__(self, content="", tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _DodgingLLM:
    """Always narrates, never calls a tool — on the primary AND the fallback (skip_primary). Records
    each call's skip_primary so the test can prove B4 attempted the fallback retry."""
    def __init__(self):
        self.calls = []

    async def complete(self, messages, tools=None, tool_choice="auto", skip_primary=False):
        self.calls.append({"tool_choice": tool_choice, "skip_primary": skip_primary})
        return _FakeMsg(content="You have 5 unread messages, sir.")   # fabrication, no tool_calls


async def _run_b3():
    a = AfonAgent()
    a._llm = _DodgingLLM()
    a._self_improve = False
    # narrowed LIVE-DATA intent that dodges -> honest degrade, no fabricated number. Uses a NON-zero-arg
    # read (crypto price) so it stays on the model path; zero-arg reads now fire deterministically (B5-lite).
    r1 = await a.respond("what's the current price of Bitcoin?")
    check(r1 == _FABRICATION_DEGRADE, f"B3 degrades a dodged live-data intent (got {r1!r})")
    # Tool-tier/B4: a forced tool turn must reach the reliable fallback tool-caller (skip_primary=True) —
    # either straight away (tool_turns_prefer_fallback) or via the B4 retry after a primary dodge.
    check(any(c["skip_primary"] for c in a._llm.calls),
          "forced tool turn uses the fallback tool-caller (skip_primary=True)")
    # a KNOWLEDGE intent (define) that dodges -> inline answer passes through, NOT degraded
    a2 = AfonAgent()
    a2._llm = _DodgingLLM()
    a2._self_improve = False
    r2 = await a2.respond("define ephemeral")
    check(r2 != _FABRICATION_DEGRADE, f"B3 does NOT degrade a dodged knowledge intent (got {r2!r})")
    # free-form chat is NOT narrowed -> content passes through unchanged (guard is scoped)
    a3 = AfonAgent()
    a3._llm = _DodgingLLM()
    a3._self_improve = False
    r3 = await a3.respond("how are you today?")
    check(r3 != _FABRICATION_DEGRADE, f"B3 does NOT fire on free-form chat (got {r3!r})")
    # B4 (broadened): a MULTI-INTENT forced dodge (not narrowed) must ALSO retry on the fallback —
    # this is what rescues the get_time+remember combos when the primary answers one part inline.
    a4 = AfonAgent()
    a4._llm = _DodgingLLM()
    a4._self_improve = False
    await a4.respond("what's the time, and also remember that I prefer tea over coffee")
    check(any(c["skip_primary"] for c in a4._llm.calls),
          "a multi-intent forced turn reaches the fallback tool-caller (tool-tier/B4)")


asyncio.run(_run_b3())


# --- 8) B5-lite: a zero-arg read fires DETERMINISTICALLY, no model round-trip to dodge ---
class _CountingLLM:
    def __init__(self):
        self.completes = 0

    async def complete(self, messages, tools=None, tool_choice="auto", skip_primary=False):
        self.completes += 1
        return _FakeMsg(content="You have 5 unread, sir.")   # would fabricate if ever consulted


async def _run_b5():
    a = AfonAgent()
    a._llm = _CountingLLM()
    a._self_improve = False
    fired: list[str] = []

    async def fake_exec(messages, calls, content="", on_progress=None):
        fired.extend(c["name"] for c in calls)
        return [{"name": c["name"], "result": "No unread messages, sir.", "ok": True} for c in calls]

    a._execute_calls = fake_exec  # type: ignore[assignment]
    r = await a.respond("any unread telegram messages?")
    check("check_telegram" in fired, "zero-arg read fires the tool deterministically")
    check(r == "No unread messages, sir.", f"speaks the short tool result verbatim (got {r!r})")
    check(a._llm.completes == 0, f"short zero-arg read needs no model round-trip (completes={a._llm.completes})")


asyncio.run(_run_b5())


# --- 9) B5 thinking-tier: an arg-bearing forced tool the fast models DODGE is fired by the reasoning model ---
from types import SimpleNamespace  # noqa: E402


class _ThinkingRescueLLM:
    """Dodges on the fast path (no prepend_model) but fires the tool once the thinking-tier escalates
    (prepend_model set) — models MiniMax-Text-01 missing set_reminder while MiniMax-M2.5 nails it."""
    def __init__(self):
        self.calls = []

    async def complete(self, messages, tools=None, tool_choice="auto", skip_primary=False, prepend_model=None):
        self.calls.append({"prepend_model": prepend_model})
        if prepend_model:  # the reasoning model fires the tool with real args
            tc = SimpleNamespace(id="1", function=SimpleNamespace(
                name="set_reminder", arguments='{"text":"stretch","when":"in 90 minutes"}'))
            return _FakeMsg(content="", tool_calls=[tc])
        return _FakeMsg(content="Sure, I'll remind you.", tool_calls=None)   # fast-path dodge


async def _run_b5_thinking():
    from afon.config import settings
    check(bool(settings.llm_thinking_model), "a MiniMax thinking model is configured by default")

    a = AfonAgent()
    a._llm = _ThinkingRescueLLM()
    a._self_improve = False
    ran: list[str] = []

    async def fake_exec(messages, calls, content="", on_progress=None):
        ran.extend(c["name"] for c in calls)
        return [{"name": c["name"], "result": "Reminder set, sir.", "ok": True} for c in calls]

    a._execute_calls = fake_exec  # type: ignore[assignment]
    await a.respond("remind me to stretch in 90 minutes")
    check(any(c["prepend_model"] for c in a._llm.calls), "B5 escalates to the thinking model on a dodge")
    check("set_reminder" in ran, "the thinking model's tool call actually executes")
    # a conversational turn must NOT trigger the thinking-tier (cost is only paid on forced dodges)
    b = AfonAgent()
    b._llm = _ThinkingRescueLLM()
    b._self_improve = False
    await b.respond("what do you think about that idea?")   # no tool trigger words
    check(not any(c["prepend_model"] for c in b._llm.calls), "no thinking-tier on a conversational turn")


asyncio.run(_run_b5_thinking())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
import sys
sys.exit(1 if _fail else 0)
