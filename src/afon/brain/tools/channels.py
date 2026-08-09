"""YouTube channel operations — list / pick / open.

For "play a random video from channel X" or "play the latest from X". Three resolution paths,
tried in order: (1) Composio YouTube toolkit (full structured data), (2) yt-dlp flat extract
(spa-compatible), (3) raw HTML scrape (last resort, often empty on modern YouTube).
The returned URL is then handed to ``open_url`` which dispatches to the laptop executor
(pc_agent.py) and opens it in the default browser.

Lazy group: 'channels' (loaded by trigger keywords: "youtube channel", "channel",
"from channel", "random video", "latest from").
"""

from __future__ import annotations

import asyncio
import random
import re

from loguru import logger

from afon.brain.tools.base import http_get


# (handle -> channel_id) — handle resolution is one Composio call, cache forever.
_CHANNEL_ID_CACHE: dict[str, str] = {}


def _normalise_handle(name: str) -> str:
    """Turn 'Lofi Girl', '@LofiGirl', 'https://youtube.com/@LofiGirl' into 'LofiGirl'.

    Case is preserved (YouTube handles are case-sensitive). Spaces collapse.
    """
    s = (name or "").strip()
    s = re.sub(r"^https?://(www\.)?youtube\.com/", "", s, flags=re.I)
    s = re.sub(r"^@", "", s)
    s = s.split("/")[0].split("?")[0]
    s = re.sub(r"\s+", "", s)
    return s


# ---------------------------------------------------------------------------
# Composio YouTube toolkit — primary path. We call the API directly (instead of via
# composio_run_tool) because composio_run_tool caps the result text at 600 chars, which
# only fits a couple of videos; we want the full list.
# ---------------------------------------------------------------------------

async def _composio_execute(slug: str, arguments: dict) -> dict | None:
    """Direct Composio API call. Returns the parsed JSON data dict (or None on failure)."""
    try:
        from afon.brain.tools.composio import _post, _context
    except ImportError:
        return None
    try:
        uid, _ = await _context()
        data = await _post(f"/tools/execute/{slug}", {"user_id": uid, "arguments": arguments})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"channels: Composio {slug} call failed: {e}")
        return None
    if not data.get("successful", data.get("success", False)):
        logger.warning(f"channels: Composio {slug} unsuccessful: "
                       f"{str(data.get('error') or data.get('raw') or data)[:200]}")
        return None
    return data.get("data") or {}


async def _channel_id_via_composio(handle: str) -> str | None:
    cached = _CHANNEL_ID_CACHE.get(handle)
    if cached:
        return cached
    arg = handle if handle.startswith("@") else f"@{handle}"
    data = await _composio_execute("YOUTUBE_GET_CHANNEL_ID_BY_HANDLE",
                                   {"channel_handle": arg})
    if not data:
        return None
    # The API nests items under response_data.items[].id OR top-level.
    items = (data.get("items") or
             (data.get("response_data") or {}).get("items") or [])
    if items:
        cid = items[0].get("id") or ""
        if cid:
            _CHANNEL_ID_CACHE[handle] = cid
            return cid
    logger.warning(f"channels: Composio returned no channelId for @{handle}: {str(data)[:200]}")
    return None


async def _videos_via_composio(channel_id: str, limit: int) -> list[dict] | None:
    """Returns [{id, title, url}, …] up to `limit` items, or None on failure."""
    data = await _composio_execute("YOUTUBE_LIST_CHANNEL_VIDEOS",
                                   {"channelid": channel_id, "maxresults": limit, "type": "video"})
    if not data:
        return None
    items = (data.get("items") or
             (data.get("response_data") or {}).get("items") or [])
    out: list[dict] = []
    for v in items:
        # Two shapes we see: id.videoId OR top-level videoId.
        if isinstance(v.get("id"), dict):
            vid = v["id"].get("videoId", "")
        else:
            vid = v.get("videoId") or v.get("id") or ""
        snippet = v.get("snippet") or {}
        title = snippet.get("title") or ""
        if vid:
            out.append({"id": str(vid), "title": title,
                        "url": f"https://www.youtube.com/watch?v={vid}"})
        if len(out) >= limit:
            break
    return out or None


# ---------------------------------------------------------------------------
# yt-dlp fallback — works on modern (SPA) YouTube where the initial HTML is empty.
# ---------------------------------------------------------------------------

async def _videos_via_ytdlp(handle: str, limit: int) -> list[dict] | None:
    """Use yt-dlp flat-extract to list a channel's videos. Off the GIL."""
    def _extract() -> list[dict]:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return []
        url = f"https://www.youtube.com/@{handle}/videos"
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
                "playlistend": limit, "skip": "live"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        out = []
        for e in entries:
            vid = e.get("id") or ""
            title = e.get("title") or e.get("alt_title") or ""
            if vid:
                out.append({"id": vid, "title": title,
                            "url": f"https://www.youtube.com/watch?v={vid}"})
        return out
    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# HTML scrape — last resort, often empty because YouTube is SPA-rendered.
# ---------------------------------------------------------------------------

_VID_RE = re.compile(
    r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})".*?"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]{1,200})"',
    re.DOTALL,
)


async def _videos_via_scrape(handle: str, limit: int) -> list[dict] | None:
    from urllib.parse import quote
    url = f"https://www.youtube.com/@{quote(handle)}/videos"
    try:
        r = await http_get(url)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"channels: scrape of {url} failed: {e}")
        return None
    seen: set[str] = set()
    out: list[dict] = []
    for m in _VID_RE.finditer(r.text):
        vid, title = m.group(1), m.group(2)
        if vid in seen:
            continue
        seen.add(vid)
        out.append({"id": vid, "title": title, "url": f"https://www.youtube.com/watch?v={vid}"})
        if len(out) >= limit:
            break
    return out or None


async def _resolve_videos(name: str, limit: int) -> tuple[list[dict] | None, str | None]:
    """(videos, error). On success videos is non-empty; on failure error is the spoken line."""
    handle = _normalise_handle(name)
    if not handle:
        return None, "Which channel, sir?"
    # 1. Composio (structured).
    cid = await _channel_id_via_composio(handle)
    if cid:
        videos = await _videos_via_composio(cid, limit)
        if videos:
            return videos, None
    # 2. yt-dlp (SPA-compatible).
    logger.info(f"channels: falling back to yt-dlp for @{handle}")
    videos = await _videos_via_ytdlp(handle, limit)
    if videos:
        return videos, None
    # 3. HTML scrape (often empty).
    logger.info(f"channels: falling back to scrape for @{handle}")
    videos = await _videos_via_scrape(handle, limit)
    if videos:
        return videos, None
    return None, f"I couldn't find any videos on the '{name}' channel, sir."


def _format_listing(videos: list[dict], picked: dict | None = None) -> str:
    lines = []
    for i, v in enumerate(videos, 1):
        marker = " ►" if picked and v["id"] == picked["id"] else "  "
        title = (v.get("title") or "").strip()
        lines.append(f"{marker} {i}. {title} — {v['url']}")
    return "\n".join(lines)


def _limit(args: dict, default: int, lo: int, hi: int) -> int:
    """Clamp the requested count, tolerating a non-numeric one.

    The model sometimes fills this with a word ("twelve") or an empty string; ``int()`` then raises
    and the owner hears "ValueError" from the agent's blanket catch. A sensible default is a far
    better answer to "show me a few videos" than an error.
    """
    try:
        n = int(args.get("limit", default))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


async def list_channel_videos(args: dict) -> str:
    """List recent videos on a YouTube channel."""
    name = (args.get("channel") or args.get("name") or "").strip()
    limit = _limit(args, 12, 1, 30)
    videos, err = await _resolve_videos(name, limit)
    if err:
        return err
    if not videos:
        return f"I couldn't find any videos on '{name}', sir." if name else "I found no videos, sir."
    header = f"Recent videos on '{name}', sir:\n" if name else "Recent videos, sir:\n"
    return header + _format_listing(videos)


async def play_random_from_channel(args: dict) -> str:
    """Pick a random video from a channel and open it."""
    name = (args.get("channel") or args.get("name") or "").strip()
    limit = _limit(args, 20, 5, 30)  # at least 5 to randomise from
    videos, err = await _resolve_videos(name, limit)
    if err:
        return err
    if not videos:
        # random.choice([]) is an IndexError, and "no videos" is a complete answer, not a failure.
        return f"I couldn't find any videos on '{name}', sir."
    picked = random.choice(videos)
    # Dispatch the open to the laptop (pc_agent) so the browser pops on the owner's screen.
    from afon.brain.tools.system import open_url
    open_res = await open_url({"url": picked["url"]})
    title = picked.get("title") or picked["id"]
    if "Opened" in open_res or "Launched" in open_res:
        return f"Opening a random one from '{name}', sir: {title}."
    return f"I picked {title} for '{name}', sir, but the browser wouldn't open ({open_res})."


async def play_latest_from_channel(args: dict) -> str:
    """Open the latest video on a YouTube channel."""
    name = (args.get("channel") or args.get("name") or "").strip()
    limit = 5
    videos, err = await _resolve_videos(name, limit)
    if err:
        return err
    if not videos:
        return f"I couldn't find any videos on '{name}', sir."
    picked = videos[0]  # YouTube orders by recency in the /videos tab
    from afon.brain.tools.system import open_url
    open_res = await open_url({"url": picked["url"]})
    title = picked.get("title") or picked["id"]
    if "Opened" in open_res or "Launched" in open_res:
        return f"Opening the latest from '{name}', sir: {title}."
    return f"I found the latest — {title} — but the browser wouldn't open ({open_res})."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_channel_videos",
            "description": (
                "List recent videos from a YouTube channel (e.g. 'Lofi Girl', 'Veritasium'). Returns "
                "title + watch URL for each. Use when the owner asks what a channel has posted, or to "
                "preview before picking one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description":
                                "Channel name or @handle, e.g. 'Lofi Girl', '@veritasium'."},
                    "limit": {"type": "integer", "description":
                              "How many recent videos to list (default 12, max 30)."},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_random_from_channel",
            "description": (
                "Pick a random recent video from a YouTube channel and open it in the owner's "
                "default browser. Use when the owner says 'play a random video from Lofi Girl', "
                "'open something from Veritasium', 'surprise me with a MrBeast clip', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description":
                                "Channel name or @handle."},
                    "limit": {"type": "integer", "description":
                              "Pool size to randomise from (default 20, min 5, max 30)."},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_latest_from_channel",
            "description": (
                "Open the most recent video from a YouTube channel in the owner's browser. Use "
                "when the owner says 'play the latest from X', 'what's new on X', 'open the new "
                "Veritasium upload'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description":
                                "Channel name or @handle."},
                },
                "required": ["channel"],
            },
        },
    },
]

HANDLERS = {
    "list_channel_videos": list_channel_videos,
    "play_random_from_channel": play_random_from_channel,
    "play_latest_from_channel": play_latest_from_channel,
}