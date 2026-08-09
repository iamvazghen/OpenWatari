"""AfonAgent — Afon's own reasoning loop: personality + memory + tools + session.

He answers as himself first. Tools are things he reaches for (the current time; the
OpenClaw fleet for deep work) — never a change of identity. Conversation history is kept
so he remembers the flow across turns ("what did I just ask you?").
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, NamedTuple
from zoneinfo import ZoneInfo

from loguru import logger

from afon.brain.context import build_system_prompt
from afon.brain.fleet import FLEET_TOOL_SCHEMA, FleetUnavailable, delegate_to_fleet
from afon.config import settings
from afon.brain.llm import LLMClient
from afon.brain.tools import (
    core_tool_schemas,
    group_tool_schemas,
    groups_for_text,
    tool_handlers,
)

# Short spoken filler per tool so a longer turn is never dead air. These are the BASE
# acknowledgements; _ack_for() adds context from the arguments where it helps ("…for the rabbit
# farm charter"). An unknown tool still gets a generic "On it, sir." so EVERY tool use is announced.
_TOOL_PROGRESS = {
    "delegate_to_fleet": "Right away, sir — putting that to the team lead",
    "search_vault": "Checking your vault",
    "read_vault_note": "Reading that note",
    "write_vault": "Saving that to your vault",
    "web_search": "Looking that up",
    "scrape_url": "Opening the page",
    "browse_web": "Opening a browser",
    "check_telegram": "Checking your Telegram",
    "read_chat": "Reading that chat",
    "send_telegram": "Sending that",
    "telegram_music": "Pulling that from your playlist",
    "play_in_music_room": "Cueing it up in your music room",
    "stop_music_room": "Leaving the music room",
    "play_music": "Finding that song",
    "stop_music": "Stopping the music",
    "open_url": "Opening that",
    "open_app": "Opening that",
    "file_op": "Working on your files",
    "process_op": "On it",
    "run_powershell": "Running that",
    "browser": "In the browser",
    "run_protocol": "Authorizing the protocol",
    "set_reminder": "Setting that reminder",
    "send_push": "Pinging your phone",
    "get_time": "Getting the time",
    "weather": "Checking the weather",
    "list_events": "Checking your calendar",
    "create_event": "Adding that to your calendar",
    "read_email": "Checking your email",
    "send_email": "Sending that email",
    "remember": "Noting that down",
    "recall": "Let me recall",
    "ha_state": "Checking that device",
    "ha_call": "On it",
}

# Generic instant acknowledgements (G1) — spoken before the LLM runs to kill first-token dead air.
# Rotated so Afon doesn't say the identical line every turn (the old code only had one each). WORK =
# a clear command/tool turn; CHAT = a request that tripped a tool-group but reads more conversational.
_WORK_ACKS = ("Right away, sir.", "On it, sir.", "Of course, sir.", "Let me take care of that, sir.",
              "Consider it done, sir.")
_CHAT_ACKS = ("Yes, sir.", "Certainly, sir.", "Of course, sir.", "One moment, sir.")

# Args whose value gives a natural tail for the acknowledgement ("Looking that up — <query>, sir.").
_ACK_CONTEXT_KEYS = ("query", "task", "song", "title", "city", "location", "to", "name", "topic",
                     "url", "app")


# Spoken noun for a tool, used in "still working on <label>" long-task updates.
_TOOL_LABELS = {
    "open_url": "the browser", "open_app": "the app launch", "browser": "the browser",
    "browse_web": "the browser", "web_search": "the search", "scrape_url": "the page",
    "delegate_to_fleet": "the team's task", "work_on_task": "the research",
    "run_powershell": "the command", "file_op": "the file work",
    "send_email": "the email", "play_music": "the music", "composio_run_tool": "the app action",
}


def _ack_for(name: str, args: dict) -> str:
    """A brief, natural, CONTEXTUAL acknowledgement spoken before a tool runs (Afon-style):
    'Right away, sir — putting that to the team lead.', 'Looking that up — BTC price, sir.'"""
    base = _TOOL_PROGRESS.get(name, "On it")
    tail = ""
    for k in _ACK_CONTEXT_KEYS:
        v = args.get(k) if isinstance(args, dict) else None
        if isinstance(v, str) and 0 < len(v) <= 60:
            spoken = v.strip()
            if k == "url":  # "https://www.youtube.com/x" would be read out character by character
                spoken = re.sub(r"^https?://(www\.)?", "", spoken).split("/")[0]
            tail = f" — {spoken}"
            break
    line = f"{base}{tail}"
    return line if line.rstrip().endswith(("sir", "sir.")) else f"{line}, sir."

# The owner's local timezone (configurable via AFON_USER_TZ). Used for time/greeting/scheduling.
USER_TZ = ZoneInfo(settings.user_tz)

# Some models, when pushed past their tool budget or asked to speak without tools, emit a
# textual tool-call (e.g. "<tool_call>browser ...") instead of prose. That must never be
# spoken, so we strip such artifacts from any final reply.
_ARTIFACT_RE = re.compile(
    r"<\s*/?\s*(tool_call|arg_key|arg_value|function|tool_response)\s*>|<\|[^>]*\|>",
    re.IGNORECASE,
)

# Self-closing / parameterised textual tool-call forms a weak model may emit instead of a native
# call — e.g. '<function name="x" parameters="{…}" />', '<tool_code …>', 'print(default_api.x(…))'.
# The LLM layer fails over off these, but strip any that still reach a spoken reply (defence in depth).
_TOOLCALL_TEXT_RE = re.compile(
    r"<\s*function\b[^>]*/?>|<\s*tool_code\b[^>]*>.*?(?:</\s*tool_code\s*>|$)|"
    r"\bprint\s*\(\s*default_api\.[^)]*\)|\bdefault_api\.\w+\([^)]*\)",
    re.IGNORECASE | re.DOTALL,
)


# Weak models (e.g. llama-3.1-8b-instant) sometimes SPEAK a confirmation ("done, home is now X")
# without actually emitting the tool call. For unambiguous *imperative* commands — where the right
# behaviour is unquestionably to call a tool — we force the provider's tool_choice to "required" on
# the first pass so the model physically cannot answer with a hollow confirmation. Kept deliberately
# tight (clear commands, not capability questions) so normal chat still flows on "auto".
_COMMAND_PATTERNS = (
    r"\b(set|change|update|switch)\b.{0,30}\b(home|location)\b",
    r"\bremind me\b", r"\bset (a |an )?reminder\b", r"\bremember (that|to|my)\b",
    r"\b(send|reply)\b.{0,30}\b(telegram|message|dm|text)\b",
    r"\b(turn|switch) (on|off)\b", r"\b(lock|unlock|dim)\b", r"\bset the (thermostat|heating)\b",
    r"\bplay\b.{0,40}\b(song|music|track|playlist)\b", r"\bstop the music\b",
    r"\b(add|schedule|create).{0,30}\b(calendar|event|meeting|appointment)\b",
    r"\b(draft|send).{0,20}\bemail\b", r"\bback ?up (my )?(memory|vault)\b",
    r"\brun (the )?protocol\b", r"\bsearch (my )?vault\b",
)
_COMMAND_RE = re.compile("|".join(_COMMAND_PATTERNS), re.IGNORECASE)

# Anaphoric / terse follow-up commands ("set it back to X", "make it Paris", "turn it off") carry
# no object noun, so the patterns above miss them and a weak model fakes the confirmation. Catch a
# leading imperative verb (optionally after "now/actually/no/please/ok") as a command too. Scoped to
# action verbs so ordinary replies — which rarely START with these — aren't forced into a tool call.
_IMPERATIVE_RE = re.compile(
    r"^\s*(now|actually|no,?|please|ok|okay|and|then)?\s*"
    r"(set|change|update|switch|make|turn|play|send|remind|add|schedule|remember|"
    r"lock|unlock|dim|put|cancel|delete|remove|stop|start|run|back ?up|draft|reply)\b",
    re.IGNORECASE,
)


# Data-READ intents (Roadmap 2.4): questions whose honest answer can only come from a tool —
# email/calendar/tasks/telegram contents, live weather/price/FX, web/vault lookups, reminder lists.
# A weak model otherwise fabricates ("you have no new email") without ever calling the tool, so for
# these we also force tool_choice on the first pass. Kept to clear data-pull phrasings so opinion /
# chat / arithmetic ("what do you think…", "what's two plus two") still answer freely on auto.
_READ_INTENT_PATTERNS = (
    r"\b(weather|temperature|forecast)\b",
    r"\b(price|worth|quote)\b.{0,20}\b(of|for)\b|\bhow much is\b|\b(stock|share) price\b",
    r"\b(exchange rate|fx rate)\b|\bconvert\b.{0,20}\b(to|into)\b",
    r"\b(unread|new|any)\b.{0,20}\b(email|emails|mail|inbox|telegram|messages?|dms?)\b",
    r"\b(check|read)\b.{0,20}\b(email|inbox|telegram|messages?)\b",
    r"\bwhat'?s? (on|in)\b.{0,20}\b(calendar|agenda|schedule|inbox|plate)\b",
    r"\b(my|the)\b.{0,12}\b(calendar|agenda|schedule|events?|meetings?|appointments?)\b",
    r"\b(due|overdue)\b|\b(my|any) (tasks?|reminders?|to-?dos?)\b|\bon my plate\b|\bwhat'?s due\b",
    r"\b(search|look up|google|find)\b.{0,30}\b(online|web|vault|notes?|internet)\b",
    r"\b(latest|recent) (news|headlines?|on)\b|\bheadlines\b",
    # A bare "news" too, when it's clearly a request for some: "any tech news", "what's the news".
    # Without this, "any tech news" forced no tool at all and the model answered from its own
    # weights — a stale-knowledge answer to a question that is only ever about right now, which is
    # the exact fabrication this list exists to stop. Qualifier-led so "that's good news" and
    # "no news is good news" stay chat.
    r"\b(any|whats?|what'?s|latest|recent|todays?|the)\b.{0,20}\bnews\b",
    r"\bsearch (my )?vault\b|\bin my (vault|notes)\b",
)
_READ_INTENT_RE = re.compile("|".join(_READ_INTENT_PATTERNS), re.IGNORECASE)


def _wants_forced_tool(user_text: str) -> bool:
    """True for clear imperative commands OR data-read questions that must result in a tool call,
    not a spoken claim/fabrication."""
    t = user_text or ""
    return bool(_COMMAND_RE.search(t) or _IMPERATIVE_RE.search(t) or _READ_INTENT_RE.search(t))


# Multi-intent connectors (Roadmap 4.2): a compound request ("look up X AND remember it", "do A then
# B") where a weak model often satisfies only the first part. We detect the connector joining a SECOND
# action and add a completion nudge so the tool loop keeps going until every part is done. Connectors
# are paired with an action verb so "fish and chips" / "nice and quiet" don't trigger.
_MULTI_INTENT_RE = re.compile(
    r"\b(and|then|also|plus|afterwards?|after that|as well as)\b[^.?!]{0,40}?\b("
    r"remember|note|save|send|set|add|schedule|create|draft|reply|look up|search|check|find|"
    r"play|turn|lock|unlock|email|message|text|remind|put|delete|cancel|summarise|summarize|"
    r"write|tell|give|update)\b",
    re.IGNORECASE,
)
_MULTI_INTENT_NUDGE = (
    "This request has MORE THAN ONE part. Complete EVERY part — use the right tool for each, one "
    "after another — and do not give your final reply until all parts are done or you've said which "
    "part you can't do and why."
)
# Injected for a multi-intent turn when the model tries to STOP after firing only one tool — it nearly
# always means a second part (save/send/set it) is still unsatisfied. We give it exactly ONE forced
# pass to complete the remaining part before the turn ends (Roadmap 4.2).
_MULTI_INTENT_COMPLETE = (
    "You have handled only ONE part of the request so far. Now CALL THE TOOL for the REMAINING part "
    "(e.g. remember/save it, send it, set it, add it, note it) before you give your final reply."
)


def _is_multi_intent(user_text: str) -> bool:
    return bool(_MULTI_INTENT_RE.search(user_text or ""))


class TurnPlan(NamedTuple):
    """Everything decided about a turn BEFORE the first model call.

    Exists because `respond()` and `_respond_stream_impl()` each carried their own copy of
    these ~26 lines. The streaming mechanics genuinely differ — one returns a string, one
    yields chunks — but the DECISIONS must not, and two copies meant every behavioural fix
    had to be made twice. A fix applied once produced a defect that appeared only on one
    path, depending on how the turn arrived: the hardest kind to reproduce.
    """
    messages: list[dict[str, Any]]
    turn_tools: list[dict[str, Any]]
    narrowed: bool
    forced_name: str | None
    force_first: bool
    multi_intent: bool
    work_intent: bool
    clause_plan: list[str]


def _completion_force(clause_plan: list[str], seen_tools: set[str],
                      turn_tools: list[dict[str, Any]]):
    """On a multi-intent completion pass, force the specific tool that has NOT fired yet.

    Returns ``(tools, forced_name)`` narrowed to that one tool, or ``(turn_tools, None)``
    when there is nothing specific to force.

    This is what `clause_tools` was built for. The old completion pass appended a prose
    nudge — "now CALL THE TOOL for the REMAINING part" — and hoped the model picked the
    right one, which is the same hope that fails everywhere else in this file: the
    non-thinking primary narrates instead of calling. Knowing WHICH tool is outstanding
    turns that hope into a named, forced call.

    Fail-quiet: any problem here degrades to the old prose-nudge behaviour rather than
    breaking the turn.
    """
    try:
        missing = [n for n in clause_plan if n not in seen_tools]
        if not missing:
            return turn_tools, None
        from afon.brain.tools import schemas_by_name
        narrowed = schemas_by_name(missing[:1])
        if narrowed:
            return narrowed, narrowed[0]["function"]["name"]
    except Exception as e:  # noqa: BLE001 — completion must never break a turn
        logger.debug(f"clause completion skipped: {type(e).__name__}: {e}")
    return turn_tools, None


# Catastrophic system-destruction commands — refused DETERMINISTICALLY, before the model, with NO tool
# call at all. Defense-in-depth on top of the protected-paths guard (file_op) + the confirm tier: a weak
# model can't even emit the destructive call, and the refusal is guaranteed. Deliberately NARROW —
# wiping a system root, formatting a drive, or mass shell deletion of the system — so a normal
# "delete this file" still flows through the confirm-gated file_op as before.
_CATASTROPHIC_RE = re.compile(
    r"(?:\b(?:delete|remove|wipe|erase|destroy|format|nuke|del|rm)\b[^.?!]*\b(?:"
    r"system32|c:\\?\s*windows|windows\s+(?:folder|directory)|system\s+drive|c[:\s]+drive|"
    r"boot\s+(?:partition|sector)|registry|program\s+files|"
    r"everything\s+(?:on|in)\s+(?:my|the)\s+(?:pc|computer|laptop|c\s*drive|system|hard\s*drive))\b)"
    r"|\brm\s+-rf\s+/(?:\s|$|\*)|\bformat\s+c:|\bdel\s+/[fsq]\b[^.?!]*\bc:\\?\s*windows",
    re.IGNORECASE,
)
_CATASTROPHIC_REFUSAL = (
    "No, sir — I won't do that. Wiping that would destroy your system and it can't be undone, so I've "
    "refused it. If you meant a specific file or folder, tell me exactly which and I'll confirm first."
)


def _catastrophic(user_text: str) -> bool:
    return bool(_CATASTROPHIC_RE.search(user_text or ""))


# Background-work intent (Phase 4.1 / Autonomy): a research-AND-produce request that should be handed to
# work_on_task (the bounded background worker), not answered inline. Requires BOTH an investigate verb
# AND a deliverable noun, so a quick "what's the capital of Japan" still answers live on auto.
_WORK_INTENT_RE = re.compile(
    r"\b(?:look into|research|dig into|investigate|analyse|analyze|compile|put together|work on|"
    r"write\s*up|write me|draft me|prepare|pull together)\b[^.?!]*\b(?:"
    r"summary|summarise|summarize|write[-\s]?up|report|brief|briefing|overview|analysis|breakdown|"
    r"comparison|plan|draft|rundown|memo|document)\b",
    re.IGNORECASE,
)
_WORK_INTENT_NUDGE = (
    "This is a multi-step research-and-write-up request. Call work_on_task to do it in the BACKGROUND "
    "(it researches and drafts, then reports back) and tell the owner you're on it — do NOT try to "
    "answer it all inline in this turn."
)


def _is_work_intent(user_text: str) -> bool:
    return bool(_WORK_INTENT_RE.search(user_text or ""))


# NOTE: the per-turn "flaky tool" reliability note was REMOVED. Telling a non-thinking model that its
# tools are unreliable made it DODGE tool calls (parroting "the tools have been flaky, I won't promise
# they'll work") — the opposite of the intent. Tool-reliability is now handled where it belongs: in code
# via the B4 fallback retry (a dodged forced call re-runs on a reliable tool-caller), and surfaced to the
# owner on the HUD via tool_reliability.reliability_summary(). The prompt no longer discourages tool use.


# B1: on a narrowed intent we advertise ONLY the right tool and force tool_choice="required". We tried
# forcing the specific tool BY NAME ({"type":"function",...}) too, but MiniMax-Text-01 honours it no
# better than "required" (it dodged define/telegram under both) while adding a failure mode — so we keep
# the simpler "required". The single-tool surface is what actually stops mis-selection.

# B3 degrade set: a dodged forced call becomes an honest "couldn't pull that up" ONLY for live/personal-
# data or side-effect tools, where speaking without the tool = fabrication or a false "I did it". For
# pure-KNOWLEDGE tools the model legitimately answers inline (define knows "ephemeral"; recall is backed
# by the auto-RAG note), so degrading there is worse than the correct inline answer.
_NO_DEGRADE = frozenset({"define_word", "recall"})


def _should_degrade(narrowed: bool, seen_tools: set, forced_name: str | None) -> bool:
    """B3: narrowed+forced intent fired NO tool, and it wasn't a knowledge tool that may answer inline."""
    return narrowed and not seen_tools and forced_name not in _NO_DEGRADE


# B3 — anti-fabrication hard stop. If B1 narrowed a turn to the one right tool and forced it, yet the
# turn still ended with ZERO tools fired (a model in the chain rejected `required` and fell back to auto,
# then narrated instead of calling), the spoken reply is unreliable — the classic "you have 5 unread"
# hallucination. We drop it for an honest degrade rather than let a fabricated number/claim through.
_FABRICATION_DEGRADE = (
    "I wasn't able to pull that up just now, sir — let me try again in a moment rather than guess."
)


def _narrowed_tools(user_text: str, turn_tools: list[dict[str, Any]], multi_intent: bool):
    """B1 router: on a high-precision single intent, replace the turn's tool surface with just the
    one right tool so a forced call cannot mis-select (get_time) or fabricate. Returns
    (tools, narrowed?, forced_name). ``forced_name`` is the tool to force by NAME when the intent
    resolves to exactly one tool — a stronger signal than tool_choice="required", which MiniMax-Text-01
    honours inconsistently (it dodged define_word/set_reminder under plain "required"). Skipped on
    multi-intent turns (they need the full surface). Fail-quiet."""
    if multi_intent:
        return turn_tools, False, None
    try:
        from afon.brain.intent_router import forced_tools
        from afon.brain.tools import schemas_by_name
        names = forced_tools(user_text)
        if not names:
            return turn_tools, False, None
        narrowed = schemas_by_name(names)
        if narrowed:
            forced_name = narrowed[0]["function"]["name"] if len(narrowed) == 1 else None
            logger.info(f"B1 intent router: narrowing turn to {[s['function']['name'] for s in narrowed]}"
                        + (f" (forcing {forced_name} by name)" if forced_name else ""))
            return narrowed, True, forced_name
    except Exception as e:  # noqa: BLE001 — routing must never break a turn
        logger.debug(f"intent router skipped: {type(e).__name__}: {e}")
    return turn_tools, False, None


# A short affirmation that grants a pending confirmation ("yes", "go ahead", "do it", "send it").
# Used by the confirm-tier gate: a held-back outward/destructive tool runs only after one of these.
_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|please do|please go ahead|confirm|"
    r"confirmed|affirmative|sounds good|go for it|proceed|send it|do that|that'?s right|"
    r"correct|fine|absolutely|yes please|go|right)\b",
    re.IGNORECASE,
)


def _is_affirmation(text: str) -> bool:
    return bool(_AFFIRM_RE.match(text or ""))


# Pure conversational turns (greetings, thanks, small talk, opinions, a joke) never call a tool. Yet the
# model is otherwise handed the full ~56-tool surface every turn — a ~10k-token (~31KB) prefill that adds
# ~1.7s of first-word latency (measured: MiniMax TTFT 0.5s with no tools vs ~2.2s with the full surface)
# for nothing. On a high-confidence chatter turn we carry NO tools, so the model answers immediately.
# ANCHORED to the whole utterance + length-capped, so it can NEVER swallow a tool-needing turn
# ("what do you think about my calendar?" is 7 words but fails the ^…$ match → keeps its tools).
_PURE_CHAT_RE = re.compile(
    r"^\s*(hi|hey+|hello|hiya|yo|howdy|good\s*(morning|afternoon|evening|night)|greetings|"
    r"how\s*(are|'?re)\s*(you|ya|things)|how\s*(are\s*)?you\s*doing|how'?s\s*it\s*going|"
    r"how\s*have\s*you\s*been|what'?s\s*up|sup|"
    r"thank(s| you)( so much| a lot| very much)?|cheers|much appreciated|appreciate it|"
    r"well done|good job|nice(\s*(work|one))?|awesome|great(\s*job)?|amazing|brilliant|perfect|excellent|"
    r"good\s*night|goodnight|bye|goodbye|see\s*(you|ya)( later| soon)?|talk\s*(to\s*you\s*)?later|"
    r"tell me a joke|say something funny|you'?re (funny|hilarious|great|the best)|that'?s funny|ha+|lol|lmao|"
    r"how do you feel|are you (ok|okay|there|alright|awake|listening)|you good|you there|"
    r"i (love|like|appreciate) you|love you|"
    r"cool|nice|neat|got it|gotcha|i see|makes sense|no worries|my bad|of course|"
    r"never\s*mind|nevermind|forget it|just (saying|checking|kidding))"
    r"[\s,.!'?]*(afon|afon|sir|buddy|mate|man|dude|please|then|too|though|there|everyone|all)?[\s,.!'?]*$",
    re.IGNORECASE,
)


def _is_pure_chat(text: str) -> bool:
    """High-confidence conversational turn that needs no tool (so we advertise none → fast first word)."""
    t = (text or "").strip()
    if not t or len(t.split()) > 7:
        return False
    return bool(_PURE_CHAT_RE.match(t))


def _clean_reply(text: str) -> str:
    text = (text or "")
    # Drop whole <tool_call>…</tool_call> blocks first, then any stray tags/tokens.
    text = re.sub(r"<tool_call>.*?</tool_call>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = _TOOLCALL_TEXT_RE.sub(" ", text)
    text = _ARTIFACT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Sentence boundary for streaming: a .!?… followed by whitespace ends a speakable chunk. We feed
# whole sentences to TTS so Afon starts talking on sentence 1 while the rest still generates.
_SENT_BOUNDARY = re.compile(r"[.!?…](?=\s)")


def _pop_sentences(buf: str) -> tuple[list[str], str]:
    """Split off any COMPLETE sentences from a running buffer; return (sentences, remainder)."""
    out: list[str] = []
    while True:
        m = _SENT_BOUNDARY.search(buf)
        if not m:
            break
        idx = m.end()
        seg = buf[:idx].strip()
        if seg:
            out.append(seg)
        buf = buf[idx:].lstrip()
    return out, buf


# Read-only tools whose string result is ALREADY a natural spoken sentence (they end in ", sir."
# and contain the whole answer). For a turn that calls exactly ONE of these and nothing else, we
# speak the tool result directly and SKIP the second "summarise what you found" LLM pass — saving a
# whole round trip on the most common quick lookups (time/weather/price/fx/convert/define/…). The
# summary pass is kept for multi-tool, ambiguous, action, or non-speakable turns. (Roadmap 2.3.)
_SPEAKABLE_DIRECT = frozenset({
    "get_time", "weather", "crypto_price", "stock_price", "fx_rate", "convert",
    "define_word", "wiki_lookup", "news_brief",
})
# Channel/task READS that ALSO return a natural spoken sentence ("Nothing on your calendar today, sir.",
# "You have 2 new emails, sir: …"). Speaking them verbatim skips the summary LLM pass (~1-3s faster on
# these latency-dragged turns) but loses the summary's polish on a raw multi-item dump — a UX-vs-speed
# trade-off, so it's OPT-IN via settings.direct_speak_channel_reads, and only for SHORT results.
_SPEAKABLE_CHANNEL_READS = frozenset({"list_events", "read_email", "notion_tasks", "check_telegram"})
_CHANNEL_DIRECT_MAX = 360   # chars; longer channel reads still get the summary pass even when opted in

# B5-lite: router-narrowed reads that take NO required args (calendar/email/telegram/tasks). When the
# intent router pins the turn to exactly one of these, there's nothing for the model to decide — so we
# fire it deterministically instead of asking a dodgy tool-caller to. Kills the Channels/Tasks-read
# "tool didn't fire → 50" swing at its root (the scorer only needs the read to actually happen).
_ZERO_ARG_READS = frozenset({"list_events", "read_email", "check_telegram", "notion_tasks"})


def _direct_speakable(calls: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> str | None:
    """If the turn was a single speakable read-only tool that succeeded, return its result text to
    speak verbatim (skipping the summary LLM pass); otherwise None (fall through to summarise)."""
    if len(outcomes) != 1:
        return None
    o = outcomes[0]
    if not o["ok"]:
        return None
    name = o["name"]
    text = _clean_reply(o["result"])
    if name in _SPEAKABLE_DIRECT:
        pass  # utility reads are always clean one-liners
    elif settings.direct_speak_channel_reads and name in _SPEAKABLE_CHANNEL_READS:
        if len(text) > _CHANNEL_DIRECT_MAX:
            return None   # long multi-item dump -> keep the summary's polish
    else:
        return None
    # Never short-circuit a sentinel/marker (e.g. an upstream "FOO_REQUIRED") — let the model phrase it.
    if not text or re.search(r"[A-Z]{4,}_[A-Z]{3,}", text):
        return None
    return text


GET_TIME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the owner's current local date and time.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

WORK_ON_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "work_on_task",
        "description": "Work AUTONOMOUSLY on a multi-step task in the background (research, draft, "
                       "summarise, light coding + test). Returns immediately with a task id; Afon "
                       "does the safe work himself and reports back when done. Outward/destructive "
                       "steps (sending, deleting, pushing) are deferred for your approval. Use for "
                       "'look into X and write it up', 'research Y', 'draft Z', not for quick lookups.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "The task to work on, in one clear sentence."}},
            "required": ["task"]},
    },
}

IMPROVE_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "improve_own_code",
        "description": "Improve your OWN source code, autonomously but safely: on a fresh branch, read "
                       "the code, make a minimal change, run the tests, and commit locally if green. "
                       "Branch-only — you NEVER push; the branch is left for the owner to review and "
                       "approve. Off by default; only runs when armed. Use when the owner asks you to "
                       "'improve yourself / fix your own code / change how you work' at the code level.",
        "parameters": {"type": "object", "properties": {
            "objective": {"type": "string",
                          "description": "The improvement to make, in one clear sentence."}},
            "required": ["objective"]},
    },
}


PLAN_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan_task",
        "description": "Plan a task WITH the owner before doing it (the co-pilot dry-run): produce a "
                       "step-by-step plan, surface any clarifying questions, flag steps that will need "
                       "his approval, and recommend whether you or the fleet should do it — WITHOUT "
                       "executing anything. Use when he wants help thinking a task through, or before "
                       "you take on a to-do ('how would you tackle X?', 'plan the website relaunch', "
                       "'let's map out X'). Identify an existing to-do by a word from its title, or "
                       "pass a fresh task string.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "A word from an existing to-do's title, or its id."},
            "task": {"type": "string", "description": "A fresh task to plan (if it's not on the list yet)."}},
            "required": []},
    },
}

EXECUTE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "execute_task",
        "description": "Actually DO a planned to-do for the owner: run it in the background via your "
                       "bounded worker (or the fleet, for deep-domain work), linking progress back to "
                       "the task and reporting when done. Outward/destructive steps (send, delete, "
                       "push, pay) are DEFERRED for his approval — never done unattended. Use after "
                       "he's approved a plan, or when he says 'go ahead and do X', 'take care of X', "
                       "'you handle it'. If the task is still unclear it will ask first unless he says "
                       "to just proceed.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "A word from the to-do's title, or its id."},
            "task": {"type": "string", "description": "A fresh task to execute (if not on the list)."},
            "proceed_anyway": {"type": "boolean",
                               "description": "Set true when the owner says to proceed despite open "
                                              "questions ('just do it', 'proceed anyway')."}},
            "required": []},
    },
}


class AfonAgent:
    def __init__(self, max_history_turns: int = 12, max_tool_iters: int = 4) -> None:
        self._llm = LLMClient()
        self._system = {"role": "system", "content": build_system_prompt()}
        self._history: list[dict[str, Any]] = []
        self._max_history_turns = max_history_turns
        self._max_tool_iters = max_tool_iters
        # The fleet touches shared infra, so it stays off unless armed — either per-session via
        # set_fleet_authorized(True), or for a 24/7 deployment via AFON_FLEET_AUTHORIZED=true.
        from afon.config import settings as _s

        self.fleet_authorized = _s.fleet_authorized
        # Self-improvement loop (background_review): count turns; every N, spawn a background pass
        # that learns durable facts into L1 memory. Off the hot path, so it never slows a reply.
        self._turns_since_review = 0
        self._turns_since_digest = 0     # Fix #2: rebuild the learned-digest every N turns (not startup-frozen)
        self._review_every = max(2, _s.self_improve_every_turns)
        self._self_improve = _s.self_improve_enabled
        # Confirmation tier ENFORCEMENT (not just a prompt rule): an outward-facing / destructive
        # tool (send_email, file_op delete, run_powershell, …) is BLOCKED on first attempt and run
        # only after the owner affirms. _pending_confirm holds the held-back call; _confirm_granted is
        # set for a turn whose utterance affirms a pending one. So a weak model physically cannot
        # fire a consequential tool without a yes.
        self._pending_confirm: dict | None = None
        self._confirm_granted = False
        # Smart session reset: clear stale WORKING memory after a long idle gap (durable memory kept).
        self._last_turn_at: datetime | None = None
        self._idle_reset_min = max(0, _s.session_idle_reset_minutes)
        # Restart-durable working memory: resume the rolling thread after a brain restart/crash.
        self._load_session()
        # The handler REGISTRY is always complete — every tool can execute. What varies per turn is
        # what we ADVERTISE to the model: a lean CORE surface every turn, plus any lazy group the
        # turn needs (fine-tuning.md Item 2). _core_tools = built-ins + core schemas (the typical
        # per-turn surface). Lazy groups light up via _active_groups (with a one-turn warm decay so
        # an immediate follow-up like "reply to it" still has the tools).
        self._core_tools = [GET_TIME_SCHEMA, FLEET_TOOL_SCHEMA, WORK_ON_TASK_SCHEMA,
                            PLAN_TASK_SCHEMA, EXECUTE_TASK_SCHEMA, *core_tool_schemas()]
        # improve_own_code is advertised only alongside the coding lazy group (see _tools_for_turn),
        # so it never bloats the every-turn surface — it appears when the turn is about code.
        self._group_ttl: dict[str, int] = {}     # group -> turns it stays advertised
        self._tools = list(self._core_tools)     # current advertised set (core until a turn needs more)
        self._registry: dict[str, Callable] = {
            "get_time": self._tool_get_time,
            "delegate_to_fleet": self._tool_delegate,
            "work_on_task": self._tool_work_on_task,
            "plan_task": self._tool_plan_task,
            "execute_task": self._tool_execute_task,
            "improve_own_code": self._tool_improve_own_code,
            **tool_handlers(),
        }
        logger.info(f"tools: {len(self._core_tools)} core advertised; "
                    f"{len(self._registry)} total in registry")

    async def _prepare_turn(self, user_text: str) -> TurnPlan:
        """Assemble the turn: history, context notes, tool surface, and what to force.

        THE SINGLE decision path for both `respond()` and `_respond_stream_impl()`. Anything
        that changes what a turn is allowed to do belongs here, so it cannot be applied to one
        response mode and forgotten in the other.

        The caller still owns the catastrophic check and the immediate ack, because those exit
        the turn differently (return vs yield) and merging them would need a sentinel value
        that obscures more than it saves.
        """
        self._history.append({"role": "user", "content": user_text})
        messages = [self._system, *self._history]

        # Research-and-write-up requests go to work_on_task (background); this takes precedence
        # over the generic multi-intent nudge so the model hands off instead of answering inline.
        work_intent = _is_work_intent(user_text)
        multi_intent = _is_multi_intent(user_text) and not work_intent
        if work_intent:
            messages.append({"role": "system", "content": _WORK_INTENT_NUDGE})
        elif multi_intent:
            messages.append({"role": "system", "content": _MULTI_INTENT_NUDGE})

        recall_note = await self._recall_note(user_text)   # auto-RAG (ephemeral, not stored)
        if recall_note:
            messages.append({"role": "system", "content": recall_note})
        rel_note = self._relational_note(user_text)   # affect-aware manner + relationship memory
        if rel_note:
            messages.append({"role": "system", "content": rel_note})
        world_note = self._world_note()   # G5: fresh integration events reach the reactive turn
        if world_note:
            messages.append({"role": "system", "content": world_note})

        turn_tools = self._tools_for_turn(user_text)
        # B1: narrow to the one right tool on a high-precision single intent so a forced call
        # cannot mis-select or fabricate. Deliberately skipped on multi-intent — those need the
        # full surface, and the per-clause plan below is how they get steered instead.
        turn_tools, narrowed, forced_name = _narrowed_tools(user_text, turn_tools, multi_intent)
        force_first = (narrowed or _wants_forced_tool(user_text) or work_intent) and bool(turn_tools)

        # B6: which tool EACH clause of a compound request needs. `forced_tools` answers for the
        # whole string and so returns only the first matching route — for "look up X and remember
        # it" one half won and the other silently routed to nothing, which is precisely what the
        # Combination scenarios measure (51.5 against ~96 elsewhere). Empty for single-intent.
        clause_plan: list[str] = []
        if multi_intent:
            try:
                from afon.brain.intent_router import clause_tools
                clause_plan = clause_tools(user_text)
                if clause_plan:
                    logger.info(f"B6 clause router: compound request needs {clause_plan}")
            except Exception as e:  # noqa: BLE001 — routing must never break a turn
                logger.debug(f"clause router skipped: {type(e).__name__}: {e}")

        return TurnPlan(messages, turn_tools, narrowed, forced_name, force_first,
                        multi_intent, work_intent, clause_plan)

    def _tools_for_turn(self, user_text: str) -> list[dict[str, Any]]:
        """Core surface + any lazy group this turn activates (or is still warm from last turn)."""
        for g in self._group_ttl:                # decay previous activations by one turn
            self._group_ttl[g] -= 1
        # Pure chatter (greeting/thanks/joke/opinion) needs no tool — carry NONE so the ~10k-token tool
        # prefill (≈1.7s of first-word latency) is skipped and the model speaks immediately. Suppressed
        # unconditionally: chatter never needs a group, and a broad trigger ("how are" arms the graph
        # group on "how are you?") would otherwise defeat the fast path.
        if _is_pure_chat(user_text):
            self._group_ttl = {g: ttl for g, ttl in self._group_ttl.items() if ttl > 0}
            self._tools = []
            return []
        for g in groups_for_text(user_text):     # (re)arm groups the utterance calls for
            self._group_ttl[g] = 2               # this turn + one follow-up
        active = [g for g, ttl in self._group_ttl.items() if ttl > 0]
        self._group_ttl = {g: ttl for g, ttl in self._group_ttl.items() if ttl > 0}
        tools = list(self._core_tools)
        for g in active:
            tools.extend(group_tool_schemas(g))
        if "coding" in active:                    # code self-improve rides with the coding group only
            tools.append(IMPROVE_CODE_SCHEMA)
        if active:
            logger.info(f"lazy tool groups active this turn: {active} (+{len(tools) - len(self._core_tools)} tools)")
        self._tools = tools
        return tools

    def _begin_turn(self, user_text: str) -> None:
        """Per-turn bookkeeping before the model runs: smart idle-reset + confirmation-grant state."""
        now = datetime.now(USER_TZ)
        self._maybe_idle_reset(now)                       # uses the PREVIOUS _last_turn_at
        # A pending confirm is granted only if THIS utterance affirms it; any other new request
        # supersedes (drops) the pending one — a "yes" must immediately follow the ask.
        self._confirm_granted = bool(self._pending_confirm) and _is_affirmation(user_text)
        if self._pending_confirm and not self._confirm_granted:
            self._pending_confirm = None
        self._last_turn_at = now
        self._maybe_refresh_digest()                      # Fix #2: unfreeze the startup digest

    def _maybe_refresh_digest(self) -> None:
        """Fix #2 — the system-prompt learned-digest is built once at startup, so on a 24/7 brain a
        fact learned mid-session (by the background reviewer or the remember tool) wouldn't surface
        for days. Rebuild the prompt every N turns so recent facts reach the model without a restart.
        Cheap (reads a few small files); fail-quiet so a rebuild hiccup never blocks a turn."""
        every = settings.memory_digest_refresh_every_turns
        if every <= 0:
            return
        self._turns_since_digest += 1
        if self._turns_since_digest < every:
            return
        self._turns_since_digest = 0
        try:
            self._system = {"role": "system", "content": build_system_prompt()}
            logger.debug("system prompt digest refreshed (mid-session learned facts now in prompt)")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"digest refresh skipped: {type(e).__name__}: {e}")

    def _maybe_idle_reset(self, now: datetime) -> None:
        if not self._idle_reset_min or not self._history or self._last_turn_at is None:
            return
        if (now - self._last_turn_at) >= timedelta(minutes=self._idle_reset_min):
            self.reset_session(reason=f"idle {int((now - self._last_turn_at).total_seconds()//60)}m")

    def reset_session(self, reason: str = "manual") -> None:
        """Smart reset: journal the prior conversation (in the background) and clear WORKING memory.
        Durable memory (L1 learned, L2 journal, L3 vault) is untouched, so he forgets the *thread*,
        not the *person*. Safe to call from voice ("start a new conversation") or on idle."""
        history = list(self._history)
        self._history = []
        self._pending_confirm = None
        self._confirm_granted = False
        # Drop the on-disk snapshot too, so a restart doesn't resurrect the thread we just cleared.
        try:
            self._session_path().unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        if history:
            async def _run() -> None:
                try:
                    await self._journal_history(history)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"reset journal skipped: {e}")
            try:
                asyncio.get_running_loop().create_task(_run())
            except RuntimeError:
                pass  # no loop (sync context) — skip the journal, still reset
        logger.info(f"session reset ({reason}); cleared {len(history)} working messages")

    def note_proactive(self, message: str) -> None:
        """Record something Afon said UNPROMPTED (a proactive nudge or a fired reminder). It goes
        into BOTH the working history (so an immediate reply — "yes do it", "what did you mean?" —
        has context) AND the L2 journal (so the owner can refer back DAYS later — "that thing you
        suggested last week" — long after the 12-turn working window has rolled over)."""
        message = (message or "").strip()
        if not message:
            return
        self._history.append({"role": "assistant", "content": message})
        self._trim()
        try:
            from afon.brain.memory import STORE

            STORE.journal_append(f"[proactive] Afon said unprompted: {message}")
        except Exception:  # noqa: BLE001 — journalling is best-effort, never block the nudge
            pass

    def set_fleet_authorized(self, ok: bool) -> None:
        self.fleet_authorized = ok
        logger.info(f"fleet delegation {'authorized' if ok else 'disabled'} for session")

    async def warmup(self) -> None:
        """Prime the primary model so the first real turn isn't a cold ~3s TTFT."""
        # L3 vault must always be readable; L1 learned-memory readiness logged for visibility.
        try:
            from afon.brain.context import validate_vault
            from afon.brain.memory import STORE

            validate_vault()
            logger.info(f"learned memory (L1): {STORE.count()} facts")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"memory/vault startup check skipped: {e}")
        try:
            # Prime EVERY distinct provider in the chain (primary + each fallback's client), so the
            # first turn and the first failover both skip cold-connect latency — not just the primary.
            await self._llm.warmup()
            logger.info("brain warmup complete (all providers primed)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"brain warmup skipped: {e}")
        self._warm_vault_cache()
        await self._load_mcp_tools()

    def _warm_vault_cache(self) -> None:
        """Read the vault once in the background so L3 recall actually works.

        NOT awaited, and deliberately so: the scan takes ~108s on a cold cache and must never sit
        between startup and the first answer. Until it finishes, L3 behaves exactly as it did
        before (abandoned at the 6s budget); after it, an explicit `recall` reaches the vault in
        ~3s instead of never. The per-turn auto-recall path is untouched — it only uses L1+L2.
        """
        async def _run() -> None:
            try:
                from afon.brain.tools.vault import warm_cache

                t0 = time.monotonic()
                n = await asyncio.to_thread(warm_cache)
                if n:
                    logger.info(f"vault (L3) cache warm: {n} notes in {time.monotonic() - t0:.0f}s")
            except Exception as e:  # noqa: BLE001 — a cold cache is slow, never fatal
                logger.debug(f"vault cache warm skipped: {type(e).__name__}: {e}")

        try:
            asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass   # no loop (tests constructing the agent synchronously) — nothing to warm

    async def _load_mcp_tools(self) -> None:
        """Start any configured MCP servers and fold their tools into the registry + core surface, so
        Afon can call them like any native tool. No config -> no-op; a bad server -> skipped."""
        try:
            from afon.brain.mcp_client import MCP

            n = await MCP.load()
            if n:
                self._registry.update(MCP.handlers)
                self._core_tools.extend(MCP.schemas)
                logger.info(f"MCP: {n} external tool(s) added to the registry")
        except Exception as e:  # noqa: BLE001 — MCP is optional; never block startup
            logger.warning(f"MCP load skipped: {e}")

    # ---- tools ------------------------------------------------------------------------
    async def _tool_get_time(self, _args: dict) -> str:
        # Spoken aloud, so no timezone id in parentheses and no zero-padded hour: the edge reflex
        # already says "It's 10:33 PM, sir.", while the brain path used to read out the full ISO-ish
        # stamp with the tz identifier appended — fine in a log, wrong in a sentence.
        now = datetime.now(USER_TZ)
        return (f"{now.strftime('%I:%M %p').lstrip('0')} on "
                f"{now.strftime('%A, %d %B %Y').replace(' 0', ' ')}")

    async def _tool_delegate(self, args: dict) -> str:
        if not self.fleet_authorized:
            return (
                "FLEET_NOT_AUTHORIZED: tell the owner you can consult the OpenClaw fleet for this, "
                "but it isn't authorized this session yet — ask if he wants you to."
            )
        # Afon briefs the team lead (ispir) only — no per-call agent target by design.
        task = args.get("task", "")
        if args.get("background"):
            # Long/open-ended work: fire it in the BACKGROUND and return at once, so the turn isn't
            # blocked for minutes. The TaskQueue persists progress (status queries read the row) and
            # announces completion by voice. coro_factory receives the queue's progress callback.
            from afon.brain.tasks import TASKS

            t = TASKS.run(
                title=task,
                coro_factory=lambda on_progress: delegate_to_fleet(task, on_progress=on_progress),
                kind="fleet",
            )
            return (
                f"BACKGROUNDED: handed to the team lead (task id {t.id}). Tell the owner you're on it "
                "and you'll let him know the moment it's done — he can ask 'how's that going?' anytime."
            )
        try:
            return await delegate_to_fleet(task, on_progress=lambda n: logger.info(f"[fleet] {n}"))
        except FleetUnavailable as e:
            return f"FLEET_UNAVAILABLE: {e}"

    def _worker_tools(self) -> list[dict[str, Any]]:
        """The tool surface a background worker may use: the full registry (research/draft/code), but
        NOT recursion into itself or another blocking fleet delegation."""
        from afon.brain.tools import tool_schemas

        # SAFETY (Phase 4.2): approve_action/reject_action are withheld from the autonomous worker. It
        # defers outward steps INTO the approval queue; if it could also approve them it would be waving
        # through its own deferrals and the human-in-the-loop gate would be worthless.
        skip = {"work_on_task", "delegate_to_fleet", "approve_action", "reject_action", "list_approvals"}
        return [GET_TIME_SCHEMA, *(s for s in tool_schemas()
                                   if s["function"]["name"] not in skip)]

    async def run_backlog(self, max_tasks: int | None = None) -> list[dict]:
        """Autonomous daily backlog pass (Phase 3.1): attempt the owner's overdue/inbox Notion tasks
        and comment the results. Safe by construction — the worker defers every outward/destructive
        step. Returns the attempts (``[{title, result, commented}]``). Wired to the scheduler in
        ``server.serve()`` when ``AFON_BACKLOG_ENABLED`` is set."""
        from afon.brain.backlog import attempt_backlog

        n = settings.backlog_max_tasks if max_tasks is None else max_tasks
        return await attempt_backlog(self._llm, self._registry, self._worker_tools(), max_tasks=n)

    async def backlog_report(self, attempts: list[dict]) -> str:
        """Spoken report of the autonomous backlog pass — what he did, why, and his reasoning — so a
        proactive action is never silent. Delivered by the scheduler via the proactive path (edge voice
        → Telegram voice note → push). '' when nothing was actually done."""
        from afon.brain.proactive_report import spoken_action_report

        done = [a for a in (attempts or []) if a.get("commented") or a.get("result")]
        return await spoken_action_report(
            self._llm, done,
            trigger="these were overdue or sitting undated in your Notion inbox")

    async def advance_objectives(self) -> list[dict]:
        """Phase 4.1 daily driver: advance the owner's multi-day objectives one safe step each and log
        progress. Safe by construction (the bounded worker defers every outward step). Returns what
        moved (``[{id, text, summary}]``). Wired to the scheduler in ``server.serve()`` when
        ``AFON_OBJECTIVES_ENABLED`` is set."""
        from afon.brain.objectives import advance_objectives

        return await advance_objectives(
            self._llm, self._registry, self._worker_tools(),
            max_objectives=settings.objectives_max, max_steps=settings.objectives_max_steps,
        )

    async def _tool_work_on_task(self, args: dict) -> str:
        """Fire an autonomous, bounded work loop in the BACKGROUND and return at once with a task id.
        The worker does the safe research/draft/coding itself; outward/destructive steps are deferred
        for the owner's approval; completion is announced by voice via the TaskQueue."""
        from afon.brain.tasks import TASKS
        from afon.brain.worker import TaskWorker

        task = (args.get("task") or "").strip()
        if not task:
            return "What would you like me to work on, sir?"
        worker = TaskWorker(self._llm, self._registry, self._worker_tools())
        t = TASKS.run(
            title=task,
            coro_factory=lambda on_progress: worker.run(task, on_progress=on_progress),
            kind="work",
        )
        return (
            f"BACKGROUNDED: I'm working on that now (task id {t.id}). Tell the owner you're on it and "
            "you'll report back the moment it's done — he can ask 'how's that going?' anytime."
        )

    # ---- task co-pilot (Phase 3): clarify -> plan -> confirm -> execute -> report ----------
    def _resolve_todo(self, topic: str):
        """Find one open to-do by id/word (None if no/ambiguous match). Mirrors tools/tasks._resolve."""
        from afon.brain.tasks import TASKS

        matches = TASKS.find_todo((topic or "").strip())
        return matches[0] if len(matches) == 1 else None

    async def _tool_plan_task(self, args: dict) -> str:
        """Plan a task WITH the owner (dry-run): build a step plan + clarifying questions + who does it,
        store it on the to-do so execute_task can reuse it, and read it back for approval. Runs nothing."""
        from afon.brain.copilot import build_plan, plan_to_meta, spoken_plan
        from afon.brain.tasks import TASKS

        topic = (args.get("topic") or "").strip()
        raw = (args.get("task") or "").strip()
        todo = self._resolve_todo(topic) if topic else None
        if todo is None and topic and not raw:
            raw = topic  # a word that didn't match a to-do — plan it as a fresh task
        title = todo.title if todo else raw
        description = todo.description if todo else ""
        if not title:
            return "What task should I plan, sir?"
        plan = await build_plan(title, description, self._llm, fleet_available=self.fleet_authorized)
        if todo is not None:  # persist the plan on the to-do so execute_task reuses it
            todo.meta["plan"] = plan_to_meta(plan)
            TASKS.edit_todo(todo.id)
        return spoken_plan(plan, title)

    async def _tool_execute_task(self, args: dict) -> str:
        """Execute a (planned) task in the BACKGROUND via the bounded worker or the fleet, link progress
        back to the to-do, and report on completion. Outward/destructive steps are always deferred."""
        from afon.brain.copilot import build_plan, execution_objective, plan_from_meta, plan_to_meta
        from afon.brain.tasks import TASKS
        from afon.brain.worker import TaskWorker

        topic = (args.get("topic") or "").strip()
        raw = (args.get("task") or "").strip()
        proceed = bool(args.get("proceed_anyway")) or settings.copilot_autopilot_enabled
        todo = self._resolve_todo(topic) if topic else None
        if todo is None and topic and not raw:
            raw = topic
        title = todo.title if todo else raw
        description = todo.description if todo else ""
        if not title:
            return "Which task should I take on, sir?"

        # Reuse an approved plan if we have one; otherwise plan on the spot.
        plan = plan_from_meta(todo.meta.get("plan")) if todo else None
        if plan is None:
            plan = await build_plan(title, description, self._llm, fleet_available=self.fleet_authorized)
            if todo is not None:
                todo.meta["plan"] = plan_to_meta(plan)
                TASKS.edit_todo(todo.id)
        # Still-underspecified and no explicit go-ahead -> ask first (don't guess).
        if plan.needs_clarification and not proceed:
            return ("Before I start on '" + title + "', sir — " +
                    " ".join(q.rstrip("?") + "?" for q in plan.questions) +
                    " Answer those, or say 'just proceed'.")

        objective = execution_objective(title, description, plan)
        use_fleet = plan.executor == "fleet" and self.fleet_authorized
        todo_id = todo.id if todo else None

        async def _run_and_link(on_progress):
            if use_fleet:
                result = await delegate_to_fleet(objective, on_progress=on_progress)
            else:
                worker = TaskWorker(self._llm, self._registry, self._worker_tools(),
                                    max_steps=settings.copilot_max_steps)
                result = await worker.run(objective, on_progress=on_progress)
            if todo_id is not None:  # link the outcome back onto the to-do before completion fires
                try:
                    TASKS.edit_todo(todo_id, note="Afon worked on it: " + result[:400])
                except Exception:  # noqa: BLE001
                    pass
            return result

        t = TASKS.run(title=f"execute: {title}", coro_factory=_run_and_link, kind="work",
                      meta={"todo_id": todo_id} if todo_id else None)
        if todo_id is not None:
            todo.meta["exec_job"] = t.id
            TASKS.edit_todo(todo_id, note="Afon is working on it now.")
        who = "the fleet" if use_fleet else "it myself"
        downgraded = (plan.executor == "fleet" and not self.fleet_authorized)
        note = " (the fleet isn't authorized this session, so I'll do the safe parts myself)" if downgraded else ""
        return (f"BACKGROUNDED: I'm on '{title}' now via {who}{note} (task id {t.id}). Tell the owner "
                "you'll report back the moment it's done — he can ask 'how's that going?' anytime.")

    async def _tool_improve_own_code(self, args: dict) -> str:
        """Run one bounded, branch-only code self-improvement pass in the BACKGROUND (off by default).
        Afon branches, edits, tests, and commits locally; he never pushes — the branch waits for the
        owner's review. Returns at once with a task id; completion is announced by voice."""
        from afon.brain.code_improve import run_code_self_improve
        from afon.brain.tasks import TASKS

        objective = (args.get("objective") or "").strip()
        if not objective:
            return "What would you like me to improve in my own code, sir?"
        from afon.config import settings as _s
        if not _s.code_self_improve_enabled:
            # Answer inline (no background task) so the owner immediately hears it's disarmed.
            return ("Code self-improvement is switched off by default, sir — it edits my own source, so "
                    "it only runs when you arm it (AFON_CODE_SELF_IMPROVE_ENABLED=true).")
        t = TASKS.run(
            title=f"self-improve: {objective}",
            coro_factory=lambda on_progress: run_code_self_improve(
                objective, self._llm, self._registry, on_progress=on_progress),
            kind="work",
        )
        return (
            f"BACKGROUNDED: I'm improving that on a fresh branch now (task id {t.id}) — I'll test and "
            "commit locally, then leave it for your review; I won't push. Tell the owner you're on it."
        )

    def _refuse_catastrophic(self, user_text: str) -> str:
        """Record a catastrophic-command turn (the user request + a hard refusal) and return the
        refusal. No tool runs and the model is never called — deterministic safety on top of the
        protected-paths guard and the confirm tier."""
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": _CATASTROPHIC_REFUSAL})
        self._trim()
        logger.info("refused a catastrophic system command (deterministic safety guard)")
        return _CATASTROPHIC_REFUSAL

    # ---- main loop --------------------------------------------------------------------
    async def respond(self, user_text: str, on_progress: Callable | None = None) -> str:
        """Run one full turn (with tool calls) and return Afon's spoken reply text."""
        from afon.brain.metrics import METRICS
        METRICS.incr("turns")
        self._begin_turn(user_text)
        if _catastrophic(user_text):
            return self._refuse_catastrophic(user_text)
        self._immediate_ack(user_text, on_progress)
        plan = await self._prepare_turn(user_text)
        messages, turn_tools = plan.messages, plan.turn_tools
        narrowed, forced_name, force_first = plan.narrowed, plan.forced_name, plan.force_first
        multi_intent, work_intent = plan.multi_intent, plan.work_intent
        clause_plan = plan.clause_plan

        # B5-lite: a narrowed zero-arg read needs no model round-trip — fire it and answer, guaranteed.
        if narrowed and forced_name in _ZERO_ARG_READS:
            reply = await self._forced_zero_arg_read(messages, forced_name, on_progress)
            self._history.append({"role": "assistant", "content": reply})
            self._trim()
            self._spawn_review()
            return reply

        completion_pending = False   # force an extra pass to finish a multi-intent turn
        completion_done = False
        completion_passes = 0        # bounded by len(clause_plan) when a clause plan exists
        b4_retried = False           # B4: one fallback-model retry per turn when the primary dodges
        b5_escalated = False         # B5: one thinking-tier escalation per turn on a persistent dodge
        seen_tools: set[str] = set()
        # A compound request legitimately needs more passes than a single-intent one: each
        # outstanding clause costs a completion pass, and the old fixed budget ran out before
        # the last clause could fire — the turn ended having satisfied everything but the part
        # the user probably cared about most ("...and remember it").
        for i in range(self._max_tool_iters + len(clause_plan)):
            force_this = (i == 0 and force_first) or completion_pending
            completion_pending = False
            choice = "required" if force_this else "auto"
            # Tool-tier: on a forced tool pass, go STRAIGHT to the reliable fallback tool-caller (groq)
            # instead of the primary, which dodges ~half its forced calls. Conversational turns keep the
            # primary. This removes the wasted primary round-trip that B4 otherwise pays after a dodge.
            # EXCLUDES work_intent — work_on_task is a background-delegation meta-tool the primary handles
            # better (routing it to groq regressed Autonomy); tool-tier is for data/command tools.
            prefer_fb = force_this and settings.tool_turns_prefer_fallback and not work_intent
            try:
                msg = await self._llm.complete(messages, tools=turn_tools, tool_choice=choice,
                                               skip_primary=prefer_fb)
            except RuntimeError:
                # A model in the chain may reject forced tool_choice — retry this pass on "auto".
                if choice == "auto":
                    raise
                msg = await self._llm.complete(messages, tools=turn_tools, tool_choice="auto",
                                               skip_primary=prefer_fb)
            # B4: if a forced pass STILL fired no tool (the primary was used and dodged, or a rare
            # fallback dodge), retry ONCE past the primary onto the reliable tool-caller before giving up.
            # Skipped when we already started at the fallback (prefer_fb) — that retry would be identical.
            if (force_this and not getattr(msg, "tool_calls", None) and not b4_retried and not prefer_fb):
                b4_retried = True
                try:
                    alt = await self._llm.complete(messages, tools=turn_tools,
                                                   tool_choice="required", skip_primary=True)
                    if getattr(alt, "tool_calls", None):
                        logger.info(f"B4: primary dodged {forced_name}; fallback tool-caller fired it")
                        msg = alt
                except (RuntimeError, TypeError):
                    # RuntimeError: the fallback also rejected forcing. TypeError: an injected test-double
                    # LLM lacks the skip_primary kwarg (the real client always has it). Either way, no
                    # fallback available — fall through to the honest degrade.
                    pass
            # B5 thinking-tier: forced turn the fast models still dodged → escalate ONCE to a MiniMax
            # reasoning model before giving up. This is what rescues arg-bearing tools (set_reminder,
            # create_event, notion_create) that the non-thinking primary + groq miss.
            if force_this and not getattr(msg, "tool_calls", None) and not b5_escalated:
                b5_escalated = True
                alt = await self._escalate_thinking(messages, turn_tools)
                if alt is not None:
                    msg = alt
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                # Multi-intent completion: the user asked for >1 thing but only ONE tool ran. Give the
                # model exactly one forced pass to satisfy the remaining part before ending (Roadmap 4.2).
                # A clause plan tells us exactly what is still outstanding, so completion is
                # allowed until every clause has fired (bounded by the plan's length and by
                # _max_tool_iters). Without a plan we keep the original single extra pass.
                _outstanding = [n for n in clause_plan if n not in seen_tools]
                _may_complete = (
                    (bool(_outstanding) and completion_passes < len(clause_plan))
                    or (not clause_plan and len(seen_tools) == 1 and not completion_done)
                )
                if multi_intent and _may_complete:
                    completion_done = True
                    completion_passes += 1
                    completion_pending = True
                    # B6: force the tool the clause router says is still outstanding, by name.
                    # The prose nudge below is the fallback for when we cannot name it.
                    turn_tools, clause_forced = _completion_force(clause_plan, seen_tools, turn_tools)
                    if clause_forced:
                        forced_name = clause_forced
                        narrowed = True
                        logger.info(f"B6 completion: forcing outstanding {clause_forced}")
                    else:
                        messages.append({"role": "system", "content": _MULTI_INTENT_COMPLETE})
                    continue
                reply = _clean_reply(msg.content or "")
                # B3: a narrowed+forced live-data/side-effect intent that fired NO tool means the model
                # dodged the call and is about to speak an unverified answer — degrade honestly. Knowledge
                # tools (define/recall) are exempt: the inline answer there is legitimate.
                if _should_degrade(narrowed, seen_tools, forced_name):
                    logger.info(f"B3 anti-fabrication: forced {forced_name} dodged (0 tools) — honest degrade")
                    reply = _FABRICATION_DEGRADE
                # Never persist an empty assistant turn: with no content AND no tool_calls it's an
                # invalid message that some upstream models reject (400) when the history is replayed
                # next turn. Fall back to a spoken acknowledgement instead.
                stored = reply or "Sorry sir, I didn't catch that — could you say it again?"
                self._history.append({"role": "assistant", "content": stored})
                self._trim()
                self._spawn_review()
                return reply or stored

            # Record the assistant's tool-call turn, then execute each call (shared with the
            # streaming path) — normalise the OpenAI objects to plain dicts first.
            calls = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
                for tc in tool_calls
            ]
            outcomes = await self._execute_calls(messages, calls, msg.content or "", on_progress)
            seen_tools.update(o["name"] for o in outcomes)
            # Short-circuit: a single speakable read-only result is spoken as-is (no summary pass).
            # Suppressed on multi-intent turns so a second pending intent still gets handled.
            direct = None if multi_intent else _direct_speakable(calls, outcomes)
            if direct is not None:
                self._history.append({"role": "assistant", "content": direct})
                self._trim()
                self._spawn_review()
                return direct

        # Tool-iteration budget exhausted — final no-tools pass so he speaks a summary, not
        # another tool call. The nudge steers models that would otherwise emit a raw tool-call.
        messages.append({
            "role": "system",
            "content": "Now reply to the owner in one or two spoken sentences, summarising what you "
                       "did and what you found. Do NOT call or write any tool calls.",
        })
        msg = await self._llm.complete(messages)
        reply = _clean_reply(msg.content or "") or "I've done what I can on that, sir."
        self._history.append({"role": "assistant", "content": reply})
        self._trim()
        self._spawn_review()
        return reply

    async def _escalate_thinking(self, messages: list[dict[str, Any]], turn_tools: list[dict[str, Any]]):
        """B5 thinking-tier: a forced tool turn the fast primary AND the reliable fallback both dodged is
        retried ONCE on a MiniMax reasoning model — a stronger tool-caller that fires the arg-bearing
        tools (set_reminder/create_event/notion_create) the fast models miss. Returns the message if it
        fired a tool, else None (fall through to honest degrade). Fail-quiet."""
        model = settings.llm_thinking_model
        if not model:
            return None
        try:
            alt = await self._llm.complete(messages, tools=turn_tools, tool_choice="required",
                                           prepend_model=model)
        except (RuntimeError, TypeError):
            return None
        if getattr(alt, "tool_calls", None):
            logger.info(f"B5 thinking-tier: {model} fired a tool the fast models dodged")
            return alt
        return None

    async def _forced_zero_arg_read(
        self, messages: list[dict[str, Any]], forced_name: str,
        on_progress: Callable | None,
    ) -> str:
        """Deterministically run a router-narrowed zero-arg read and return the spoken reply. The tool
        result is a natural sentence; speak it verbatim when short, else one no-tool summary pass for
        polish. Shared by both respond paths so the guarantee (the read always fires) is identical."""
        calls = [{"id": f"b5-{forced_name}", "name": forced_name, "arguments": "{}"}]
        outcomes = await self._execute_calls(messages, calls, "", on_progress)
        text = _clean_reply(outcomes[0]["result"]) if outcomes and outcomes[0]["ok"] else ""
        if text and len(text) <= _CHANNEL_DIRECT_MAX and not re.search(r"[A-Z]{4,}_[A-Z]{3,}", text):
            return text
        messages.append({
            "role": "system",
            "content": "Now tell the owner what you found, in one or two spoken sentences. "
                       "Do NOT call or write any tool calls.",
        })
        msg = await self._llm.complete(messages)
        return _clean_reply(msg.content or "") or text or "Here's what I found, sir."

    async def _face_second_factor(self, name: str, args: dict) -> str | None:
        """Return a refusal sentence if the camera shows the owner is NOT there, else None.

        Runs only for confirm-gated actions, and only after the yes has been given — so it costs a
        camera burst on the handful of calls that can actually lose something, not on every turn.

        Everything that is not a positive "he is not present" returns None. An unreachable camera, an
        unenrolled face, an offline laptop, a raised exception — all of those mean CAN'T TELL, and
        can't-tell must behave exactly as the system did before this existed. The alternative is an
        assistant that stops obeying its owner because a webcam is busy.
        """
        try:
            from afon.brain.tools.camera import verify_owner_present

            v = await verify_owner_present()
        except Exception as e:  # noqa: BLE001 — a broken check must never block the owner
            logger.warning(f"face second factor: skipped ({type(e).__name__}: {e})")
            return None
        if not v.get("available"):
            logger.info(f"face second factor: unavailable — {name} proceeds on the spoken yes alone")
            return None
        if v.get("matched"):
            logger.info(f"face second factor: owner verified for {name}")
            return None
        # Positive negative: the camera worked and he is not the one in front of it.
        faces = int(v.get("faces") or 0)
        logger.warning(f"confirm-gate: REFUSED {name}({args}) — camera saw {faces} face(s), none the owner")
        if faces == 0:
            return ("BLOCKED — the camera shows nobody at the desk, so that confirmation did not come "
                    "from the owner. Tell him you've held the action until he's back, and do NOT "
                    "claim it was done.")
        return ("BLOCKED — the camera shows someone at the desk who isn't the owner. Tell him the "
                "action is held pending his own confirmation, and do NOT claim it was done.")

    async def _execute_calls(
        self,
        messages: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        assistant_content: str = "",
        on_progress: Callable | None = None,
    ) -> list[dict[str, Any]]:
        """Append the assistant's tool-call turn, run each tool, append the results. Mutates
        ``messages`` and returns one ``{"name","result","ok"}`` outcome per call (in call order), so
        the caller can short-circuit a single speakable read-only result. Shared by both the blocking
        (``respond``) and streaming (``respond_stream``) paths so tool behaviour, error recovery, and
        the audit trail stay identical."""
        messages.append({
            "role": "assistant",
            "content": assistant_content or "",
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls
            ],
        })
        from afon.brain import audit
        from afon.brain.proactive import confirm_required
        # Pass 1 — classify each call: a confirm-gated/destructive call is BLOCKED until the owner has
        # affirmed it (we hand the model a sentinel so it reads the action back and asks). Everything
        # else is runnable and, since these are independent (separate tool calls in one model turn),
        # safe to run CONCURRENTLY — "time and weather" finishes in max(latency), not the sum.
        runnable: list[tuple[int, str, dict]] = []   # (call index, name, args)
        outcomes: list[dict[str, Any] | None] = [None] * len(calls)
        for idx, c in enumerate(calls):
            name = c["name"]
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if confirm_required(name, args) and not self._confirm_granted:
                self._pending_confirm = {"name": name, "args": args}
                blocked = (
                    "CONFIRM_REQUIRED — do NOT say this is done. In one sentence tell the owner "
                    "exactly what you're about to do (the action and its target or recipient) and "
                    "ask him to confirm. It will run only after he says yes."
                )
                logger.info(f"confirm-gate: held {name}({args}) pending the owner's yes")
                audit.record(name, args, "blocked: confirmation required", ok=False,
                             decision="deny", rule="confirm_gate:awaiting_owner_confirmation",
                             reasoning=f"{name} is in the confirm tier and the owner has not "
                                       f"affirmed this specific action")
                outcomes[idx] = {"name": name, "result": blocked, "ok": False,
                                 "args": args, "blocked": True}
                continue
            # I1 — SECOND FACTOR. The yes has been given; confirm it was given by someone actually
            # there. Only for gated actions, and only ever to add a refusal (see _face_second_factor).
            if confirm_required(name, args) and settings.face_second_factor:
                refusal = await self._face_second_factor(name, args)
                if refusal:
                    audit.record(name, args, "blocked: owner not visually present", ok=False,
                                 decision="deny", rule="face_second_factor:owner_not_present",
                                 reasoning="the confirmation was given but the camera could not "
                                           "place the owner in the room")
                    outcomes[idx] = {"name": name, "result": refusal, "ok": False,
                                     "args": args, "blocked": True}
                    continue
            # ACKNOWLEDGEMENT: announce what we're about to do BEFORE running the tool, always — so
            # Afon is never silently "working" (the Afon "Right away, sir — getting the time"
            # beat). Deterministic + instant (no LLM), and contextual from the args.
            if on_progress:
                on_progress(_ack_for(name, args))
            runnable.append((idx, name, args))

        # Pass 2 — execute the runnable calls. One call stays on the simple path; several fire
        # concurrently (each still watchdog-wrapped for "still on it" progress).
        if len(runnable) == 1:
            idx, name, args = runnable[0]
            outcomes[idx] = await self._run_one_tool(name, args, on_progress)
        elif runnable:
            results = await asyncio.gather(
                *(self._run_one_tool(name, args, on_progress) for _, name, args in runnable)
            )
            for (idx, _, _), out in zip(runnable, results):
                outcomes[idx] = out

        # Pass 3 — append tool results in original call order, record audit, consume any confirm grant.
        for c, out in zip(calls, outcomes):
            assert out is not None
            if not out.get("blocked"):  # blocked calls were already audited in pass 1
                # Why the call was PERMITTED, which is the question an incident review asks and
                # the old line could not answer: either the tool is outside the confirm tier, or
                # it is inside it and the owner affirmed this specific action.
                gated = confirm_required(out["name"], out["args"])
                audit.record(
                    out["name"], out["args"], str(out["result"]), ok=out["ok"],
                    decision="allow",
                    rule=("confirm_gate:owner_confirmed" if gated else "confirm_gate:not_gated"),
                    reasoning=("the owner affirmed this action before it ran" if gated
                               else f"{out['name']} is a read/low-risk tool outside the confirm tier"),
                )
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(out["result"])})
            # A confirmed consequential action consumes the grant: one "yes" authorises one action.
            if confirm_required(out["name"], out["args"]) and out["ok"]:
                self._confirm_granted = False
                self._pending_confirm = None
        return [{"name": o["name"], "result": str(o["result"]), "ok": o["ok"]} for o in outcomes]

    async def _run_one_tool(
        self, name: str, args: dict, on_progress: Callable | None
    ) -> dict[str, Any]:
        """Run a single tool under the slow-job watchdog; degrade (never crash) on error."""
        from afon.shared import errors as _err

        started = time.monotonic()
        detail = ""
        fn = self._registry.get(name)
        if fn is None:
            result, ok = f"unknown tool {name}", False
            detail = f"no handler registered for '{name}'"
        else:
            try:
                result, ok = await self._await_with_progress(fn(args), on_progress, name), True
            except Exception as e:  # noqa: BLE001 — degrade, don't die
                logger.exception(f"tool {name} raised")
                result = (f"That tool ({name}) hit an error: {type(e).__name__}. "
                          "Tell the owner briefly that it failed and carry on.")
                ok = False
                detail = f"{type(e).__name__}: {e}"
        # EVERY tool passes through here, so this one call gives all 133 (and every future one)
        # end-to-end tracking. It catches the soft failures that never raise — a tool returning
        # "isn't configured yet" or "I couldn't complete the…" looks like a normal answer to the
        # agent loop, which is how a dead integration can stay dead unnoticed. Arguments are kept
        # (scrubbed and clipped) because "which tool failed" is rarely enough to fix anything.
        soft = ok and _err.looks_failed(result)
        _err.record_op(
            "tool", name,
            ok=ok and not soft,
            detail=detail or (str(result)[:200] if soft else ""),
            duration_ms=(time.monotonic() - started) * 1000,
            slow_ms=float(settings.tool_slow_warn_seconds) * 1000 * 3,
            context={"args": str(args)[:200]},
        )
        logger.info(f"tool {name}({args}) -> {str(result)[:80]}")
        from afon.brain.metrics import METRICS
        METRICS.incr("tool_calls")
        METRICS.incr(f"tool.{name}")
        # ...and the same count durably: METRICS is in-memory, and the brain restarts daily, so
        # the "which tools does he actually use" question the catalogue tiering needs could never
        # accumulate an answer.
        from afon.brain.tool_usage import USAGE

        USAGE.record(name)
        if not ok:
            METRICS.incr("tool_errors")
        return {"name": name, "result": result, "ok": ok, "args": args}

    async def _await_with_progress(self, coro, on_progress: Callable | None, name: str = ""):
        """Await a tool coroutine but, if it runs long, speak periodic "still on it" updates so a
        slow tool (or a 20-minute fleet delegation) never goes silent. The tool keeps running; we
        only emit progress between checks. Re-raises the tool's exception unchanged."""
        task = asyncio.ensure_future(coro)
        interval = max(0.05, settings.tool_slow_warn_seconds)
        every = max(0.05, settings.tool_long_update_seconds)
        # Name the work in the updates ("the browser is taking longer…"), not just "it".
        label = _TOOL_LABELS.get(name, "that task")
        msgs = [
            f"{label.capitalize()} is taking a little longer than expected, sir — still on it.",
            f"Still working on {label}, sir.",
            f"Bear with me, sir — {label} is almost there.",
        ]
        i = 0
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task in done:
                return task.result()          # re-raises any tool exception to the caller
            if on_progress:
                on_progress(msgs[min(i, len(msgs) - 1)])
            i += 1
            interval = every

    def _immediate_ack(self, user_text: str, on_progress: Callable | None) -> None:
        """Speak an instant acknowledgement the moment a work-like request arrives — BEFORE the LLM
        even runs — so there's no dead air during the model's first-token latency. Only for requests
        that clearly mean work (a command or a tool-group trigger), so plain chatter stays snappy.

        Polished (G1): rotates a small phrase set (never the same line twice running) and STAYS SILENT
        when a specific per-tool ack is imminent — a router-narrowed zero-arg read fires its own
        "Checking your calendar, sir." near-instantly, so a generic "Right away" here would just
        double up. Non-instant turns still get the filler (real dead air during arg-extraction)."""
        if not (on_progress and settings.ack_before_tools):
            return
        if _is_pure_chat(user_text):
            return  # chatter now answers in ~0.5s (no tools) — a "Yes, sir." filler would just precede it
        from afon.brain.intent_router import forced_tools
        fn = forced_tools(user_text)
        if len(fn) == 1 and fn[0] in _ZERO_ARG_READS:
            return  # specific ack ("Checking your Telegram, sir.") lands in milliseconds — no double-up
        pool = _WORK_ACKS if (_wants_forced_tool(user_text) or groups_for_text(user_text)) else _CHAT_ACKS
        choices = [p for p in pool if p != getattr(self, "_last_ack", None)] or list(pool)
        line = random.choice(choices)
        self._last_ack = line
        on_progress(line)

    async def respond_stream(self, user_text: str, on_progress: Callable | None = None):
        """Streaming twin of ``respond``: yields spoken sentences AS they generate, so TTS can
        start on sentence 1 while the model is still writing. Resolves tool calls between passes
        exactly like ``respond``. Yields ``str`` chunks; persists the full reply to history.

        Thin guard around ``_respond_stream_impl``: if the stream is cancelled mid-turn (a barge-in
        or a superseding utterance closes the generator) or the turn raises, the user message would
        otherwise be left in history with no assistant reply — two consecutive user turns can 400 on
        the next replay (AUDIT #7). The ``finally`` records a short placeholder so that never happens.
        """
        self._stream_done = False
        try:
            async for chunk in self._respond_stream_impl(user_text, on_progress):
                yield chunk
        finally:
            if not self._stream_done:
                self._history.append({"role": "assistant", "content": "(interrupted)"})
                self._trim()

    async def _respond_stream_impl(self, user_text: str, on_progress: Callable | None = None):
        """Streaming body — see ``respond_stream`` for the cancellation guard."""
        self._begin_turn(user_text)
        if _catastrophic(user_text):
            self._refuse_catastrophic(user_text)   # records user + a hard refusal
            self._stream_done = True
            yield _CATASTROPHIC_REFUSAL
            return
        self._immediate_ack(user_text, on_progress)
        plan = await self._prepare_turn(user_text)
        messages, turn_tools = plan.messages, plan.turn_tools
        narrowed, forced_name, force_first = plan.narrowed, plan.forced_name, plan.force_first
        multi_intent, work_intent = plan.multi_intent, plan.work_intent
        clause_plan = plan.clause_plan
        spoken: list[str] = []

        def _finish(default: str) -> None:
            final = " ".join(spoken).strip() or default
            self._history.append({"role": "assistant", "content": final})
            self._trim()
            self._spawn_review()
            self._stream_done = True

        # B5-lite: a narrowed zero-arg read fires deterministically — no model round-trip to dodge.
        if narrowed and forced_name in _ZERO_ARG_READS:
            reply = await self._forced_zero_arg_read(messages, forced_name, on_progress)
            spoken.append(reply)
            yield reply
            _finish(reply)
            return

        completion_pending = False   # force an extra pass to finish a multi-intent turn
        completion_done = False
        completion_passes = 0        # bounded by len(clause_plan) when a clause plan exists
        b5_escalated = False         # B5: one thinking-tier escalation per turn on a persistent dodge
        seen_tools: set[str] = set()
        # A compound request legitimately needs more passes than a single-intent one: each
        # outstanding clause costs a completion pass, and the old fixed budget ran out before
        # the last clause could fire — the turn ended having satisfied everything but the part
        # the user probably cared about most ("...and remember it").
        for i in range(self._max_tool_iters + len(clause_plan)):
            force_this = (i == 0 and force_first) or completion_pending
            completion_pending = False
            choice = "required" if force_this else "auto"
            attempts = [choice] if choice == "auto" else [choice, "auto"]
            prefer_fb = force_this and settings.tool_turns_prefer_fallback and not work_intent  # tool-tier
            buf, calls, preamble = "", None, []
            for attempt in attempts:
                buf, calls, preamble = "", None, []
                try:
                    async for kind, payload in self._llm.stream_with_tools(
                        messages, tools=turn_tools, tool_choice=attempt, skip_primary=prefer_fb
                    ):
                        if kind == "text":
                            buf += payload
                            sents, buf = _pop_sentences(buf)
                            for s in sents:
                                cs = _clean_reply(s)
                                if cs:
                                    preamble.append(cs)
                                    spoken.append(cs)
                                    yield cs
                        else:  # ("tools", calls)
                            calls = payload
                    break  # this attempt succeeded
                except RuntimeError:
                    if attempt == "auto":
                        raise
                    continue  # forced tool_choice rejected by every model — retry on auto

            if not calls:
                # B5 thinking-tier: a forced turn the fast models dodged → escalate ONCE to a MiniMax
                # reasoning model. Only when nothing's been spoken yet, so we never double up on speech.
                if force_this and not b5_escalated and not spoken:
                    b5_escalated = True
                    alt = await self._escalate_thinking(messages, turn_tools)
                    if alt is not None and getattr(alt, "tool_calls", None):
                        calls = [{"id": tc.id, "name": tc.function.name,
                                  "arguments": tc.function.arguments or "{}"} for tc in alt.tool_calls]
            if not calls:
                # Multi-intent completion: only one tool ran but the user asked for >1 thing — force ONE
                # pass to finish the remaining part (e.g. remember it) before ending the turn.
                # A clause plan tells us exactly what is still outstanding, so completion is
                # allowed until every clause has fired (bounded by the plan's length and by
                # _max_tool_iters). Without a plan we keep the original single extra pass.
                _outstanding = [n for n in clause_plan if n not in seen_tools]
                _may_complete = (
                    (bool(_outstanding) and completion_passes < len(clause_plan))
                    or (not clause_plan and len(seen_tools) == 1 and not completion_done)
                )
                if multi_intent and _may_complete:
                    completion_done = True
                    completion_passes += 1
                    completion_pending = True
                    # B6: same named-forcing as the buffered path. Both loops call the one helper,
                    # so this behaviour cannot drift between response modes.
                    turn_tools, clause_forced = _completion_force(clause_plan, seen_tools, turn_tools)
                    if clause_forced:
                        forced_name = clause_forced
                        narrowed = True
                        logger.info(f"B6 completion: forcing outstanding {clause_forced}")
                    else:
                        messages.append({"role": "system", "content": _MULTI_INTENT_COMPLETE})
                    continue
                # B3: a narrowed+forced live-data intent that fired no tool → don't speak an unverified
                # tail (knowledge tools exempt — they may answer inline).
                if _should_degrade(narrowed, seen_tools, forced_name) and not spoken:
                    yield _FABRICATION_DEGRADE
                    spoken.append(_FABRICATION_DEGRADE)
                    _finish(_FABRICATION_DEGRADE)
                    return
                tail = _clean_reply(buf)
                if tail:
                    spoken.append(tail)
                    yield tail
                _finish("Sorry sir, I didn't catch that — could you say it again?")
                return

            outcomes = await self._execute_calls(messages, calls, " ".join(preamble), on_progress)
            seen_tools.update(o["name"] for o in outcomes)
            # Short-circuit: speak a single speakable read-only result directly (no summary pass).
            # Skip if the model already streamed prose this iteration, or this is a multi-intent turn.
            direct = None if multi_intent else _direct_speakable(calls, outcomes)
            if direct is not None and not preamble:
                spoken.append(direct)
                yield direct
                _finish(direct)
                return

        # Budget exhausted — one final no-tools summary pass, also streamed.
        messages.append({
            "role": "system",
            "content": "Now reply to the owner in one or two spoken sentences, summarising what you "
                       "did and what you found. Do NOT call or write any tool calls.",
        })
        buf = ""
        async for kind, payload in self._llm.stream_with_tools(messages):
            if kind == "text":
                buf += payload
                sents, buf = _pop_sentences(buf)
                for s in sents:
                    cs = _clean_reply(s)
                    if cs:
                        spoken.append(cs)
                        yield cs
        tail = _clean_reply(buf)
        if tail:
            spoken.append(tail)
            yield tail
        _finish("I've done what I can on that, sir.")

    def _trim(self) -> None:
        # Keep the last N turns (user+assistant pairs) to bound context.
        max_msgs = self._max_history_turns * 2
        if len(self._history) > max_msgs:
            self._history = self._history[-max_msgs:]
        self._persist_session()

    # ---- restart-durable working memory --------------------------------------------------
    def _session_path(self) -> Path:
        p = settings.session_persist_path
        return Path(p) if p else Path(__file__).resolve().parents[3] / "afon_session.json"

    def _persist_session(self) -> None:
        """Snapshot the rolling thread to disk so a restart resumes it. Best-effort, never raises."""
        try:
            self._session_path().write_text(
                json.dumps({"saved_at": time.time(), "history": self._history}),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001 — persistence must never break a turn
            logger.debug(f"session persist skipped: {e}")

    def _load_session(self) -> None:
        """Restore the thread on startup unless it's older than the idle-reset window (then drop it)."""
        path = self._session_path()
        try:
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            history = data.get("history") or []
            saved_at = float(data.get("saved_at") or 0)
            age_min = (time.time() - saved_at) / 60.0
            if not history:
                return
            if self._idle_reset_min and age_min >= self._idle_reset_min:
                logger.info(f"session snapshot expired ({age_min:.0f}m old) — starting fresh")
                path.unlink(missing_ok=True)
                return
            self._history = history
            self._last_turn_at = datetime.fromtimestamp(saved_at, USER_TZ)
            logger.info(f"resumed conversation thread: {len(history)} messages ({age_min:.0f}m old)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"session restore skipped: {e}")

    def _world_note(self) -> str | None:
        """G5 connectivity — surface FRESH integration events (webhooks/world-model) into the reactive
        turn so the owner asking 'anything new?' gets the Stripe payout / CI failure / reply that landed,
        not only the proactive loop. Usually empty (events are rare) → ~zero token cost. Fail-quiet."""
        try:
            from afon.brain.world_model import WORLD
            events = WORLD.recent_events()
        except Exception:  # noqa: BLE001
            return None
        if not events:
            return None
        return "Fresh updates from your integrations (mention only if relevant): " + "; ".join(events)

    def _relational_note(self, user_text: str) -> str | None:
        """Phase 6 — read the room. Infer the owner's affect from this utterance (6.1), log it into the
        relationship memory (6.2), and return one per-turn system note that adapts Afon's manner and
        surfaces mood-over-time / sensitivities / running jokes. Cheap (no LLM) and fail-quiet — reading
        the room must never break a turn."""
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from afon.brain.affect import infer_affect, manner_note
            from afon.brain.relationship import RELATIONSHIP

            now = datetime.now(ZoneInfo(settings.user_tz))
            affect = infer_affect(user_text, now=now)
            RELATIONSHIP.note_affect(affect.tags(), now=now)   # builds the mood-over-time log
            parts = [p for p in (manner_note(affect), RELATIONSHIP.render()) if p]
            return "\n".join(parts) or None
        except Exception as e:  # noqa: BLE001 — reading the room must never break a turn
            logger.debug(f"relational note skipped: {type(e).__name__}")
            return None

    async def _recall_note(self, user_text: str) -> str | None:
        """Fix #1 — auto-RAG. Retrieve memory relevant to THIS utterance and return a compact system
        note, so durable facts reach the model every turn without it having to call `recall`. Keyword
        L1 + L2 only (fast, local, free); the explicit recall tool still does the semantic + vault
        search. Skips trivial turns (too short, a bare affirmation) — nothing to ground there."""
        if not (settings.memory_enabled and settings.memory_autorecall_enabled):
            return None
        t = (user_text or "").strip()
        if len(t.split()) < settings.memory_autorecall_min_words or _is_affirmation(t):
            return None
        try:
            from afon.brain.memory import STORE

            hits = await STORE.fused_recall(t, limit=settings.memory_autorecall_limit,
                                            layers=("L1", "L2"))
        except Exception as e:  # noqa: BLE001 — recall must never break a turn
            logger.debug(f"auto-recall skipped: {type(e).__name__}: {e}")
            return None
        if not hits:
            return None
        tag = {"L1": "learned", "L2": "journal"}
        lines = [f"- [{tag.get(h['layer'], h['layer'])}] {h['text'].rstrip('.')}." for h in hits]
        return ("Relevant things you already know about the owner (from memory — draw on them only if "
                "pertinent; don't recite them):\n" + "\n".join(lines))

    def _spawn_review(self) -> None:
        """Self-improvement: every N turns, kick off a background pass that learns durable facts into
        L1 memory. Fire-and-forget so the reply is never delayed; a snapshot of history is passed so
        the review is stable even as the conversation moves on."""
        # T3b: log every user turn so pattern detection has data (cheap: one JSONL append).
        try:
            last_user = next((m for m in reversed(self._history)
                              if m.get("role") == "user" and m.get("content")), None)
            if last_user:
                from afon.brain.patterns import record
                record(last_user["content"])
        except Exception:
            pass
        if not self._self_improve:
            return
        self._turns_since_review += 1
        if self._turns_since_review < self._review_every:
            return
        self._turns_since_review = 0
        history = list(self._history)

        async def _run() -> None:
            from afon.brain.background_review import review_and_learn
            await review_and_learn(history, self._llm)

        try:
            asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass  # no running loop (sync context) — skip; end_session still reviews

    async def _journal_history(self, history: list[dict[str, Any]]) -> str | None:
        """Summarise a conversation to the L2 journal (+ distil L1 facts). Shared by end_session and
        the smart reset, so both paths preserve continuity the same way. Best-effort."""
        from afon.brain.memory import STORE

        user_turns = [m for m in history if m.get("role") == "user"]
        if len(user_turns) < 2:
            return None  # nothing worth journalling
        try:
            convo = "\n".join(f"{m['role']}: {m['content']}" for m in history if m.get("content"))
            msg = await self._llm.complete(
                [
                    {"role": "system", "content": "Summarise this conversation in ONE sentence for "
                     "the assistant's private journal: what the owner wanted, what was done, and any "
                     "open thread. Third person, past tense, no preamble."},
                    {"role": "user", "content": convo[:6000]},
                ],
                temperature=0.2,
            )
            summary = _clean_reply(msg.content or "")
            if summary:
                STORE.journal_append(summary)
            if self._self_improve:
                try:
                    from afon.brain.background_review import review_and_learn
                    await review_and_learn(list(history), self._llm)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"journal self-improve skipped: {e}")
            return summary or None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"journal skipped: {e}")
        return None

    async def end_session(self) -> str | None:
        """Summarise this conversation to the L2 journal for continuity. Best-effort."""
        return await self._journal_history(self._history)
