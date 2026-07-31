"""Shared helpers for Jarvis's Phase 3 tools.

Every tool follows one rule: **never crash the brain on a missing integration**. If a
tool's credentials aren't set, its handler returns a short, plain-English note (via
``not_configured``) that Jarvis simply re-voices to the owner ("Web search isn't wired up
yet, sir — you'd need to add a Tavily key"). Errors are caught and returned the same way
(``tool_error``). Handlers therefore always return a ``str`` the LLM can speak.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from jarvis.config import settings


def not_configured(what: str, needs: str) -> str:
    """A speakable note that a capability exists but isn't set up yet."""
    return f"{what} isn't configured yet — it needs {needs}. Tell the owner and offer to help set it up."


def tool_error(what: str, err: Exception) -> str:
    logger.warning(f"tool '{what}' failed: {type(err).__name__}: {err}")
    return f"I couldn't complete the {what} just now ({type(err).__name__}). I'll let the owner know."


# ponytail: handlers always return speakable prose and never raise, so a caller that consumes a
# tool result as DATA (proactive signals, digests) cannot tell "here are your events" from "I
# couldn't do that" — and will happily read the error aloud. Substring-match the two failure
# shapes produced above; if a third shape appears, add it here rather than teaching every caller
# to sniff strings.
_FAILURE_MARKERS = ("i couldn't complete the", "isn't configured yet")


def tool_failed(result: str | None) -> bool:
    """True when a tool's string return is a failure / not-configured note rather than data."""
    if not result or not result.strip():
        return True
    low = result.lower()
    return any(m in low for m in _FAILURE_MARKERS)


# A real User-Agent: some providers (e.g. Wikipedia) reject the default httpx UA with 403.
_USER_AGENT = "JarvisAssistant/1.0 (+https://github.com/; personal voice assistant)"


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
