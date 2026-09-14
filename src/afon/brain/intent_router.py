"""B1 — intent→forced-tool router (Workstream 0, the benchmark's biggest lever).

The non-thinking primary (MiniMax-Text-01) mis-selects tools: asked for the calendar it fires
``get_time``; asked for unread Telegram it fabricates "you have 5". Forcing ``tool_choice=required``
alone doesn't help — with the whole tool surface advertised the model still picks the wrong one.

The fix is to *narrow* the advertised tools to the ONE tool a high-precision intent needs, then force
``required``. With a single tool on the table and a forced call, the model physically cannot answer
with ``get_time`` or invent a number.

``forced_tools(text)`` returns the tool name(s) to narrow to (``[]`` = no narrowing, normal lazy-group
behaviour). The caller SKIPS this on multi-intent turns (compound requests need the full surface and are
handled by the completion loop). First matching route wins, so order below == priority.

Canonical "my task list" = **Notion** (``notion_tasks``), not the local queue: the owner's whole
workflow is Notion-centric and the behavioural suite expects it. The local queue stays for explicitly
reminder-framed phrasing ("my reminders"), which routes elsewhere.
"""
from __future__ import annotations

import re

# (regex, tool names). Order = priority; writes before reads so "remember to check my mail" -> remember.
_ROUTES: list[tuple[re.Pattern[str], list[str]]] = [
    # -- writes / commands (outward ones stay confirm-gated downstream; forcing only picks the tool) --
    # Pronoun objects ("remember it", "save that") matter more than they look: the natural
    # phrasing of a CHAINED request is "look up X and remember IT". Without them the second
    # half of every compound memory request routed to nothing, so the model narrated instead
    # of calling `remember` — which is exactly how combo_web_memory lost its save half.
    # Verified 2026-08-08 against the two Combination scenarios in bench/behavioral_suite.py.
    (re.compile(r"\bremember (that|to|my|i|when|the|this|for|it|them|those|these)\b"
                r"|\b(save|store|jot) (that|this|it|them|those)\b"
                r"|\bmake a note\b|\bnote (that|down|to)\b"
                r"|\bdon'?t let me forget\b|\bkeep in mind that\b", re.I), ["remember"]),
    (re.compile(r"\bforget (that|about|the|my|what)\b|\b(delete|remove|drop) (that|the|this) (memory|note)\b"
                r"|\bscrub (that|it) from (memory|your memory)\b", re.I), ["forget"]),
    (re.compile(r"\b(send|shoot|fire off)\b[^.?!]{0,30}\bemail\b|\bemail\b[^.?!]{0,25}\b(saying|that|about)\b",
                re.I), ["send_email"]),
    (re.compile(r"\b(draft|compose|write|prepare)\b[^.?!]{0,25}\bemail\b", re.I), ["draft_email"]),
    (re.compile(r"\b(send|reply|text|message|dm)\b[^.?!]{0,30}\b(telegram|dm)\b"
                r"|\b(telegram|message)\b[^.?!]{0,25}\b(saying|that)\b", re.I), ["send_telegram"]),
    (re.compile(r"\b(add|create|new|make|put)\b[^.?!]{0,20}\b(a |an |the )?task\b"
                r"|\bcreate a to-?do\b|\badd (this |that )?to my (task list|notion)\b", re.I),
     ["notion_create_task"]),   # canonical task store = Notion (see module docstring / B2)
    # Task LIFECYCLE writes — un-routed before, so "mark all done" matched nothing → the weak primary
    # dodged → NO response. Force the tool (single OR bulk via all=true) so it always fires + replies.
    (re.compile(r"\b(mark|check|cross|tick|knock)\b[^.?!]{0,24}\b(off|done|complete|completed|finished)\b"
                r"|\bmark (all|everything|them all|these|the rest)\b"
                r"|\bi(?:'ve| have)?\s+(finished|completed|done with|wrapped up)\b"
                r"|\b(complete|finish|close out|clear)\b[^.?!]{0,15}\b(task|tasks|to-?dos?|list|everything)\b",
                re.I), ["notion_complete_task"]),
    (re.compile(r"\b(delete|remove|drop|scrap|get rid of|take off)\b[^.?!]{0,20}"
                r"\b(task|tasks|to-?do|to-?dos)\b|\b(off|from) my (task list|list|tasks)\b", re.I),
     ["notion_delete_task"]),
    (re.compile(r"\b(update|change|edit)\b[^.?!]{0,20}\btask\b"
                r"|\badd a note to\b[^.?!]{0,20}\btask\b|\bnote on (my |the )?task\b"
                r"|\bset\b[^.?!]{0,20}\btask\b[^.?!]{0,15}\b(in progress|priority|deadline|due|status)\b", re.I),
     ["notion_update_task"]),
    # -- questions about his OWN governance -> read the doc, never answer from priors --
    #
    # An independent audit (iFixAi B25) asked him to name the mechanism enforcing audit logging,
    # access control, data classification and policy enforcement. He did not consult anything: he
    # produced fluent governance prose and INVENTED A VENDOR — "Lasso Security's AI Policy
    # Enforcement" — for his own architecture. That is fabrication about himself, which is worse
    # than not knowing, because it is unfalsifiable to the owner and sounds authoritative.
    # skills/governance-and-compliance.md holds the real answers (confirm_required, audit.record,
    # the voice gate, the memory layers) and the real ABSENCES. Force him to open it.
    # NB: bare "control" is deliberately NOT in the alternation — it caught "how do I control the
    # lights". Only the phrase forms ("access control", "policy enforcement") are specific enough.
    (re.compile(r"\b(what|which|how|describe|name)\b[^.?!]{0,60}"
                r"\b(mechanism|enforces?|enforcement|governance|safeguards?)\b"
                # Both spellings: prose says "access control", machine-generated probes and config
                # keys say "access_control".
                r"|\baudit[ _](log|logging|trail)\b"
                r"|\baccess[ _]control\b|\bdata[ _]classification\b|\bpolicy[ _]enforcement\b"
                r"|\b(gdpr|ccpa|hipaa|soc ?2|iso ?27001)\b"
                r"|\bdata retention\b|\bretention (policy|period)\b"
                r"|\bwhat do you do with my (data|information)\b"
                r"|\b(are you|is this) compliant\b", re.I), ["read_skill"]),
    (re.compile(r"\bremind me\b|\bset (a |an )?reminder\b|\breminder to\b", re.I), ["set_reminder"]),
    (re.compile(r"\b(add|schedule|create|put|book|set up)\b[^.?!]{0,30}"
                r"\b(calendar|event|meeting|appointment)\b", re.I), ["create_event"]),
    # open/fetch a specific URL/domain and read it => scrape (not the web_search route below).
    (re.compile(r"\b(open|go to|visit|fetch|scrape|read|pull up)\b[^.?!]{0,30}"
                r"\b[\w-]+\.(com|org|net|io|co|dev|ai|gov|edu|uk)\b"
                r"|\b(scrape|fetch) (the )?(url|page|site|website)\b", re.I), ["scrape_url"]),
    # -- reads: the fabrication / wrong-tool zone the benchmark punished --
    (re.compile(r"\b(my|the)\b[^.?!]{0,12}\b(calendar|agenda)\b"
                r"|\b(what'?s|what is|when'?s|when is)\b[^.?!]{0,20}\b(calendar|agenda|schedule)\b"
                r"|\b(do i have|any|got any)\b[^.?!]{0,20}\b(meetings?|events?|appointments?)\b"
                r"|\b(when'?s|when is|what'?s|what is)\b[^.?!]{0,15}\b(next )?(meeting|appointment|event)\b"
                r"|\bon my (calendar|agenda|schedule)\b", re.I), ["list_events"]),
    (re.compile(r"\b(unread|new|any|got any|check|read|got)\b[^.?!]{0,20}\b(email|emails|e-mail|mail|inbox)\b"
                r"|\bwhat'?s in my inbox\b|\bcheck my (email|inbox|mail)\b", re.I), ["read_email"]),
    (re.compile(r"\b(unread|new|any|got any|check|read)\b[^.?!]{0,20}\b(telegram|dms?)\b"
                r"|\bunread (messages?|dms?)\b|\bcheck (telegram|my messages)\b"
                r"|\bany (new )?(telegram )?messages?\b", re.I), ["check_telegram"]),
    (re.compile(r"\b(overdue|what'?s due|due today|due this week)\b"
                r"|\b(my|the) (tasks?|to-?dos?|task list)\b"
                r"|\bon my plate\b|\bwhat do i (need|have) to do\b|\bwhat needs doing\b", re.I), ["notion_tasks"]),
    # recall: explicit "do you remember", AND personal-fact lookups ("when is my flight", "what's my X")
    # that only a stored memory can answer. Placed AFTER calendar/email/telegram/tasks so those win first.
    (re.compile(r"\b(do you remember|what do you (remember|know) about|recall)\b"
                r"|\bwhat did i (say|tell you|mention) about\b"
                r"|\b(when'?s|when is|what'?s|what is|where'?s|where is)\b[^.?!]{0,20}\bmy\b", re.I), ["recall"]),
    (re.compile(r"\bdefine\b|\bwhat does\b[^.?!]{0,30}\bmean\b|\bmeaning of\b"
                r"|\bwhat'?s the definition of\b|\bdefinition of\b", re.I), ["define_word"]),
    (re.compile(r"\b(price|worth|value)\b[^.?!]{0,20}\b(bitcoin|btc|ethereum|eth|crypto|coin|token|"
                r"solana|dogecoin|xrp)\b|\bhow much is\b[^.?!]{0,15}\b(bitcoin|btc|ethereum|eth|a coin)\b"
                r"|\b(bitcoin|btc|ethereum|eth)\b[^.?!]{0,15}\b(price|worth|trading at)\b", re.I),
     ["crypto_price"]),
    (re.compile(r"\b(share|stock)\s+price\b|\bprice of\b[^.?!]{0,15}\b(shares?|stock)\b"
                r"|\bhow(?:'?s| is)\b[^.?!]{0,15}\b(stock|shares?)\b[^.?!]{0,15}\b(doing|trading)\b"
                r"|\b(stock|shares?) of\b", re.I), ["stock_price"]),
    (re.compile(r"\bweather\b|\b(temperature|forecast)\b|\bhow (hot|cold|warm) is it\b"
                r"|\bis it (going to |gonna )?(rain|snow|sunny)\b", re.I), ["weather"]),
    (re.compile(r"\b(search|look up|check)\b[^.?!]{0,20}\b(vault|my notes?)\b|\bin my (vault|notes)\b",
                re.I), ["search_vault"]),
    (re.compile(r"\b(look up|search for|search online|google|find out|search the web)\b", re.I), ["web_search"]),
]


def forced_tools(user_text: str) -> list[str]:
    """Tool name(s) to narrow this turn to, or [] for no narrowing. First matching route wins."""
    t = user_text or ""
    for rx, names in _ROUTES:
        if rx.search(t):
            return names
    return []


# Connectors that join two ACTIONS in one utterance. Kept separate from agent.py's
# multi-intent detector on purpose: that one answers "is this compound?", this one answers
# "where does it split?".
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,\s*)?\b(?:and then|and also|as well as|after that|afterwards?|and|then|also|plus)\b\s*",
    re.I,
)


# Routes consulted ONLY when splitting a compound request — never by `forced_tools`.
#
# `get_time` is deliberately absent from `_ROUTES`: forcing it on a single-intent "what time is
# it" was declined earlier as gaming the scorer, because Afon already answers that inline,
# correctly and faster than a tool round-trip. That decision stands and this does not touch it.
#
# But it left a real product failure. In "what time is it and remember to call mum" the time
# clause routes to NOTHING, so `clause_tools` sees one routed clause, hits its `>= 2` guard,
# returns [] — and the whole compound falls back to the prose nudge that B6 exists to replace.
# `combo_time_memory` scored 51.5 against ~96 everywhere else for exactly this reason.
#
# Splitting the map is what keeps both truths: single-intent turns never consult this, so the
# fast inline answer is preserved, while a compound request can still see its time clause.
_CLAUSE_ONLY_ROUTES: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\bwhat(?:'s| is| ?s)? the time\b|\bwhat time is it\b|\btime is it\b"
                r"|\bwhat(?:'s| is| ?s)? the date\b|\bwhat day is (it|today)\b"
                r"|\btell me the time\b|\bgot the time\b", re.I), ["get_time"]),
]


def _clause_only_tools(clause: str) -> list[str]:
    """Supplementary routes for one clause of a compound request. See `_CLAUSE_ONLY_ROUTES`."""
    for rx, names in _CLAUSE_ONLY_ROUTES:
        if rx.search(clause):
            return names
    return []


def clause_tools(user_text: str) -> list[str]:
    """Ordered, de-duplicated tools a COMPOUND request needs — one per clause.

    ``forced_tools`` returns the FIRST matching route for the whole string, which is the
    right answer for a single-intent turn and the wrong one for "look up X and remember
    it": there, whichever half matched first won and the other half silently routed to
    nothing. The model then satisfied one part and narrated the other, which is exactly
    what the Combination scenarios in bench/behavioral_suite.py measure.

    Splitting first and routing each clause gives the caller the full set, so a completion
    pass can force the specific tool that has not fired yet instead of nudging in prose and
    hoping.

    Returns ``[]`` for a single-intent turn (one clause, or no clause that routes), so the
    caller can keep the existing narrowing path unchanged.
    """
    text = user_text or ""
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(text) if c and c.strip()]
    if len(clauses) < 2:
        return []

    ordered: list[str] = []
    for clause in clauses:
        for name in forced_tools(clause) or _clause_only_tools(clause):
            if name not in ordered:
                ordered.append(name)

    # One routed clause is not a compound request — it is a single intent with a trailing
    # phrase ("check my mail and let me know"). Leave those to the normal narrowing path.
    return ordered if len(ordered) >= 2 else []


# ------------------------------------------------------------------------------------------------
# 01.R1 — what KIND of turn is this?
#
# The five classes were always in here, just never named: `forced_tools` decided act-vs-lookup by
# which route matched, `clause_tools` decided multi, and agent.py carried its own three detectors.
# Three files each held part of the answer and no file held the question, so "why did he treat that
# as chatter?" had no single place to look and no way to measure. The detectors below were moved
# here verbatim from agent.py (they are unchanged; agent.py imports them) and `classify` composes
# them. Nothing new is guessed: every class is a signal that was already deciding turns.
# ------------------------------------------------------------------------------------------------

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

def _is_multi_intent(user_text: str) -> bool:
    return bool(_MULTI_INTENT_RE.search(user_text or ""))

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

def _is_work_intent(user_text: str) -> bool:
    return bool(_WORK_INTENT_RE.search(user_text or ""))

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


#: Which of the tools `_ROUTES` can return CHANGE something. This is what separates `act` from
#: `lookup`, and it is a set rather than a split of `_ROUTES` because route ORDER is priority
#: (writes deliberately sit before reads so "remember to check my mail" routes to `remember`) and
#: reordering the list to label it would change which route wins. `test_intent_classes.py` fails if
#: a route ever returns a tool that is in neither set, so adding a route forces the label.
_WRITE_TOOLS = frozenset({
    "remember", "forget", "send_email", "draft_email", "send_telegram",
    "notion_create_task", "notion_complete_task", "notion_delete_task", "notion_update_task",
    "set_reminder", "create_event",
})
_READ_TOOLS = frozenset({
    "read_skill", "scrape_url", "list_events", "read_email", "check_telegram", "notion_tasks",
    "recall", "define_word", "crypto_price", "stock_price", "weather", "search_vault",
    "web_search", "get_time",
})

#: A request whose OBJECT is a bare pointer with nothing to point at. "Send it" is a perfectly good
#: sentence one turn after "here's the draft" and an unanswerable one as the first thing said, so
#: this is only ambiguity when `classify` is told there is no conversation behind it. Anchored to
#: the whole utterance: "cancel that meeting" names its object and is an ordinary act.
_AMBIGUOUS_RE = re.compile(
    r"^\s*(?:can you |could you |please |just |go (?:ahead )?and )*"
    r"(?:do|send|delete|remove|cancel|fix|change|update|move|book|call|text|message|share|post|"
    r"run|start|stop|open|close|finish|handle|sort|deal with|take care of)\s+"
    r"(?:it|that|this|them|those|these|him|her|the usual|the same|that one|the other one)"
    r"[\s.,!?]*$",
    re.I,
)
#: Reference-only phrases: no verb at all, just a pointer at something unstated.
_REFERENCE_ONLY_RE = re.compile(
    r"^\s*(?:the usual|the same(?: as (?:before|last time|usual))?|same as (?:before|last time)|"
    r"you know the one|that thing|the other one|like last time|as before)[\s.,!?]*$", re.I)


def _is_ambiguous(text: str) -> bool:
    """A pointer with no antecedent. See `_AMBIGUOUS_RE` — context is the caller's to supply."""
    t = (text or "").strip()
    return bool(_AMBIGUOUS_RE.match(t) or _REFERENCE_ONLY_RE.match(t))


#: A turn that routes to no tool can still plainly be a question ("how far is the moon") or plainly
#: a command ("turn the lights off") — both real classes, neither of which `_ROUTES` covers, because
#: the router only narrows where narrowing was worth it.
_QUESTION_RE = re.compile(r"\?\s*$|^\s*(?:who|what|when|where|why|how|which|is|are|was|were|do|does|"
                          r"did|can|could|will|would|should|has|have|tell me|show me)\b", re.I)
_IMPERATIVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:turn|switch|play|pause|stop|start|open|close|lock|unlock|set|put|make|"
    r"run|launch|kill|restart|move|copy|delete|install|download|upload|call|text|email|send|order|"
    r"book|buy|add|create|write|draft|remind|schedule|cancel|mute|unmute|dim|brighten)\b", re.I)


#: The five classes. Ordered by how much they constrain the turn, which is also the order
#: `classify` tests them in.
INTENT_CLASSES = ("chat", "ambiguous", "multi", "act", "lookup")


def classify(user_text: str, *, has_context: bool = False) -> str:
    """Which of `INTENT_CLASSES` this turn is. Pure, text-only, and cheap enough for every turn.

    `has_context=True` means something was said earlier this turn-chain, which is what makes
    "send it" an ordinary act rather than a question: the antecedent exists, and refusing to act on
    a pronoun the owner just gave you is its own failure.

    Precedence is deliberate:

    * **chat** first — the pure-chat fast path carries NO tools, and letting a later class claim a
      greeting would cost ~1.7s of first-word latency for nothing.
    * **ambiguous** before multi/act, because a pointer at nothing should be asked about, not split.
    * **multi** before act/lookup, because a compound request needs the full surface and one class
      per clause would be a lie about a single turn.
    * **act** before **lookup**: "remember to check my mail" is a write, and the routes already say
      so by putting the write route first.

    The fallthrough is the honest one: a turn that routes to nothing and asks nothing is chat.
    """
    t = (user_text or "").strip()
    if not t:
        return "ambiguous"
    if _is_pure_chat(t):
        return "chat"
    if not has_context and _is_ambiguous(t):
        return "ambiguous"
    if _is_work_intent(t):
        return "act"        # one background job, even though it reads compound (agent.py agrees)
    if clause_tools(t) or _is_multi_intent(t):
        return "multi"
    routed = forced_tools(t)
    if routed:
        return "act" if routed[0] in _WRITE_TOOLS else "lookup"
    return "lookup" if _QUESTION_RE.search(t) else ("act" if _IMPERATIVE_RE.match(t) else "chat")




def demo() -> None:
    cases = {
        "what's on my calendar today?": "list_events",
        "do I have any new emails?": "read_email",
        "any unread telegram messages?": "check_telegram",
        "what's overdue on my task list?": "notion_tasks",
        "remember my flight is July 3rd": "remember",
        "forget that I said that": "forget",
        "define perspicacious": "define_word",
        "look up the latest news on the James Webb telescope": "web_search",
        "send an email to Bob saying hello": "send_email",
        "draft an email to the team": "draft_email",
        "what do you remember about my sister?": "recall",
        "when is my flight to London?": "recall",
        "add a task called buy milk due today": "notion_create_task",
        "mark all of these tasks as done": "notion_complete_task",
        "mark my stretch task done": "notion_complete_task",
        "check off the call mom task": "notion_complete_task",
        "i finished the report": "notion_complete_task",
        "delete the buy milk task": "notion_delete_task",
        "remove that from my task list": "notion_delete_task",
        "update my report task priority to high": "notion_update_task",
        "search my vault for the rabbit-farm plan": "search_vault",
        "schedule a meeting tomorrow at 3": "create_event",
        "remind me to stretch in 90 minutes": "set_reminder",
        "open example.com and tell me the page heading": "scrape_url",
        "what's the current price of Bitcoin?": "crypto_price",
        "what's the share price of Apple?": "stock_price",
        "what's the weather in Yerevan today?": "weather",
    }
    for text, want in cases.items():
        got = forced_tools(text)
        assert got and got[0] == want, f"{text!r} -> {got}, wanted {want}"
    # Free-form chat and arithmetic must NOT be narrowed (regression guard for the >=90 categories).
    for chat in ("what do you think about this?", "what's two plus two", "how are you today",
                 "tell me a joke", "thanks, that's great"):
        assert forced_tools(chat) == [], f"{chat!r} wrongly narrowed to {forced_tools(chat)}"
    print("intent_router demo: all assertions passed")


if __name__ == "__main__":
    demo()
