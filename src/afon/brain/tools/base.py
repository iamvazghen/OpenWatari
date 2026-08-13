"""Shared helpers for Afon's Phase 3 tools.

Every tool follows one rule: **never crash the brain on a missing integration**. If a
tool's credentials aren't set, its handler returns a short, plain-English note (via
``not_configured``) that Afon simply re-voices to the owner ("Web search isn't wired up
yet, sir — you'd need to add a Tavily key"). Errors are caught and returned the same way
(``tool_error``). Handlers therefore always return a ``str`` the LLM can speak.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import httpx
from loguru import logger

from afon.config import settings


class ErrorKind(str, Enum):
    """J7.3 — why a tool failed, as a value instead of a sentence to grep.

    Every handler returns speakable prose, which is right for the model and useless to a caller that
    has to DECIDE something: retry, fall back, page the owner, or stay quiet. Without this, deciding
    meant substring-matching English — which is how the LLM permanent-error classifier and the
    degradation tests both ended up built on prose.
    """

    NOT_CONFIGURED = "not_configured"   # the capability exists but has no credentials — never retry
    AUTH = "auth"                       # credentials present and rejected — retrying cannot help
    NETWORK = "network"                 # unreachable/timed out — a retry is reasonable
    RATE_LIMIT = "rate_limit"           # retry, but later and slower
    UNAVAILABLE = "unavailable"         # remote reachable and failing (5xx) — retry later
    NOT_FOUND = "not_found"             # the thing asked for is not there — retrying is pointless
    BAD_ARGS = "bad_args"               # WE called it wrong — a different call might work
    UNKNOWN = "unknown"


class ToolResult(str):
    """A tool result that is a plain ``str`` everywhere, and additionally carries ``kind``.

    A str SUBCLASS rather than a new return type on purpose: `tool_error` is the most connected node
    in the system (132 edges), every handler is contracted to return something the model can speak,
    and results are json-dumped across the edge/brain link. Subclassing changes none of that — the
    kind is simply there for the callers that ask, and absent (None) for everyone else.

    It does NOT survive the wire: JSON has no room for it, so anything crossing to the edge arrives
    as prose again. In-process callers get the type; the spoken contract is unchanged.
    """

    kind: "ErrorKind | None"

    def __new__(cls, text: str, kind: "ErrorKind | None" = None) -> "ToolResult":
        obj = super().__new__(cls, text)
        obj.kind = kind
        return obj


def classify_error(err: Exception) -> ErrorKind:
    """Map an exception to the taxonomy. Status codes first — they are the specific evidence."""
    if isinstance(err, httpx.HTTPStatusError):
        code = err.response.status_code
        if code in (401, 403):
            return ErrorKind.AUTH
        if code == 404:
            return ErrorKind.NOT_FOUND
        if code == 429:
            return ErrorKind.RATE_LIMIT
        if code >= 500:
            return ErrorKind.UNAVAILABLE
        if code >= 400:
            return ErrorKind.BAD_ARGS
    if isinstance(err, (httpx.TimeoutException, httpx.TransportError, ConnectionError,
                        TimeoutError, OSError)):
        return ErrorKind.NETWORK
    if isinstance(err, (ValueError, TypeError, KeyError, IndexError, AttributeError)):
        return ErrorKind.BAD_ARGS
    return ErrorKind.UNKNOWN


def not_configured(what: str, needs: str) -> ToolResult:
    """A speakable note that a capability exists but isn't set up yet."""
    return ToolResult(
        f"{what} isn't configured yet — it needs {needs}. Tell the owner and offer to help set it up.",
        ErrorKind.NOT_CONFIGURED)


def tool_error(what: str, err: Exception) -> ToolResult:
    kind = classify_error(err)
    logger.warning(f"tool '{what}' failed [{kind.value}]: {type(err).__name__}: {err}")
    return ToolResult(
        f"I couldn't complete the {what} just now ({type(err).__name__}). I'll let the owner know.",
        kind)


def missing_arg(tool: str, args: dict, *names: str, ask: str) -> str:
    """The one usable argument is absent: return an ERROR if the model used unknown keys, else ASK.

    A tool that answers a bad call with "Which URL, sir?" hands the model a fluent sentence, and the
    model speaks it as the reply — a fake answer with the tool never having run. That is exactly how
    `recall`'s "What should I recall, sir?" became a governance answer in the L3c audit.

    But asking is CORRECT when the owner genuinely left the detail out ("remind me" / "play
    something"), so a blanket sweep would trade one failure for another. The discriminator is which
    KEYS arrived, not whether a value is empty:

      * ``{}``                     -> the model relayed an underspecified request  -> ASK.
      * ``{"query": ""}``          -> right key, no content; still underspecified   -> ASK.
      * ``{"entity": …, "key": …}`` -> the model invented key names                  -> ERROR.

    Only the third case changes behaviour, and it is the documented bug: the intent was right and
    only the key was wrong, so an error lets the retry ladder fire instead of ending the turn on a
    sentence. ``names`` is the full set of accepted spellings, synonyms included.
    """
    if args and not (set(names) & set(args)):
        return tool_error(tool, ValueError(
            f"unknown argument(s) {sorted(args)}; expected one of {sorted(names)}"))
    return ask


# ponytail: handlers always return speakable prose and never raise, so a caller that consumes a
# tool result as DATA (proactive signals, digests) cannot tell "here are your events" from "I
# couldn't do that" — and will happily read the error aloud. `kind` (J7.3) answers this properly
# for anything built here; the substring markers remain the fallback for prose that arrives from
# somewhere else — across the wire, from an MCP server, or hand-written in a handler.
_NOT_CONFIGURED_MARKERS = ("isn't configured yet", "isn't configured", "not configured")
_ERROR_MARKERS = ("i couldn't complete the",)
_FAILURE_MARKERS = _ERROR_MARKERS + _NOT_CONFIGURED_MARKERS


def kind_of(result: object) -> ErrorKind | None:
    """The failure kind of a tool result, or None when it is data (or untyped prose)."""
    return getattr(result, "kind", None)


def tool_failed(result: str | None) -> bool:
    """True when a tool's string return is a failure / not-configured note rather than data."""
    if kind_of(result) is not None:
        return True
    if not result or not result.strip():
        return True
    low = result.lower()
    return any(m in low for m in _FAILURE_MARKERS)


def is_not_configured(result: str | None) -> bool:
    """True when a tool declined because a credential is missing (as opposed to erroring).

    Exists because the *contract* was centralised in ``not_configured()`` while the *assertions*
    were not: ten bench files each grepped the literal prose "isn't configured yet" for themselves,
    across 61 call sites. Rewording one sentence would have left every one of them passing while
    the guarantee they exist to protect was gone — a green suite over a broken degradation path.
    Callers ask this instead, so the wording lives in exactly one place.
    """
    if kind_of(result) is ErrorKind.NOT_CONFIGURED:
        return True
    if not result or kind_of(result) is not None:
        return False        # typed as some OTHER failure: definitively not a missing credential
    low = result.lower()
    return any(m in low for m in _NOT_CONFIGURED_MARKERS)


# A real User-Agent: some providers (e.g. Wikipedia) reject the default httpx UA with 403.
_USER_AGENT = "AfonAssistant/1.0 (+https://github.com/; personal voice assistant)"


def _client(**kw: Any) -> httpx.AsyncClient:
    kw.setdefault("timeout", settings.http_timeout_seconds)
    # Follow 3xx so a provider that moved hosts (e.g. an API that now 301s to a new domain)
    # still resolves instead of surfacing the redirect as an error.
    kw.setdefault("follow_redirects", True)
    headers = {"User-Agent": _USER_AGENT}
    headers.update(kw.pop("headers", None) or {})
    kw["headers"] = headers
    return httpx.AsyncClient(**kw)


async def http_get(url: str, **kw: Any) -> httpx.Response:
    async with _client() as c:
        r = await c.get(url, **kw)
        r.raise_for_status()
        return r


async def http_post(url: str, **kw: Any) -> httpx.Response:
    async with _client() as c:
        r = await c.post(url, **kw)
        r.raise_for_status()
        return r


async def http_patch(url: str, **kw: Any) -> httpx.Response:
    async with _client() as c:
        r = await c.patch(url, **kw)
        r.raise_for_status()
        return r


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …(truncated)"
