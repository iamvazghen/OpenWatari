"""LLM client for Afon's brain — freellmapi (OpenAI-compatible) with a fallback chain.

A single rate-limit or provider hiccup must never mute Afon, so every call walks
``settings.llm_chain`` (primary first, then ordered fallbacks) and returns the first model
that answers. Supports both plain streaming and OpenAI-style tool-calling.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from openai import APIError, APITimeoutError, RateLimitError

from afon.config import settings

# Errors that mean "this model is unavailable right now — try the next one".
_FAILOVER = (RateLimitError, APITimeoutError, APIError)

# A PERMANENT failure means "retrying this key WON'T help" — out of credits, unpaid/invalid key, or no
# access to the model. Unlike a transient 429/timeout, a dead-credit key benched for the short transient
# cooldown just flaps back into the chain seconds later and fails every turn. Matched on the error BODY
# (vendor error codes), NOT the HTTP status: a 429 is also plain rate-limiting and a 404 is also a wrong
# base_url — neither of those is permanent. Cues are verbatim vendor markers (OpenAI/Anthropic/Groq).
_PERMANENT_ERR_CUES = (
    "insufficient_quota", "exceeded your current quota", "credit balance is too low",
    "billing hard limit", "billing_hard_limit_reached", "model_not_found",
    "does not exist or you do not have access", "does not have access to model",
    "permission_error", "permission denied", "invalid api key", "invalid_api_key",
    "unauthorized", "account is not active", "account_deactivated",
)
_PERMANENT_COOLDOWN_S = 21600.0  # 6h — credits/access don't come back in the transient window

#: Every provider the chain can address: (name, base-url setting, api-key setting, key fallback).
#: One spelling, because `_resolve` and `provider_of` must never disagree about which provider an
#: entry belongs to — a second copy of this list is how a provider ends up benched under a name
#: nothing else uses, and the deprioritisation silently stops applying (J3.6).
_PROVIDERS: tuple[tuple[str, str, str | None, str], ...] = (
    ("groq", "groq_base_url", "groq_api_key", "missing-groq-key"),
    ("cerebras", "cerebras_base_url", "cerebras_api_key", "missing-cerebras-key"),
    ("minimax", "minimax_base_url", "minimax_api_key", "missing-minimax-key"),
    ("ollama", "ollama_base_url", None, "ollama"),          # Ollama ignores the key
    ("vercel", "vercel_ai_gateway_base_url", "vercel_ai_gateway_api_key", "missing-vercel-key"),
)
_DEFAULT_PROVIDER = "freellmapi"   # an unprefixed entry goes to the proxy


def provider_of(entry: str) -> str:
    """Which provider a chain entry is served by. `ollama:llama3:8b` is Ollama, not `llama3`."""
    head = entry.split(":", 1)[0]
    return head if any(head == p[0] for p in _PROVIDERS) else _DEFAULT_PROVIDER


# ── learned provider health (SYSTEMS.md 02.F3) ───────────────────────────────────────────────
# The model-level bench below is CONFIGURED health: one failure, one fixed cooldown, applied to
# the single chain entry that happened to fail. It learns nothing. When a provider's key is rate-
# limited or its region is down, every entry it serves is dead, and the chain discovers that one
# entry at a time — paying a failed round-trip for each, on every turn, forever.
#
# So failures are also counted per PROVIDER. Two inside five minutes and that provider is
# deprioritised: its entries move to the back of the chain rather than out of it, because a
# provider that is merely slow must still be reachable when the healthy ones are exhausted.
# Repeat offences double the cooldown (a provider that keeps failing has told you more each time),
# and one success clears the record — recovery has to be learnable too, or the first bad five
# minutes of the day would bench a provider until restart.
#
# Process-wide, like METRICS: there is one chain per brain, and the HUD has to be able to read
# this without holding a reference to whichever LLMClient the agent happens to own.
_PROVIDER_FAIL_WINDOW_S = 300.0     # "failed twice in five minutes"
_PROVIDER_FAIL_THRESHOLD = 2
_PROVIDER_COOLDOWN_S = 120.0
_PROVIDER_COOLDOWN_MAX_S = 1800.0   # 30m — past this it is an outage, and paging is the answer

_PROVIDER_FAILS: dict[str, list[float]] = {}
_PROVIDER_COOL: dict[str, float] = {}     # provider -> monotonic deadline
_PROVIDER_STRIKES: dict[str, int] = {}    # how many times it has earned a cooldown
_PROVIDER_LAST: dict[str, str] = {}       # provider -> why it was last deprioritised


def note_provider_failure(entry: str, permanent: bool = False, why: str = "") -> bool:
    """Record one failure. Returns True if this failure put the provider into a cooldown."""
    p = provider_of(entry)
    now = time.monotonic()
    fails = [t for t in _PROVIDER_FAILS.get(p, ()) if now - t <= _PROVIDER_FAIL_WINDOW_S]
    fails.append(now)
    _PROVIDER_FAILS[p] = fails
    if not permanent and len(fails) < _PROVIDER_FAIL_THRESHOLD:
        return False
    strikes = _PROVIDER_STRIKES[p] = _PROVIDER_STRIKES.get(p, 0) + 1
    cooldown = min(_PROVIDER_COOLDOWN_MAX_S, _PROVIDER_COOLDOWN_S * (2 ** (strikes - 1)))
    if permanent:
        cooldown = _PERMANENT_COOLDOWN_S
    _PROVIDER_COOL[p] = now + cooldown
    _PROVIDER_FAILS[p] = []   # the strike consumed them; don't re-trigger on the next failure
    _PROVIDER_LAST[p] = why[:120] or ("permanent failure" if permanent else
                                      f"{len(fails)} failures inside {_PROVIDER_FAIL_WINDOW_S:.0f}s")
    logger.warning(f"LLM provider '{p}' deprioritised for {cooldown:.0f}s "
                   f"(strike {strikes}): {_PROVIDER_LAST[p]}")
    return True


def note_provider_success(entry: str) -> None:
    """A provider that answers has earned its place back. Clears the record, and steps the strike
    count down rather than to zero — a provider that has failed all morning should not be treated
    as pristine because of one good answer."""
    p = provider_of(entry)
    _PROVIDER_FAILS.pop(p, None)
    _PROVIDER_COOL.pop(p, None)
    _PROVIDER_LAST.pop(p, None)
    if _PROVIDER_STRIKES.get(p):
        _PROVIDER_STRIKES[p] -= 1


def provider_cooling(entry: str) -> bool:
    return _PROVIDER_COOL.get(provider_of(entry), 0.0) > time.monotonic()


def provider_health() -> list[dict]:
    """What the HUD shows: every provider the chain can address, and whether it is trusted.

    Built from `_PROVIDERS` rather than from whichever providers happen to have failed, so a
    healthy provider is a row saying so — the same reason 31.F4's loop table is declared and not
    collected.
    """
    now = time.monotonic()
    out = []
    for name in [p[0] for p in _PROVIDERS] + [_DEFAULT_PROVIDER]:
        until = _PROVIDER_COOL.get(name, 0.0)
        out.append({
            "provider": name,
            "cooling": until > now,
            "cooling_for_s": round(max(0.0, until - now), 1),
            "recent_failures": len([t for t in _PROVIDER_FAILS.get(name, ())
                                    if now - t <= _PROVIDER_FAIL_WINDOW_S]),
            "strikes": _PROVIDER_STRIKES.get(name, 0),
            "why": _PROVIDER_LAST.get(name, ""),
        })
    return out


def reset_provider_health() -> None:
    """Forget everything learned. For tests and for a deliberate operator reset."""
    for d in (_PROVIDER_FAILS, _PROVIDER_COOL, _PROVIDER_STRIKES, _PROVIDER_LAST):
        d.clear()


def _is_permanent_error(err: Exception) -> bool:
    """True for a quota/credit/access failure that retrying won't fix (bench the model long, not the
    short transient cooldown, so the chain stops flapping onto a dead key). See ``_PERMANENT_ERR_CUES``."""
    return any(cue in str(err).lower() for cue in _PERMANENT_ERR_CUES)

# A weaker model in the chain sometimes emits a tool call AS TEXT — '<function name="...">',
# '<tool_code ...>', '<|python_tag|>…', 'print(default_api.x(...))' — instead of using the native
# tool-calling API. If we accept that as a final answer the TOOL NEVER RUNS and the raw tag gets
# spoken aloud. We detect it (in the message lead) and treat it as an empty response, so the chain
# fails over to a model that calls tools natively. Behavioral suite found this; it was the single
# biggest correctness drag (scrape/telegram/calendar/email all silently no-op'd).
_TEXTUAL_TOOLCALL_RE = re.compile(
    r"<\s*(function|tool_call|tool_code|tool_response|invoke)\b|</\s*function\b|"
    r"\bdefault_api\s*\.\w|print\s*\(\s*default_api|<\|\s*(python_tag|tool)",
    re.IGNORECASE,
)


def _looks_like_textual_toolcall(text: str | None) -> bool:
    t = (text or "").lstrip()
    return bool(t) and bool(_TEXTUAL_TOOLCALL_RE.search(t[:160]))


# MiniMax reasoning models (M2.7-highspeed, M3 — the paid primary chain) emit inline chain-of-thought
# wrapped in <think>...</think> BEFORE the real answer. No request param disables it (all tested), so
# we strip it: never speak the reasoning, never let it reach a tool parser. It always LEADS the content.
_THINK_BLOCK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.IGNORECASE | re.DOTALL)


def _strip_think(text: str | None) -> str | None:
    """Remove a leading <think>...</think> reasoning block from a whole (non-streamed) content string."""
    if not text:
        return text
    return _THINK_BLOCK_RE.sub("", text, count=1)


class _ThinkStripper:
    """Streaming counterpart of ``_strip_think``: withhold a leading <think>...</think> block that
    arrives split across chunks, then pass everything after </think> through unchanged. A model that
    never emits <think> (groq/gemini/proxy) streams through with only one chunk of buffering delay."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._state = "start"  # start -> thinking | pass

    def feed(self, chunk: str) -> str:
        if self._state == "pass":
            return chunk
        self._buf += chunk
        if self._state == "start":
            lead = self._buf.lstrip()
            if not lead:
                return ""                       # only whitespace so far — wait
            if lead.startswith(self._OPEN):
                self._state = "thinking"
                self._buf = lead                # drop leading whitespace before the tag
            elif self._OPEN.startswith(lead):
                return ""                       # ambiguous prefix like "<thi" — wait for more
            else:
                self._state = "pass"            # definitely not a think block — flush as-is
                out, self._buf = self._buf, ""
                return out
        if self._state == "thinking":           # hold until the block closes
            idx = self._buf.find(self._CLOSE)
            if idx == -1:
                return ""
            self._buf = self._buf[idx + len(self._CLOSE):]
            self._state = "trim"                # now drop the answer's leading whitespace (the \n\n)
        if self._state == "trim":
            stripped = self._buf.lstrip()
            self._buf = ""
            if not stripped:
                return ""                       # answer hasn't started yet — keep waiting
            self._state = "pass"
            return stripped
        return ""

    def flush(self) -> str:
        """End of stream: emit any held content that turned out NOT to be a think block."""
        return self._buf if self._state == "start" else ""


# groq + llama-3.3-70b intermittently emits a tool call in Llama's TEXT format —
# '<function=open_url{"url": "…"}</function>' — instead of the native JSON tool_calls, then returns
# 400 tool_use_failed with that text in `failed_generation`. The model picked the right tool with the
# right args; only the wire format is wrong. Parse it back so we keep the fast groq path instead of
# burning a ~1.5s failover to gemini on every tool turn.
_GROQ_FN_RE = re.compile(r"<\s*function\s*=\s*([\w.\-]+)\s*>?\s*(\{.*?\})\s*</\s*function\s*>", re.DOTALL)


def _recover_textual_toolcall(err: Exception):
    """Return a ChatCompletionMessage with native tool_calls recovered from a groq tool_use_failed
    error, or None if there's nothing to recover."""
    import json

    body = getattr(err, "body", None)
    gen = None
    if isinstance(body, dict):
        gen = body.get("failed_generation")
        if not gen and isinstance(body.get("error"), dict):
            gen = body["error"].get("failed_generation")
    if not gen:
        return None
    calls = []
    for m in _GROQ_FN_RE.finditer(gen):
        name, raw = m.group(1), m.group(2)
        try:
            args = json.dumps(json.loads(raw))  # validate + normalise the JSON args
        except (ValueError, TypeError):
            continue
        calls.append((name, args))
    if not calls:
        return None
    from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
    from openai.types.chat.chat_completion_message_tool_call import Function

    tcs = [
        ChatCompletionMessageToolCall(id=f"recovered_{i}", type="function",
                                      function=Function(name=n, arguments=a))
        for i, (n, a) in enumerate(calls)
    ]
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=tcs)


#: The wire markers for a fully exhausted failover chain. These are CONSTANTS because the sentence
#: is load-bearing outside this process: bench/run_all_tests.py reads a child's stdout and uses this
#: text to tell "no reachable model in this environment" (SKIP) from a real failure (FAIL). A
#: subprocess can only communicate in text, so the coupling cannot be removed — but it can be named,
#: and bench/test_llm_chain_exhausted.py asserts the harness and this module still agree (J7.2).
CHAIN_EXHAUSTED = "all LLM models failed"
VISION_CHAIN_EXHAUSTED = "all vision models failed"


class ChainExhausted(RuntimeError):
    """Every model in the failover chain refused — Afon has lost the ability to think, not just one call.

    A distinct type because three consumers must tell it apart from an ordinary call failure: the
    brain (apologise, do not retry into the same wall), the error journal (its own operation, with
    every model's error attached), and the test harness (SKIP, not FAIL).

    Subclasses ``RuntimeError`` deliberately — the same adoption trick as ``ToolResult(str)`` in
    J7.3. Every existing caller already catches ``RuntimeError`` or ``Exception``, so the type is
    purely additive: nothing changes behaviour, and callers that WANT the distinction can now ask
    for it by type instead of by reading the message.
    """


class _EmptyResponse(Exception):
    """A model returned 200 but with no usable choice (some proxies wrap errors in a 200)."""


class _SlowFirstToken(_EmptyResponse):
    """The model was reachable and healthy — it just hadn't produced a first token in time.

    Distinguished from a real failure because the right RESPONSE is different. A 4xx, a dead key or
    a connection reset means "this model is broken, stop asking for a while" (45s bench). A slow
    first token means "this ONE turn is taking too long, someone else can answer it faster" — and on
    the measured chain the fallback's TTFT is 0.18s against the primary's 0.74s median, so failing
    over is genuinely quicker than waiting.

    Benching the primary 45s for that would be wrong: the measured distribution is 9 turns in
    0.61-0.92s and one at 3.69s, so a tight deadline trips on roughly 1 turn in 10 — all healthy.
    At 45s each that quietly migrates a tenth of the day's traffic onto groq, which has a 100k
    token/day cap and cannot be the primary. Hence the much shorter bench below: a genuinely hung
    provider re-trips every turn and still gets skipped, while a one-off slow turn costs 5s.
    """


_SLOW_FIRST_TOKEN_COOLDOWN_S = 5.0


def _model_extra(model_name: str) -> dict[str, Any]:
    """Per-model request params. gpt-oss reasoning models (e.g. Cerebras gpt-oss-120b) otherwise
    spend the first ~2s 'thinking' with empty content — fatal for a voice turn. reasoning_effort=low
    makes them emit the spoken answer immediately (~0.26s). Only set it for models that accept it."""
    if "gpt-oss" in model_name:
        return {"reasoning_effort": "low"}
    return {}


class LLMClient:
    """OpenAI-compatible client with model failover + per-model provider routing.

    A chain entry is a model name optionally prefixed with a provider: ``groq:<model>`` hits Groq
    directly (api.groq.com, AFON_GROQ_API_KEY) for a fast, consistent primary; an unprefixed name
    goes to the freellmapi proxy. Failover walks ``settings.llm_chain``; streaming also fails over if
    no FIRST token arrives within ``llm_first_token_timeout_seconds`` (a slow/hung model -> fast
    recovery instead of a full-timeout stall)."""

    def __init__(self) -> None:
        self._timeout = settings.llm_request_timeout_seconds
        self._first_token_timeout = max(0.5, settings.llm_first_token_timeout_seconds)
        self._cooldown = max(0.0, settings.llm_unhealthy_cooldown_seconds)
        self._unhealthy_until: dict[str, float] = {}
        self.last_route: dict[str, Any] = {}
        self._clients: dict[str, AsyncOpenAI] = {}
        self._default = self._make_client(
            settings.freellmapi_base_url, settings.freellmapi_api_key or "freellmapi"
        )
        self._chain = settings.llm_chain

    def _make_client(self, base_url: str, api_key: str) -> AsyncOpenAI:
        return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=self._timeout, max_retries=0)

    def _resolve(self, entry: str) -> tuple[AsyncOpenAI, str]:
        """Map a chain entry to (client, model_name), honouring a ``provider:`` prefix.

        ``groq:<model>`` hits Groq directly; ``cerebras:<model>`` hits Cerebras directly (the fastest
        inference provider, ~2000 tok/s, separate rate-limit pool from Groq); ``minimax:<model>`` hits
        MiniMax directly (api.minimax.io — a paid reasoning model, independent of Groq's quota + the
        freellmapi proxy); ``ollama:<model>`` hits a LOCAL Ollama server (no key, true offline
        fallback); ``vercel:<model>`` hits the Vercel AI Gateway, the paid LAST-RESORT backstop that
        only answers once everything ahead of it has failed; unprefixed goes to the freellmapi proxy.
        Each provider's client is built once and cached."""
        for name, url_attr, key_attr, key_default in _PROVIDERS:
            prefix = f"{name}:"
            if entry.startswith(prefix):
                api_key = (getattr(settings, key_attr) if key_attr else None) or key_default
                client = self._clients.get(name)
                if client is None:
                    client = self._clients[name] = self._make_client(
                        getattr(settings, url_attr), api_key)
                return client, entry[len(prefix):]
        return self._default, entry

    @property
    def chain(self) -> list[str]:
        return self._chain

    def _candidate_chain(self) -> list[str]:
        """Return healthy entries first; if every entry is cooling down, try the full chain anyway."""
        if not self._cooldown:
            return self._deprioritise(list(self._chain))
        now = asyncio.get_running_loop().time()
        healthy = [m for m in self._chain if self._unhealthy_until.get(m, 0.0) <= now]
        if healthy:
            skipped = [m for m in self._chain if m not in healthy]
            if skipped:
                logger.debug(f"LLM skipping cooling-down models: {skipped}")
            return self._deprioritise(healthy)
        return self._deprioritise(list(self._chain))

    @staticmethod
    def _deprioritise(chain: list[str]) -> list[str]:
        """02.F3: entries from a learned-bad provider go to the BACK, never out.

        Removing them would be the wrong trade twice over — a provider that failed twice in five
        minutes is not proven dead, and a chain that can empty itself is a chain that can leave
        Afon mute. Python's sort is stable, so the healthy entries keep their configured order.
        """
        return sorted(chain, key=provider_cooling)

    def _mark_failure(self, model: str, err: Exception) -> None:
        from afon.brain.metrics import METRICS
        METRICS.incr("llm_model_failures")
        permanent = _is_permanent_error(err)
        # 02.F3: the provider learns even when the model-level cooldown is switched off, and even
        # on a slow-first-token failure — a provider that is slow twice in five minutes is a
        # provider to try second, which is exactly what a fixed per-model bench cannot express.
        if note_provider_failure(model, permanent, f"{type(err).__name__}: {err}"):
            METRICS.incr("llm_provider_cooldowns")
        # A permanent (quota/credit/access) failure benches the model even when the transient cooldown is
        # disabled — the whole point is to stop retrying a dead key. _candidate_chain still falls back to
        # the full chain if EVERY entry is benched, so this never locks Afon out of answering.
        if not self._cooldown and not permanent:
            return
        now = asyncio.get_running_loop().time()
        newly_benched = self._unhealthy_until.get(model, 0.0) <= now
        if permanent:
            cooldown = _PERMANENT_COOLDOWN_S
        elif isinstance(err, _SlowFirstToken):
            # Healthy but slow on this turn — see _SlowFirstToken. A full 45s bench here would
            # migrate ~1 turn in 10 onto the quota-capped fallback for no reason.
            cooldown = _SLOW_FIRST_TOKEN_COOLDOWN_S
        else:
            cooldown = self._cooldown
        self._unhealthy_until[model] = now + cooldown
        if permanent:
            METRICS.incr("llm_permanent_failures")
            logger.warning(
                f"LLM '{model}' PERMANENT failure (quota/credits/access) — benching {cooldown / 3600:.1f}h "
                f"so failover stops flapping onto a dead key: {str(err)[:120]}"
            )
            # The failover itself is automatic; what was MISSING is telling the owner. A dead paid key
            # otherwise degrades silently — the chain answers on fallbacks, the health probe stays green
            # (it probes the chain, not the primary), and nobody rotates the key for days. Page once per
            # bench (newly_benched gates the every-turn re-bench when the WHOLE chain is exhausted).
            if newly_benched:
                self._page_benched(model, err)
        else:
            logger.debug(f"LLM marked '{model}' unhealthy for {cooldown:.1f}s ({type(err).__name__})")

    def _page_benched(self, model: str, err: Exception) -> None:
        """Fire-and-forget owner page when a model is benched for a PERMANENT (quota/key) failure.
        Best-effort by design: paging must never delay or break the turn that triggered it."""
        async def _push() -> None:
            try:
                from afon.brain.tools.notify import push
                await push(
                    f"Heads-up, sir: LLM '{model}' failed permanently (quota or key — "
                    f"{str(err)[:80]}). I've benched it for 6 hours and I'm answering on the "
                    "fallback chain. The key likely needs attention.",
                    title="Afon — LLM benched",
                )
            except Exception:  # noqa: BLE001 — paging is best-effort
                pass
        try:
            asyncio.get_running_loop().create_task(_push())
        except RuntimeError:  # no running loop (sync/test context) — logging already covered it
            pass

    def _mark_success(
        self,
        model: str,
        failures: int,
        mode: str,
        latency_ms: float | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        self._unhealthy_until.pop(model, None)
        note_provider_success(model)   # 02.F3 — recovery is learned too, not waited out
        from afon.brain.metrics import METRICS
        METRICS.incr("llm_routes")
        if failures:
            METRICS.incr("llm_failovers")
        if latency_ms is not None:
            METRICS.observe("llm_route_ms", latency_ms)
            # The turn's model stage, measured from `route_started` so a failover's cost lands on
            # the turn that paid it. Only successful routes arrive here; a chain that fails
            # outright raises, and the trace records that turn as failed instead.
            from afon.brain.turn_trace import add_stage, note_model
            add_stage("llm", latency_ms)
            note_model(model)   # 02.R1 \u2014 a failover is charged to the model that answered
        self.last_route = {
            "mode": mode,
            "answered_by": model,
            "failed_over_count": failures,
            "latency_ms": latency_ms,
            "errors": errors or [],
        }
        logger.info(
            "LLM route: mode={} answered_by={} failed_over_count={} latency_ms={} errors={}",
            mode,
            model,
            failures,
            f"{latency_ms:.0f}" if latency_ms is not None else "n/a",
            errors or [],
        )

    async def warmup(self) -> None:
        """Pre-open a connection to EVERY distinct provider in the chain, so neither the first turn
        NOR the first failover pays a cold TLS/connection setup — a real, measurable slice of TTFT
        (fine-tuning.md Item 3 / docs/AUDIT.md). One tiny 1-token ping per distinct client, fired
        concurrently and best-effort: a provider that's down logs a debug line, never blocks startup.
        """
        seen: set[int] = set()
        pings = []
        for entry in self._chain:
            client, model_name = self._resolve(entry)
            if id(client) in seen:
                continue
            seen.add(id(client))
            pings.append(self._ping(client, model_name))
        if pings:
            await asyncio.gather(*pings, return_exceptions=True)

    async def _ping(self, client: AsyncOpenAI, model_name: str) -> None:
        try:
            await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=1,
            )
            logger.debug(f"warmup: primed '{model_name}'")
        except Exception as e:  # noqa: BLE001 — warmup is best-effort
            logger.debug(f"warmup ping for '{model_name}' skipped: {type(e).__name__}")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.6,
        tool_choice: str | dict[str, Any] = "auto",
        skip_primary: bool = False,
        prepend_model: str | None = None,
    ) -> Any:
        """Non-streaming completion (used for tool-calling turns). Returns the message.

        Walks the fallback chain on provider errors. Raises the last error if all fail.
        ``tool_choice`` is passed to the provider: "auto" (default), "required" (the model MUST
        call some tool — used to stop a weak model from *claiming* it acted without acting), or a
        ``{"type":"function","function":{"name": ...}}`` dict to force one specific tool.
        ``skip_primary`` starts the chain PAST the primary — used by the B4 retry when the primary
        (a non-thinking model) dodges a forced tool call, to reach a more reliable tool-caller.
        ``prepend_model`` tries a SPECIFIC model FIRST (then the normal chain as fallback) — used by the
        B5 thinking-tier escalation to reach a MiniMax reasoning model on a dodged forced turn.
        """
        last_err: Exception | None = None
        route_started = asyncio.get_running_loop().time()
        errors: list[dict[str, str]] = []
        failures = 0
        chain = self._candidate_chain()
        if skip_primary and len(chain) > 1:
            chain = chain[1:]
        if prepend_model:
            chain = [prepend_model] + [m for m in chain if m != prepend_model]
        for model in chain:
            client, model_name = self._resolve(model)
            try:
                kwargs: dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    **_model_extra(model_name),
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice
                resp = await client.chat.completions.create(**kwargs)
                if not getattr(resp, "choices", None):
                    # 200 with no choices = proxy/model error object — fail over, don't crash.
                    raise _EmptyResponse(getattr(resp, "error", None) or "empty choices")
                msg = resp.choices[0].message
                # Strip a MiniMax <think> block from the content before anything reads it (spoken text
                # or the textual-tool-call heuristic below). Tool calls come natively, untouched.
                if getattr(msg, "content", None):
                    msg.content = _strip_think(msg.content)
                # A 200 with a choice but NEITHER content NOR a tool call = the model said nothing
                # usable (congested free proxies do this intermittently). Treat it as a miss and fail
                # over to the next model rather than muting Afon with "I didn't catch that".
                if not (getattr(msg, "content", None) or getattr(msg, "tool_calls", None)):
                    raise _EmptyResponse("message had neither content nor tool_calls")
                # A textual tool-call (not a native one) means the tool wouldn't run — fail over to a
                # model that calls tools properly rather than speak the raw tag.
                if not getattr(msg, "tool_calls", None) and _looks_like_textual_toolcall(
                    getattr(msg, "content", None)
                ):
                    raise _EmptyResponse("textual tool-call instead of a native tool call")
                if model != self._chain[0]:
                    logger.warning(f"LLM primary unavailable; answered via fallback '{model}'")
                latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                self._mark_success(model, failures, "complete", latency_ms, errors)
                return msg
            except (*_FAILOVER, _EmptyResponse) as e:
                recovered = _recover_textual_toolcall(e)
                if recovered is not None:
                    names = [t.function.name for t in recovered.tool_calls]
                    logger.warning(f"LLM '{model}' returned a textual tool-call; recovered {names} "
                                   "from failed_generation (kept fast path, no failover)")
                    latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                    self._mark_success(model, failures, "complete", latency_ms, errors)
                    return recovered
                last_err = e
                failures += 1
                errors.append({"model": model, "error": type(e).__name__})
                self._mark_failure(model, e)
                logger.warning(f"LLM model '{model}' failed ({type(e).__name__}); trying next")
                continue
        # Total loss of the brain's ability to think — every model in the chain refused. The owner
        # experiences this as Afon going mute or apologising, so it is journalled as its own
        # operation with the whole chain's errors attached, not just the last one.
        from afon.shared import errors as _err

        _err.record_op("agentic", "llm_chain_exhausted", ok=False,
                       detail=f"all {failures} model(s) failed; last: {type(last_err).__name__}: {last_err}",
                       context={"errors": str(errors)[:200]})
        raise ChainExhausted(f"{CHAIN_EXHAUSTED}; last error: {last_err}")

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.6,
        tool_choice: str | dict[str, Any] = "auto",
        skip_primary: bool = False,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Stream a completion that may also call tools. Yields events:

        * ``("text", delta)``  — a chunk of spoken content as it generates (feed straight to TTS).
        * ``("tools", calls)`` — emitted once at the end if the model called tools; ``calls`` is a
          list of normalised ``{"id","name","arguments"}`` dicts (arguments is a JSON string).

        This is what lets the voice path speak the first sentence while the rest is still being
        generated. Fails over to the next model ONLY before the first token — once we've started
        yielding we never silently switch mid-utterance (that would double-speak).
        ``skip_primary`` starts the chain past the primary (tool-tier routing for forced turns).
        """
        last_err: Exception | None = None
        route_started = asyncio.get_running_loop().time()
        errors: list[dict[str, str]] = []
        failures = 0
        chain = self._candidate_chain()
        if skip_primary and len(chain) > 1:
            chain = chain[1:]
        for model in chain:
            client, model_name = self._resolve(model)
            tool_acc: dict[int, dict[str, str]] = {}
            got_any = False
            stripper = _ThinkStripper()  # withhold a MiniMax <think> block from the spoken stream
            # Lead-buffer classification: hold the first chunk of CONTENT until we can tell prose
            # from a textual tool-call. Prose flushes and streams normally; a textual tool-call is
            # withheld (never spoken) so got_any stays False -> the no-content failover kicks in.
            lead = ""
            lead_state = "buffering"  # buffering -> prose | toolcall
            try:
                kwargs: dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                    **_model_extra(model_name),
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice
                stream = await client.chat.completions.create(**kwargs)
                ait = stream.__aiter__()
                while True:
                    try:
                        # The FIRST token must arrive within the deadline (snappy failover off a
                        # slow/hung model); once we're streaming, later chunks aren't capped.
                        chunk = await asyncio.wait_for(
                            ait.__anext__(),
                            timeout=None if got_any else self._first_token_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as e:
                        raise _SlowFirstToken(
                            f"no first token within {self._first_token_timeout}s"
                        ) from e
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    raw = getattr(delta, "content", None)
                    piece = stripper.feed(raw) if raw else ""
                    if piece:
                        if lead_state == "buffering":
                            lead += piece
                            if _looks_like_textual_toolcall(lead):
                                lead_state = "toolcall"   # withhold; never speak the tag
                            elif len(lead) >= 24:
                                lead_state = "prose"
                                got_any = True
                                yield ("text", lead)
                        elif lead_state == "prose":
                            got_any = True
                            yield ("text", piece)
                        # lead_state == "toolcall": swallow content (no native call -> will fail over)
                    for tcd in getattr(delta, "tool_calls", None) or []:
                        got_any = True
                        slot = tool_acc.setdefault(tcd.index, {"id": "", "name": "", "arguments": ""})
                        if tcd.id:
                            slot["id"] = tcd.id
                        if tcd.function:
                            if tcd.function.name:
                                slot["name"] += tcd.function.name
                            if tcd.function.arguments:
                                slot["arguments"] += tcd.function.arguments
                tail = stripper.flush()  # held content that turned out not to be a think block
                if tail:
                    if lead_state == "buffering":
                        lead += tail
                    elif lead_state == "prose":
                        got_any = True
                        yield ("text", tail)
                # Stream ended mid-buffer: flush a short prose lead (e.g. "Yes, sir."). A withheld
                # textual tool-call is intentionally NOT flushed -> stays unspoken, fails over.
                if lead_state == "buffering" and lead and not _looks_like_textual_toolcall(lead):
                    got_any = True
                    yield ("text", lead)
                if not got_any:
                    raise _EmptyResponse("stream produced no content")
                if tool_acc:
                    yield ("tools", [tool_acc[i] for i in sorted(tool_acc)])
                if model != self._chain[0]:
                    logger.warning(f"LLM streaming via fallback '{model}'")
                latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                self._mark_success(model, failures, "stream_with_tools", latency_ms, errors)
                return
            except (*_FAILOVER, _EmptyResponse) as e:
                last_err = e
                # A mid-stream break while still buffering committed PROSE: flush it and treat as
                # already-speaking (don't fail over / double-speak). A withheld textual tool-call is
                # NOT flushed, so it still fails over to a native-tool-calling model.
                if lead_state == "buffering" and lead and not _looks_like_textual_toolcall(lead):
                    got_any = True
                    yield ("text", lead)
                if got_any:
                    # Already speaking — don't fail over and repeat; end the utterance here.
                    logger.warning(f"LLM stream '{model}' broke mid-utterance ({type(e).__name__})")
                    latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                    self._mark_success(model, failures, "stream_with_tools", latency_ms, errors)
                    return
                recovered = _recover_textual_toolcall(e)
                if recovered is not None:
                    calls = [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments}
                             for t in recovered.tool_calls]
                    logger.warning(f"LLM stream '{model}' returned a textual tool-call; recovered "
                                   f"{[c['name'] for c in calls]} from failed_generation (no failover)")
                    yield ("tools", calls)
                    latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                    self._mark_success(model, failures, "stream_with_tools", latency_ms, errors)
                    return
                failures += 1
                errors.append({"model": model, "error": type(e).__name__})
                self._mark_failure(model, e)
                logger.warning(f"LLM stream '{model}' failed ({type(e).__name__}); trying next")
                continue
        raise ChainExhausted(f"{CHAIN_EXHAUSTED}; last error: {last_err}")

    async def see(
        self,
        image_b64: str,
        prompt: str,
        *,
        chain: list[str] | None = None,
        max_tokens: int = 500,
    ) -> str:
        """Vision (Phase 3): describe/answer about a JPEG image (screen or camera frame).

        Walks ``settings.vision_chain`` — vision-capable, provider-prefixed models — and returns the
        first real answer. Image goes as an OpenAI-style ``image_url`` data URI. Raises RuntimeError
        only if EVERY vision model fails, so a caller can degrade gracefully (e.g. fall back to OCR).
        """
        chain = chain or settings.vision_chain
        if not chain:
            raise RuntimeError("no vision models configured (settings.vision_models)")
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
        messages = [{"role": "user", "content": content}]
        last_err: Exception | None = None
        for model in chain:
            client, model_name = self._resolve(model)
            try:
                resp = await client.chat.completions.create(
                    model=model_name, messages=messages, temperature=0.2, max_tokens=max_tokens,
                )
                if not getattr(resp, "choices", None):
                    raise _EmptyResponse("no choices")
                text = (resp.choices[0].message.content or "").strip()
                if not text:
                    raise _EmptyResponse("empty vision response")
                if model != chain[0]:
                    logger.warning(f"vision via fallback '{model}'")
                return text
            except Exception as e:  # noqa: BLE001 — try the next vision model
                last_err = e
                logger.warning(f"vision model '{model}' failed ({type(e).__name__}); trying next")
                continue
        raise ChainExhausted(f"{VISION_CHAIN_EXHAUSTED}; last error: {last_err}")

    async def stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.6,
    ) -> AsyncIterator[str]:
        """Stream text deltas from the first model that responds (no tools)."""
        last_err: Exception | None = None
        route_started = asyncio.get_running_loop().time()
        errors: list[dict[str, str]] = []
        failures = 0
        for model in self._candidate_chain():
            client, model_name = self._resolve(model)
            try:
                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    stream=True,
                    **_model_extra(model_name),
                )
                if model != self._chain[0]:
                    logger.warning(f"LLM streaming via fallback '{model}'")
                got_any = False
                stripper = _ThinkStripper()  # strip a MiniMax <think> block from the text stream
                # The FIRST token must arrive within the deadline, exactly as in stream_with_tools.
                # This path had NO deadline at all: a bare `async for chunk in stream`. It is the
                # PURE-CHAT path — the most common conversational turn — so a primary that accepted
                # the connection and then went quiet was waited on until the 60s request timeout
                # while the 0.18s fallback sat idle. The deadline lived only on the tool path, which
                # is the rarer one. Same construction, so the two paths cannot drift.
                ait = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            ait.__anext__(),
                            timeout=None if got_any else self._first_token_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as e:
                        raise _SlowFirstToken(
                            f"no first token within {self._first_token_timeout}s"
                        ) from e
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        piece = stripper.feed(delta)
                        if piece:
                            got_any = True
                            yield piece
                tail = stripper.flush()
                if tail:
                    got_any = True
                    yield tail
                if not got_any:
                    raise _EmptyResponse("stream produced no content")
                latency_ms = (asyncio.get_running_loop().time() - route_started) * 1000
                self._mark_success(model, failures, "stream", latency_ms, errors)
                return
            except (*_FAILOVER, _EmptyResponse) as e:
                last_err = e
                failures += 1
                errors.append({"model": model, "error": type(e).__name__})
                self._mark_failure(model, e)
                logger.warning(f"LLM stream '{model}' failed ({type(e).__name__}); trying next")
                continue
        raise ChainExhausted(f"{CHAIN_EXHAUSTED}; last error: {last_err}")
