"""Play music INTO a Telegram group voice chat — so your PHONE actually hears it live.

This is the one honest way to make music play *through Telegram on your phone*: Afon (your user
account, via **pytgcalls**) joins the voice chat of the "Afon Music Room" group and streams the
track into it. You join that voice chat once on your phone and leave it open; every track Afon
streams then plays live in your ear. (A *bot* cannot do this — only a user account can join a
Telegram voice chat, which is why this uses your Telethon session, not the bot token.)

A persistent pytgcalls client is held as a singleton (like the browser/local player). It uses a
**separate copy** of the authorized Telethon session so its long-lived connection never locks the
short-lived clients the other Telegram tools use.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from loguru import logger

from afon.brain.tools.base import not_configured, tool_error
from afon.config import settings

_APP = None       # PyTgCalls singleton
_CLIENT = None    # its dedicated Telethon client
_LOCK = asyncio.Lock()
#: Whether a track is streaming into the room RIGHT NOW. `_APP is not None` cannot answer that —
#: it stays set once the client has ever connected, so it says "we have a phone", not "someone is
#: talking". J3.5: the desktop stop path asks this before telling the owner nothing was playing.
_PLAYING: str | None = None


def room_is_playing() -> str | None:
    """The track streaming into the music room, or None. Cheap and safe to call from the other
    stop path — no locks, no network, no pytgcalls import."""
    return _PLAYING


def _vc_session() -> str:
    """A separate session file (copied from the authorized one) for the persistent voice client."""
    base = settings.telegram_session
    vc = f"{base}_vc"
    src, dst = Path(f"{base}.session"), Path(f"{vc}.session")
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)
    return vc


async def _ensure():
    global _APP, _CLIENT
    if _APP is not None:
        return
    from pytgcalls import PyTgCalls
    from telethon import TelegramClient

    _CLIENT = TelegramClient(_vc_session(), settings.telegram_api_id, settings.telegram_api_hash)
    _APP = PyTgCalls(_CLIENT)
    await _APP.start()
    logger.info("pytgcalls voice-chat client started")


def _track_label(msg) -> str:
    from telethon.tl.types import DocumentAttributeAudio

    for attr in getattr(msg.document, "attributes", []) or []:
        if isinstance(attr, DocumentAttributeAudio):
            title, performer = attr.title or "", attr.performer or ""
            if title or performer:
                return f"{title} — {performer}".strip(" —")
    return (msg.message or "a track").strip()


async def _pick_track(query: str):
    """Find a track in the playlist channel using the voice client. Returns (msg, label) or None."""
    import random

    from telethon.tl.types import InputMessagesFilterMusic

    want_latest = query.lower() in {"latest", "newest", "last", "recent"}
    match = "" if want_latest else query
    tracks = []
    async for m in _CLIENT.iter_messages(
        _entity(settings.telegram_playlist_chat), filter=InputMessagesFilterMusic, limit=200
    ):
        lbl = _track_label(m)
        if not match or match.lower() in lbl.lower():
            tracks.append((m, lbl))
    if not tracks:
        return None
    if want_latest or match:
        return tracks[0]
    return random.choice(tracks)


def _entity(ref: str):
    ref = (ref or "").strip()
    return int(ref) if ref.lstrip("-").isdigit() else ref


async def play_in_music_room(args: dict) -> str:
    query = (args.get("query") or "").strip()
    room = settings.telegram_music_room_chat
    if not room:
        return not_configured(
            "the Telegram music room", "the room chat id (AFON_TELEGRAM_MUSIC_ROOM_CHAT)"
        )
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        return not_configured("the Telegram music room", "a Telethon login")
    if not settings.telegram_playlist_chat:
        return not_configured(
            "the Telegram music room", "your playlist chat (AFON_TELEGRAM_PLAYLIST_CHAT)"
        )
    try:
        import pytgcalls  # noqa: F401
    except ImportError:
        return not_configured("voice-chat playback", "py-tgcalls (uv pip install py-tgcalls)")

    try:
        from pytgcalls.types import MediaStream

        async with _LOCK:
            await _ensure()
            picked = await _pick_track(query)
            if picked is None:
                return (f"I couldn't find '{query}' in your playlist, sir." if query
                        else "Your playlist looks empty, sir.")
            msg, label = picked
            fd, tmp = tempfile.mkstemp(suffix=".mp3")
            import os

            os.close(fd)
            await _CLIENT.download_media(msg, file=tmp)
            await _APP.play(int(room), MediaStream(tmp))
            global _PLAYING
            _PLAYING = label
        return (f"Now playing '{label}' in your Afon Music Room voice chat — open that group on "
                "your phone and join the voice chat to listen, sir.")
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram voice chat", e)


async def stop_music_room(_args: dict) -> str:
    global _PLAYING
    room = settings.telegram_music_room_chat
    async with _LOCK:
        if _APP is None or not room:
            # J3.5: nothing here — but "stop the music" is one instruction and music has two homes.
            # Reach the DESKTOP player directly rather than through its tool, so the two stop paths
            # cannot call each other in a loop.
            try:
                from afon.brain.tools.localplay import stop_desktop_playback

                was = await stop_desktop_playback()
                if was:
                    return f"Nothing was in the music room, sir — but I've stopped '{was}' on your desktop."
            except Exception as e:  # noqa: BLE001
                logger.debug(f"desktop cross-check skipped: {type(e).__name__}: {e}")
            return "Nothing is playing in the music room, sir."
        try:
            await _APP.leave_call(int(room))
        except Exception:  # noqa: BLE001
            _PLAYING = None
            return "The music room voice chat was already closed, sir."
        _PLAYING = None
    return "Left the music room voice chat, sir."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "play_in_music_room",
            "description": (
                "Stream a song into the owner's 'Afon Music Room' Telegram voice chat so it plays "
                "live on his PHONE (he joins that voice chat to listen). Pick a track from his "
                "playlist by name, 'latest' for the newest, or blank for a random one. Use this when "
                "he says 'play X in the music room' / 'play it on my phone / in Telegram'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Track/artist; 'latest' = newest; blank = random."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_music_room",
            "description": "Stop playback and LEAVE the Telegram Music Room voice chat. Use for "
                           "'stop the music room', 'leave the voice chat'. Telegram group playback "
                           "only — stop_music stops audio on the laptop.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {"play_in_music_room": play_in_music_room, "stop_music_room": stop_music_room}
