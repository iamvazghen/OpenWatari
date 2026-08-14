"""Composio tool router — breadth via the Composio REST API with a TINY tool footprint (2 tools).

Composio exposes thousands of tools across 250+ OAuth-managed apps (the owner's connected accounts:
GitHub, Slack, Google Drive/Docs/Sheets, Linear, Stripe, Airtable, Maps, YouTube, …). Advertising them
all would obliterate the per-turn tool budget, so instead of raw tools Afon gets a two-step ROUTER:

  * ``composio_find_tools(query)`` — semantic search for the right tool across the CONNECTED apps;
    returns each candidate's slug + description + required arguments.
  * ``composio_run_tool(slug, arguments)`` — execute that tool. Writes (create/send/post/charge) are
    confirm-gated; reads (get/list/search) run directly (see ``proactive.confirm_required``).

Needs ``AFON_COMPOSIO_API_KEY``. Degrades to a spoken "not configured" note without it. Scoped to the
owner's ACTIVE connected toolkits so it never offers an app that isn't connected.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from loguru import logger

from afon.brain.tools.base import not_configured, tool_error
from afon.config import settings

_API = "https://backend.composio.dev/api/v3"
_NEEDS = "a Composio API key (AFON_COMPOSIO_API_KEY) and at least one connected app"

# Process-lifetime caches (resolved lazily on first use). `_context_resolved` is the memo key, and
# it is a separate flag ON PURPOSE (J3.8): memoising on the VALUES instead breaks in both directions.
# A failed lookup with a configured user id yields (uid, empty set) — indistinguishable from a real
# "no connected apps", so a network blip disabled toolkit scoping until the next restart. And a
# SUCCESSFUL lookup that resolves no user id never engages the memo at all, so every find_tools call
# re-hits /connected_accounts inside the turn. Memoise on success; retry on failure.
_user_id: str | None = None
_active_toolkits: set[str] | None = None
_context_resolved: bool = False

# Strong keyword -> toolkit slug. Only scopes when the toolkit is actually connected (checked at use);
# a miss just falls through to the global search + fan-out. Keep short — obvious 1:1 signals only.
_TOOLKIT_HINTS: dict[str, str] = {
    "email": "gmail", "gmail": "gmail", "slack": "slack", "spreadsheet": "googlesheets",
    "google sheet": "googlesheets", "google doc": "googledocs", "google drive": "googledrive",
    "linear": "linear", "github": "github", "stripe": "stripe", "airtable": "airtable",
    "youtube": "youtube", "reddit": "reddit", "linkedin": "linkedin", "instagram": "instagram",
    "coinbase": "coinbase", "supabase": "supabase", "google calendar": "googlecalendar",
}


def _configured() -> bool:
    return bool(settings.composio_api_key)


def _headers() -> dict:
    return {"x-api-key": settings.composio_api_key or ""}


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_API}{path}", headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(f"{_API}{path}", headers=_headers(), json=body)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return {"raw": r.text}


async def _context() -> tuple[str | None, set[str]]:
    """Resolve the owner's Composio user_id + ACTIVE toolkit slugs (cached)."""
    global _user_id, _active_toolkits, _context_resolved
    if _context_resolved:
        return _user_id, _active_toolkits or set()
    uid = settings.composio_user_id
    toolkits: set[str] = set()
    try:
        data = await _get("/connected_accounts", {"limit": 500})
    except Exception as e:  # noqa: BLE001 — degrade; a context miss just means an unscoped search
        # Deliberately NOT memoised: this is not an answer, it is the absence of one. Serving it
        # from cache would keep the search unscoped for the life of the process (J3.8).
        logger.debug(f"composio context lookup failed, will retry ({type(e).__name__}: {e})")
        return uid, toolkits
    for a in data.get("items", []):
        if a.get("status") == "ACTIVE":
            slug = (a.get("toolkit") or {}).get("slug")
            if slug:
                toolkits.add(slug)
            if not uid:
                uid = a.get("user_id")
    _user_id, _active_toolkits, _context_resolved = uid, toolkits, True
    return uid, toolkits


def _required_params(tool: dict) -> list[str]:
    ip = tool.get("input_parameters") or {}
    return list(ip.get("required") or [])


async def composio_find_tools(args: dict) -> str:
    if not _configured():
        return not_configured("Composio", _NEEDS)
    query = (args.get("query") or "").strip()
    if not query:
        return ("What app action are you looking for, sir? Use a short action-verb phrase "
                "like 'create a GitHub issue' or 'send a slack message'.")
    toolkit = (args.get("toolkit") or "").strip().lower()
    items: list[dict] = []
    try:
        _uid, active = await _context()
        # Strong keyword -> connected toolkit: scope the search server-side. One precise call instead
        # of a 17-way fan-out, and it dodges Composio's global ranking burying the connected app.
        if not toolkit and active:
            ql = query.lower()
            for kw, tk in _TOOLKIT_HINTS.items():
                if kw in ql and tk in active:
                    toolkit = tk
                    break
        params: dict = {"search": query, "limit": 15}
        if toolkit:
            params["toolkit_slug"] = toolkit
        items = (await _get("/tools", params)).get("items", [])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"composio_find_tools: live API failed ({type(e).__name__}); trying local cache")
    def _pick(t: dict) -> dict:
        return {"slug": t.get("slug"), "app": (t.get("toolkit") or {}).get("slug"),
                "desc": (t.get("description") or "").strip()[:140], "required": _required_params(t)}

    out: list[dict] = []
    for t in items:
        tk = (t.get("toolkit") or {}).get("slug")
        if active and not toolkit and tk not in active:
            continue  # only offer tools from the owner's CONNECTED apps
        out.append(_pick(t))
        if len(out) >= 5:
            break
    # The global search ranks across ALL 250+ apps, so for a broad query ('send an email') the
    # connected apps get crowded out by unconnected ones and the filter leaves nothing — even
    # though the app IS connected. Fan out: search each connected toolkit directly (in parallel)
    # and merge. This is what makes EVERY connected app reachable, not just the high-ranking ones.
    if not out and active and not toolkit:
        async def _scoped(tk: str) -> list[dict]:
            try:
                r = await _get("/tools", {"search": query, "toolkit_slug": tk, "limit": 5})
                return r.get("items", [])
            except Exception:  # noqa: BLE001
                return []

        per = await asyncio.gather(*[_scoped(tk) for tk in sorted(active)])
        # Round-robin merge: each connected app's BEST match first, then second-best, etc. — so no
        # app is truncated just for sorting late alphabetically, and Composio's brittle per-app
        # ranking (it buries SEND_EMAIL under LIST_DRAFTS) still surfaces the right tool in the list.
        seen: set[str] = set()
        for rank in range(5):
            for lst in per:
                if rank < len(lst):
                    t = lst[rank]
                    slug = t.get("slug")
                    if slug and slug not in seen:
                        seen.add(slug)
                        out.append(_pick(t))
            if len(out) >= 12:
                break
        out = out[:12]
    # T12 — only fall back to the local cache if the live API returned nothing. Live API is more
    # accurate; the local cache exists for offline fallback. (Earlier we tried local FIRST and got
    # noisy substring matches that pushed the LLM toward wrong slugs — fixed here.)
    if not out:
        try:
            from afon.brain.composio_catalog import long_tail_lookup as _local_lookup
            for h in _local_lookup(query, limit=8):
                tk = h.get("app", "")
                if active and tk and tk not in active:
                    continue
                out.append({"slug": h["slug"], "app": tk, "desc": h.get("desc", "")[:140],
                             "required": h.get("required") or []})
                if len(out) >= 5:
                    break
        except Exception:
            pass
    if not out:
        return (f"I couldn't find a connected-app tool for '{query}', sir. Try a different "
                "verb (e.g. 'create', 'send', 'list', 'search', 'update') or call composio_find_tools "
                "with a broader query. If the app isn't connected, link it in Composio and tell me.")
    lines = [f"- {o['slug']} ({o['app']}): {o['desc']} | required: {o['required'] or 'none'}"
             for o in out]
    return ("Found these app tools, sir:\n" + "\n".join(lines)
            + "\nCall composio_run_tool with the slug and its arguments to use one.")


async def composio_run_tool(args: dict) -> str:
    if not _configured():
        return not_configured("Composio", _NEEDS)
    slug = (args.get("tool_slug") or "").strip()
    if not slug:
        return "Which app tool, sir? Give me its slug from composio_find_tools."
    arguments = args.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    try:
        uid, _ = await _context()
        data = await _post(f"/tools/execute/{slug}", {"user_id": uid, "arguments": arguments})
    except Exception as e:  # noqa: BLE001
        return tool_error("Composio run", e)
    if not data.get("successful", data.get("success", False)):
        err = data.get("error") or data.get("raw") or "the tool reported an error"
        return f"That didn't go through, sir: {str(err)[:200]}"
    result = data.get("data") or {}
    text = json.dumps(result, default=str) if not isinstance(result, str) else result
    return f"Done, sir. {text[:600]}"


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "composio_find_tools",
            "description": (
                "Discover which Composio tool to call for a task. ALWAYS use this FIRST when the owner asks "
                "to do something in ANY external app — GitHub, Slack, Gmail, Google Drive/Docs/Sheets/Calendar, "
                "Linear, Stripe, Airtable, Supabase, YouTube, LinkedIn, Reddit, Instagram, Coinbase, Google Maps. "
                "The system prompt lists the common actions per app; this tool finds the long tail and returns "
                "the exact slug + required arguments. Search first, refuse-never: if the first call returns nothing, "
                "broaden the verb (create / send / list / search / update / delete) and call again. Then call "
                "composio_run_tool with the chosen slug."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description":
                              "What to do, e.g. 'create a GitHub issue', 'post a Slack message', "
                              "'add a row to a Google Sheet', 'list my Linear issues'."},
                    "toolkit": {"type": "string", "description":
                                "Optional app slug to restrict the search, e.g. 'github', 'slack', "
                                "'googlesheets', 'linear', 'stripe'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "composio_run_tool",
            "description": (
                "Execute a specific Composio app tool by its exact slug (from composio_find_tools) with "
                "its arguments. Writes (create/send/post/charge) are confirmed with the owner first; "
                "reads run directly."),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_slug": {"type": "string", "description":
                                  "The exact tool slug, e.g. 'GITHUB_CREATE_AN_ISSUE'."},
                    "arguments": {"type": "object", "description":
                                  "The tool's input arguments object (per its required params)."},
                },
                "required": ["tool_slug"],
            },
        },
    },
]

HANDLERS = {"composio_find_tools": composio_find_tools, "composio_run_tool": composio_run_tool}
