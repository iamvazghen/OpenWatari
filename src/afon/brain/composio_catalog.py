"""Pre-fetch + cache the full Composio catalog, and produce a compact summary for the prompt.

Without this, the LLM only sees two schema tools (composio_find_tools / composio_run_tool)
and has ZERO idea that 572 actions across 17 apps exist. It then defaults to "I can't" when
the right tool is one composio_find_tools() away.

Architecture:
  1. ``refresh()`` — pull every tool across every ACTIVE toolkit from Composio; cache as JSON.
  2. ``compact_summary()`` — build a tiered markdown summary sized for the system prompt
     (~3K tokens): top apps + their key actions in full, long-tail apps in one line.
  3. ``long_tail_lookup(query)`` — semantic-ish search across the cached catalog for any tool
     the prompt summary didn't surface; backs ``composio_find_tools`` so it doesn't re-hit the
     network on every call.

Cached at ``~/.afon/composio_catalog.json`` (refreshed daily + on startup).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from afon.shared.paths import state_dir


_CACHE = state_dir() / "composio_catalog.json"


# Top apps whose top-actions deserve a full bullet (rest get one-line summaries).
# Ordered by how often an end-user asks for them in voice context.
_TOP_APPS = (
    "github", "slack", "gmail", "googlesheets", "googledrive",
    "googlecalendar", "googledocs", "linear", "stripe", "notion",
    "youtube", "airtable",
)
def _catalog_path() -> Path:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    return _CACHE


async def refresh() -> dict:
    """Fetch every tool across every ACTIVE toolkit from Composio. Cached on disk."""
    try:
        from afon.brain.tools.composio import _configured, _context, _get
    except ImportError as e:
        logger.warning(f"composio_catalog.refresh: imports failed ({e})")
        return {}
    if not _configured():
        # An unset key is a configuration state, not a fault. Falling through to _context() logged a
        # warning on every daily refresh — an alarm for something nobody broke (J3.8).
        logger.debug("composio_catalog.refresh: no API key configured, skipping")
        return {}
    try:
        _, active = await _context()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"composio_catalog.refresh: context failed ({e})")
        return {}
    catalog: dict = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "toolkits": {}}
    for app in sorted(active):
        try:
            data = await _get("/tools", {"toolkit_slug": app, "limit": 100})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"composio_catalog.refresh: {app} fetch failed ({e})")
            continue
        items = data.get("items", [])
        tools = []
        for t in items:
            slug = t.get("slug", "")
            desc = (t.get("description") or "").strip()
            required = (t.get("input_parameters") or {}).get("required") or []
            tools.append({"slug": slug, "desc": desc[:200], "required": list(required)})
        catalog["toolkits"][app] = tools
    try:
        _catalog_path().write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
        total = sum(len(v) for v in catalog["toolkits"].values())
        logger.info(f"composio_catalog.refresh: cached {total} tools across {len(catalog['toolkits'])} apps")
    except OSError as e:
        logger.warning(f"composio_catalog.refresh: cache write failed ({e})")
    return catalog


def _load_cache() -> dict:
    if not _catalog_path().exists():
        return {}
    try:
        return json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"composio_catalog._load_cache: read failed ({e})")
        return {}


def compact_summary(catalog: dict | None = None) -> str:
    """A COMPACT app-list pointer for the system prompt (~200 tokens, was ~4.2K).

    The old version dumped every top app's action slugs inline (~4.2K tokens EVERY turn — 60% of
    the whole system prompt and ~1s of first-token latency). But ``composio_find_tools(query=...)``
    already discovers action slugs on demand (``long_tail_lookup`` over the full cached catalog), so
    prefilling the actions was redundant. Progressive disclosure: name every app so the model knows
    what exists, and defer action detail to the lookup tool. Same awareness, a fraction of the cost.
    """
    cat = catalog or _load_cache()
    toolkits = cat.get("toolkits") or {}
    if not toolkits:
        return ("## External apps (Composio)\n"
                "(catalog not yet loaded — use `composio_find_tools(query=...)` to discover actions.)\n")
    apps = sorted(toolkits, key=lambda a: (a not in _TOP_APPS, a))  # common apps first
    app_list = ", ".join(f"{a} ({len(toolkits[a])})" for a in apps)
    return ("## External apps (Composio)\n"
            f"{len(toolkits)} apps connected (number = actions available). To act on one, call "
            "`composio_find_tools(query=\"<app> <action>\")` for the exact slug, then "
            "`composio_run_tool(tool_slug=..., arguments={...})`.\n"
            f"Apps: {app_list}.\n")


def long_tail_lookup(query: str, limit: int = 8) -> list[dict]:
    """Search the cached catalog for slugs whose name/description match `query`. Returns
    a list of {slug, app, desc, required}. Word-boundary match weighted toward slugs;
    stop-words ("a", "the", "to", "for", …) are dropped so they don't pollute the score.
    """
    cat = _load_cache()
    toolkits = cat.get("toolkits") or {}
    q = (query or "").lower().strip()
    if not q:
        return []
    raw_tokens = [t for t in re.split(r"\W+", q) if t]
    # Drop short stop-words and pure-numerics — they match everything and pollute the score.
    _STOP = {"a", "an", "the", "to", "for", "in", "on", "of", "and", "or", "with", "from", "by",
              "is", "it", "this", "that", "i", "you", "me", "my"}
    tokens = [t for t in raw_tokens if len(t) > 1 and t not in _STOP]
    if not tokens:
        return []
    # Word-boundary helper: avoid `house` matching `houseboat` (substring false positives).
    def _wb_count(needle: str, hay: str) -> int:
        return len(re.findall(rf"\b{re.escape(needle)}\b", hay))
    hits: list[dict] = []
    for app, tools in toolkits.items():
        for tool in tools:
            slug = tool.get("slug", "").lower()
            desc = tool.get("desc", "").lower()
            slug_hits = sum(_wb_count(t, slug) for t in tokens)
            desc_hits = sum(_wb_count(t, desc) for t in tokens)
            # Slug hits weighted 10x because they're explicit; desc hits for context disambiguation.
            score = slug_hits * 10.0 + desc_hits
            # Exact token in slug (e.g. token == an underscore-separated chunk) — bonus.
            slug_chunks = set(re.split(r"[_\W]+", slug))
            slug_chunks -= {""}
            exact_bonus = sum(1 for t in tokens if t in slug_chunks)
            score += exact_bonus * 5
            if score > 0:
                hits.append({"slug": tool.get("slug", ""), "app": app,
                             "desc": tool.get("desc", ""),
                             "required": tool.get("required", []),
                             "score": float(score)})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


# Module-level cache loaded lazily so importing this file is cheap.
_CACHED_SUMMARY: str | None = None


def get_summary() -> str:
    """Return the cached compact summary, building it on first call."""
    global _CACHED_SUMMARY
    if _CACHED_SUMMARY is None:
        _CACHED_SUMMARY = compact_summary()
    return _CACHED_SUMMARY


def invalidate() -> None:
    """Drop the in-memory cache (called by the scheduler after refresh)."""
    global _CACHED_SUMMARY
    _CACHED_SUMMARY = None