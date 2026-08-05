"""Proactive news-of-interest source — MyNews (the owner's RSS aggregator).

On-demand news lives behind the MyNews MCP tools (``mcp_servers``); this is the *unprompted*
side: once each morning, surface the top headlines as a proactive Signal so Watari mentions
what matters without being asked. Off unless JARVIS_MYNEWS_URL is set — fail-quiet like every
other signal source.

Lives in ``brain/``, NOT ``brain/tools/``: it exposes no schema and no handler, so it was never a
tool — it sat in the tools package purely because of where news happened to be written first, and
made "everything in tools/ is a tool" false. Its one consumer is ``proactive.py``.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from jarvis.config import settings


async def news_signals(now: datetime | None = None):
    from jarvis.brain.proactive import USER_TZ, Signal

    base = (settings.mynews_url or "").rstrip("/")
    if not base:
        return []
    now = now or datetime.now(USER_TZ)
    # Morning window only, and no wider than proactive_repeat_suppress_minutes (120) — otherwise
    # the date-keyed signal clears suppression mid-window and the brief fires twice.
    if not (8 <= now.hour < 10):
        return []
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(f"{base}/api/news", params={"limit": 5})
        r.raise_for_status()
        items = r.json().get("items") or []
    heads = []
    for item in items[:3]:
        t = (item.get("title") or "").strip()
        if t.endswith("..."):  # API truncates mid-word — drop the dangling fragment for speech
            t = t[:-3].rsplit(" ", 1)[0]
        if t:
            heads.append(t.rstrip(":;,. -"))
    if not heads:
        return []
    return [
        Signal(
            key=f"news-brief-{now:%Y-%m-%d}",
            message=f"Morning brief, sir: {'; '.join(heads)}.",
            urgency=0.62,
            kind="news",
        )
    ]
