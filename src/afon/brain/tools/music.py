"""Music playback — free, music-tuned sources so Afon can actually start a song.

Sources, in order of preference:

* ``ytmusic`` (DEFAULT) — **YouTube Music** via ytmusicapi: music-tuned search (songs, not random
  videos), free, no account. Resolves to a track and opens the YouTube Music web player (the
  browser is launched with autoplay allowed, so it plays with sound).
* ``telegram`` — your **personal Telegram playlist**: pulls a track from the configured chat and
  delivers it to your phone Telegram (see ``telegram.telegram_music``).
* ``youtube`` — plain YouTube search (kept as a fallback / for non-music clips).

The default source is ``settings.music_source``. All sources are free; no Premium account needed.
"""

from __future__ import annotations

import asyncio

from afon.brain.tools.base import missing_arg, not_configured, tool_error
from afon.config import settings


def _ytmusic_search(query: str) -> tuple[str | None, str | None]:
    """Return (videoId, 'Title — Artist') for the top SONG result, via ytmusicapi (no auth)."""
    from ytmusicapi import YTMusic  # type: ignore

    yt = YTMusic()  # unauthenticated: catalogue search works without login
    results = yt.search(query, filter="songs") or yt.search(query)
    if not results:
        return None, None
    top = results[0]
    vid = top.get("videoId")
    title = top.get("title")
    artists = ", ".join(a.get("name", "") for a in (top.get("artists") or []) if a.get("name"))
    label = f"{title} — {artists}".strip(" —") if title else None
    return vid, label


def _yt_search(query: str) -> tuple[str | None, str | None]:
    import yt_dlp  # type: ignore

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    entries = (info or {}).get("entries") or []
    if not entries:
        return None, None
    return entries[0].get("id"), entries[0].get("title")


async def _open_in_browser(url: str) -> str | None:
    """Open `url` in Afon's autoplay browser. Returns an error string, or None on success."""
    if not settings.browser_tools_enabled:
        return "I need the browser enabled to play music, sir (AFON_BROWSER_TOOLS_ENABLED)."
    from afon.brain.tools.browser import browser as browser_tool

    r = await browser_tool({"action": "new_tab", "url": url})
    if "isn't configured" in r or "disabled" in r:
        return r
    return None


async def play_music(args: dict) -> str:
    query = (args.get("query") or "").strip()
    source = (args.get("source") or settings.music_source or "ytmusic").strip().lower()

    if source == "telegram":
        # Preferred: STREAM the track live into the Music Room voice chat (videochat) that the owner
        # joins to listen — not a file drop. Falls back to delivering the file only if no music room
        # is configured or he explicitly asked to play it OUT LOUD on the desktop (local=true).
        if settings.telegram_music_room_chat and not bool(args.get("local")):
            from afon.brain.tools.voicechat import play_in_music_room

            return await play_in_music_room({"query": query})
        from afon.brain.tools.telegram import telegram_music

        return await telegram_music(
            {"action": "play", "query": query, "local": bool(args.get("local"))}
        )

    if not query:
        return missing_arg("play_music", args, "query", "track", "song", "artist", "text",
                           ask="What should I play, sir?")

    # YouTube Music (default): music-tuned search → YouTube Music player.
    if source == "ytmusic":
        try:
            import ytmusicapi  # noqa: F401
        except ImportError:
            source = "youtube"  # gracefully fall back to plain YouTube
        else:
            try:
                vid, label = await asyncio.to_thread(_ytmusic_search, query)
                if vid:
                    err = await _open_in_browser(f"https://music.youtube.com/watch?v={vid}")
                    if err:
                        return err
                    return f"Playing {label or query} on YouTube Music, sir."
                source = "youtube"  # no song hit — try a plain search
            except Exception as e:  # noqa: BLE001
                return tool_error("music playback", e)

    # Plain YouTube (fallback / explicit).
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return not_configured("music playback", "yt-dlp (uv sync --extra brain)")
    try:
        vid, title = await asyncio.to_thread(_yt_search, query)
        if not vid:
            return f"I couldn't find '{query}', sir."
        err = await _open_in_browser(f"https://www.youtube.com/watch?v={vid}")
        if err:
            return err
        return f"Playing {title or query} on YouTube, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("music playback", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "Play a song or artist. Default source 'ytmusic' (YouTube Music) is FREE and "
                "music-tuned — no account, plays with sound. 'telegram' plays from the owner's "
                "personal Telegram playlist (delivered to his phone). 'youtube' is plain search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Song / artist to play (optional for 'telegram')."},
                    "source": {
                        "type": "string",
                        "enum": ["ytmusic", "telegram", "youtube"],
                        "description": "Default 'ytmusic' (free). 'telegram' = his saved playlist.",
                    },
                    "local": {"type": "boolean", "description": "For source 'telegram': true plays OUT LOUD on the desktop; false delivers to his phone."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {"play_music": play_music}
