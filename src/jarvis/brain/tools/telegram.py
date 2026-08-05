"""Telegram tools — read unread DMs and send messages by voice.

Two transports, because Telegram splits the capability:

* READING unread DMs needs a **user** account → Telethon (MTProto). Requires a one-time
  interactive login (phone + code) that writes a session file named ``settings.telegram_session``;
  after that it runs unattended.
* SENDING is done through the **Bot API** (just an HTTP POST with the bot token) — no session,
  no extra dependency. Jarvis confirms verbally before sending (persona rule: confirm anything
  outward-facing).

Both degrade gracefully when their credentials/session are absent.
"""

from __future__ import annotations

from jarvis.brain.tools.base import clip, http_post, not_configured, tool_error
from jarvis.config import settings


async def check_telegram(args: dict) -> str:
    limit = int(args.get("limit") or 10)
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        return not_configured(
            "reading Telegram",
            "a Telethon API id + hash and a one-time login (JARVIS_TELEGRAM_API_ID / _API_HASH)",
        )
    try:
        from telethon import TelegramClient  # type: ignore
    except ImportError:
        return not_configured("reading Telegram", "the channels extra (uv sync --extra channels)")
    try:
        client = TelegramClient(
            settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return not_configured(
                "reading Telegram",
                "a one-time Telethon login — run the login script once to create the session",
            )
        unread: list[str] = []
        async for dialog in client.iter_dialogs(limit=40):
            if dialog.unread_count and dialog.is_user:
                msg = dialog.message.message if dialog.message else ""
                unread.append(f"{dialog.name} ({dialog.unread_count}): {clip(msg, 120)}")
            if len(unread) >= limit:
                break
        await client.disconnect()
        if not unread:
            return "No unread Telegram messages, sir."
        return f"You have {len(unread)} chat(s) with unread messages:\n" + "\n".join(unread)
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram check", e)


# Giphy public beta key — works without signup (rate-limited). Override with JARVIS_GIPHY_API_KEY.
_GIPHY_PUBLIC = "GlVGYHkr3WSBnllca54iNt0yFbjz7L65"

_SAVED = {"me", "saved", "saved messages", "savedmessages", "myself", "self", "my saved messages"}


async def _resolve_gif(term: str) -> str | None:
    """Search Giphy for `term` and return the top GIF's URL (or treat `term` as a URL)."""
    term = term.strip()
    if term.startswith(("http://", "https://")):
        return term
    import httpx

    key = settings.giphy_api_key or _GIPHY_PUBLIC
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
        r = await c.get(
            "https://api.giphy.com/v1/gifs/search",
            params={"api_key": key, "q": term, "limit": 1, "rating": "pg"},
        )
        r.raise_for_status()
        data = r.json().get("data", [])
    return data[0]["images"]["original"]["url"] if data else None


async def send_telegram(args: dict) -> str:
    message = (args.get("message") or "").strip()
    to = (args.get("to") or "").strip()
    gif = (args.get("gif") or "").strip()
    file = (args.get("file") or "").strip()

    # Support the "@gif <query>" command typed/spoken inside the message.
    if message.lower().startswith("@gif "):
        gif = message[5:].strip()
        message = ""

    # Resolve the media: an explicit file/URL, or a GIF found by searching Giphy for the term.
    media = file
    if not media and gif:
        media = await _resolve_gif(gif)
        if not media:
            return f"I couldn't find a '{gif}' GIF, sir."

    saved = to.lower() in _SAVED
    if not to and not media and not settings.telegram_default_chat:
        return "Who should I send it to, sir? (say 'Saved Messages' or give a chat.)"
    if not message and not media:
        return "What should I send, sir?"

    # Telethon (user client) is preferred: it can reach Saved Messages, any dialog, and send
    # files/GIFs. Fall back to the Bot API for plain text to a numeric chat.
    if settings.telegram_api_id and settings.telegram_api_hash:
        try:
            from telethon import TelegramClient  # type: ignore

            client = TelegramClient(
                settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
            )
            await client.connect()
            if await client.is_user_authorized():
                if saved or (not to):
                    entity = "me"
                    dest = "your Saved Messages"
                elif to.lstrip("-").isdigit():
                    entity = int(to)
                    dest = f"chat {to}"
                else:
                    entity = to
                    dest = to
                if media:
                    path = await _fetch_media(media)
                    await client.send_file(entity, path, caption=message or None)
                    label = "GIF" if (gif or media.endswith(".gif")) else "file"
                    await client.disconnect()
                    return f"Sent the {label} to {dest}, sir."
                await client.send_message(entity, message)
                await client.disconnect()
                return f"Sent your message to {dest}, sir."
            await client.disconnect()
        except Exception as e:  # noqa: BLE001
            return tool_error("Telegram send", e)

    # Bot API fallback (text only).
    to = to or settings.telegram_default_chat or ""
    if not settings.telegram_bot_token:
        return not_configured("sending Telegram", "a bot token (JARVIS_TELEGRAM_BOT_TOKEN)")
    if media:
        return "Sending GIFs needs the Telegram user login, sir — text only on the bot path."
    try:
        r = await http_post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": to, "text": message},
        )
        ok = r.json().get("ok", False)
        return f"Sent your message to {to}, sir." if ok else f"Telegram refused the send: {clip(r.text, 160)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram send", e)


def _entity(ref: str):
    """Coerce a chat reference ('me' / numeric id / @username / title) for Telethon."""
    ref = (ref or "").strip()
    if ref.lower() in _SAVED or not ref:
        return "me"
    if ref.lstrip("-").isdigit():
        return int(ref)
    return ref


def _telethon_ready() -> str | None:
    """Return a 'not configured' note if the Telethon user client can't be used, else None."""
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        return not_configured(
            "reading a Telegram chat",
            "a Telethon API id + hash and the one-time login (JARVIS_TELEGRAM_API_ID / _API_HASH)",
        )
    try:
        import telethon  # type: ignore  # noqa: F401
    except ImportError:
        return not_configured("reading a Telegram chat", "the channels extra (uv sync --extra channels)")
    return None


async def read_chat(args: dict) -> str:
    """Read the last N messages of ANY chat — read or unread — WITHOUT marking them seen.

    Telethon's history fetch does not send a read receipt, so Jarvis can read your DMs aloud and the
    sender still sees them as merely delivered. Use mark_telegram afterwards if you want to change
    the read state deliberately.
    """
    note = _telethon_ready()
    if note:
        return note
    chat = (args.get("chat") or "").strip()
    if not chat:
        return "Which chat should I read, sir? Give me a name, @username, or 'saved'."
    try:
        limit = max(1, min(int(args.get("limit") or 10), 50))
    except (TypeError, ValueError):
        limit = 10
    try:
        from telethon import TelegramClient  # type: ignore

        client = TelegramClient(
            settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return not_configured("reading a Telegram chat", "a one-time Telethon login")
        entity = await client.get_entity(_entity(chat))
        me = await client.get_me()
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat)
        lines: list[str] = []
        # iter_messages is newest-first; collect then reverse so Jarvis reads oldest→newest.
        async for msg in client.iter_messages(entity, limit=limit):
            who = "You" if (msg.sender_id == me.id) else (
                getattr(msg.sender, "first_name", None) or title or "Them"
            )
            body = msg.message or ("[media]" if msg.media else "")
            if body:
                lines.append(f"{who}: {clip(body, 200)}")
        await client.disconnect()
        if not lines:
            return f"There's nothing to read in {title}, sir."
        lines.reverse()
        return f"Last {len(lines)} in {title}, sir:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram read", e)


async def mark_telegram(args: dict) -> str:
    """Mark a chat seen (sends a read receipt) or unread (your-side badge only).

    Honest about Telegram's limits: marking *seen* genuinely tells the sender you've read it.
    Marking *unread* only toggles the unread indicator on YOUR side — once the sender has seen a
    read receipt, Telegram offers no way to take it back (you can't turn 'seen' into 'delivered').
    """
    note = _telethon_ready()
    if note:
        return note
    chat = (args.get("chat") or "").strip()
    if not chat:
        return "Which chat, sir?"
    seen = args.get("seen")
    seen = True if seen is None else bool(seen)
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.tl.functions.messages import MarkDialogUnreadRequest  # type: ignore

        client = TelegramClient(
            settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return not_configured("marking a Telegram chat", "a one-time Telethon login")
        entity = await client.get_entity(_entity(chat))
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat)
        if seen:
            await client.send_read_acknowledge(entity)
            await client.disconnect()
            return f"Marked {title} as read, sir — the sender now sees it as seen."
        await client(MarkDialogUnreadRequest(peer=await client.get_input_entity(entity), unread=True))
        await client.disconnect()
        return (f"Marked {title} unread on your side, sir. Note: if the sender already saw a read "
                "receipt, Telegram can't undo that — this only restores your own unread badge.")
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram mark", e)


async def telegram_music(args: dict) -> str:
    """Play from the owner's personal Telegram playlist by delivering a track to his phone.

    Telegram has no API to remote-press-play on a specific device, so the realistic, honest
    behaviour is: find the track in his playlist chat and SEND it to ``telegram_music_target``
    (default Saved Messages). It then lands on every logged-in client — including his phone,
    where it's one tap to play in Telegram's built-in player.
    """
    action = (args.get("action") or "play").strip().lower()
    query = (args.get("query") or "").strip()

    if not settings.telegram_playlist_chat:
        return not_configured(
            "your Telegram playlist",
            "the playlist chat (JARVIS_TELEGRAM_PLAYLIST_CHAT — an id, @username, or its title)",
        )
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        return not_configured(
            "your Telegram playlist",
            "a Telethon login (JARVIS_TELEGRAM_API_ID / _API_HASH + the one-time sign-in)",
        )
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.tl.types import DocumentAttributeAudio, InputMessagesFilterMusic  # type: ignore
    except ImportError:
        return not_configured("your Telegram playlist", "the channels extra (uv sync --extra channels)")

    def _track_label(msg) -> str:
        for attr in getattr(msg.document, "attributes", []) or []:
            if isinstance(attr, DocumentAttributeAudio):
                title = attr.title or ""
                performer = attr.performer or ""
                if title or performer:
                    return f"{title} — {performer}".strip(" —")
        return (msg.message or "a track").strip()

    try:
        import random

        client = TelegramClient(
            settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return not_configured("your Telegram playlist", "a one-time Telethon login")

        # "latest"/"newest"/"last" is a selector, not a search term — pick the newest track.
        want_latest = query.lower() in {"latest", "newest", "last", "most recent", "recent"}
        match_q = "" if want_latest else query

        src = _entity(settings.telegram_playlist_chat)
        tracks = []  # iter_messages yields newest-first, so tracks[0] is the latest
        async for msg in client.iter_messages(src, filter=InputMessagesFilterMusic, limit=200):
            label = _track_label(msg)
            if not match_q or match_q.lower() in label.lower():
                tracks.append((msg, label))

        if action == "list":
            await client.disconnect()
            if not tracks:
                return "I don't see any tracks in your playlist that match, sir."
            names = "; ".join(lbl for _, lbl in tracks[:15])
            return f"{len(tracks)} track(s) in your playlist: {names}"

        if not tracks:
            await client.disconnect()
            return (f"I couldn't find '{query}' in your playlist, sir." if match_q
                    else "Your playlist looks empty, sir.")

        if want_latest:
            msg, label = tracks[0]            # newest
        elif match_q:
            msg, label = tracks[0]            # best (newest) match for the query
        else:
            msg, label = random.choice(tracks)  # no query = shuffle

        # local=true → actually PLAY it out loud on the desktop. The track downloads here (the
        # Telegram session lives on the brain) and localplay ships the bytes to the laptop, which is
        # where the speakers are. Oversize falls through to phone delivery rather than just failing.
        if bool(args.get("local")):
            import os
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            await client.download_media(msg, file=tmp)
            from jarvis.brain.tools.localplay import MAX_STREAM_BYTES, play_file

            try:
                too_big = os.path.getsize(tmp) > MAX_STREAM_BYTES
            except OSError:
                too_big = False
            if not too_big:
                await client.disconnect()
                err = await play_file(tmp, label)
                # The player writes and owns its own copy from the bytes we sent (even in the
                # single-host fallback), so this download has done its job either way.
                os.unlink(tmp)
                return err or f"Playing '{label}' out loud now, sir."
            os.unlink(tmp)
            target = _entity(settings.telegram_music_target)
            await client.send_file(target, msg.media, caption=f"▶ {label}")
            await client.disconnect()
            return (f"'{label}' is too large to stream to your laptop, sir, so I've sent it to your "
                    "phone instead — open Telegram and tap play.")

        # default → deliver to the phone (Telegram has no remote press-play API).
        target = _entity(settings.telegram_music_target)
        await client.send_file(target, msg.media, caption=f"▶ {label}")
        await client.disconnect()
        where = "your Saved Messages" if target == "me" else settings.telegram_music_target
        return (f"Sent '{label}' to {where} — open Telegram on your phone and tap play, sir.")
    except Exception as e:  # noqa: BLE001
        return tool_error("Telegram playlist", e)


async def _fetch_media(url_or_path: str) -> str:
    """Return a local file path for Telethon to send (downloads a URL to a temp .gif/file)."""
    if not url_or_path.startswith(("http://", "https://")):
        return url_or_path  # already a local path
    import tempfile

    import httpx

    suffix = ".gif" if ".gif" in url_or_path.lower() else ""
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as c:
        r = await c.get(url_or_path)
        r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    import os

    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "check_telegram",
            "description": "Summarise the owner's UNREAD Telegram direct messages. Use for 'any "
                           "messages', 'check Telegram', 'who messaged me'. Reading only — "
                           "send_telegram sends; read_chat reads one named chat's history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max chats to report (default 10)."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_chat",
            "description": (
                "Read aloud the last messages of a specific Telegram chat — whether or not they're "
                "unread — WITHOUT marking them seen (the sender still sees them as just delivered). "
                "Use for 'read me my last messages with Anna / what did X say'. chat = a name, "
                "@username, numeric id, or 'saved'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {"type": "string", "description": "Chat name, @username, id, or 'saved'."},
                    "limit": {"type": "integer", "description": "How many recent messages (default 10, max 50)."},
                },
                "required": ["chat"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_telegram",
            "description": (
                "Change a chat's read state. seen=true sends a read receipt (sender sees 'seen'); "
                "seen=false restores the unread badge on the owner's side only. Note: a read receipt "
                "the sender already saw cannot be reversed by Telegram."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {"type": "string", "description": "Chat name, @username, id, or 'saved'."},
                    "seen": {"type": "boolean", "description": "true = mark read; false = mark unread."},
                },
                "required": ["chat"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": (
                "THE way to message a real person on the owner's behalf — sends AS the owner from "
                "his own Telegram account. Use for 'message/text/write to X that Y': resolve X (a "
                "contact name, @username, or chat id — use the contacts tool for a bare name) and "
                "send. Also sends GIFs/files, and 'saved' targets his Saved Messages. There is no "
                "SMS/WhatsApp — Telegram is the messaging channel. Outward-facing: confirm the "
                "recipient and content with the owner first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message text / caption."},
                    "to": {"type": "string", "description": "'saved' for Saved Messages, or a username/chat id."},
                    "gif": {"type": "string", "description": "A GIF SEARCH term (e.g. 'panda' — finds a real matching GIF) or a GIF URL. (The user may say '@gif panda'.)"},
                    "file": {"type": "string", "description": "A file/media URL or local path to send."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_music",
            "description": (
                "Play from the owner's personal Telegram music playlist. 'play' finds a track (by "
                "name, or random if none given) and delivers it to his phone Telegram to tap and "
                "play; 'list' reports what's in the playlist. Use this when he says 'play from my "
                "Telegram playlist' / 'play my saved music'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play", "list"], "description": "Default 'play'."},
                    "query": {"type": "string", "description": "Track/artist to match; 'latest' = newest; blank = random."},
                    "local": {"type": "boolean", "description": "true = play OUT LOUD on the desktop now; false (default) = deliver to his phone Telegram."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {
    "check_telegram": check_telegram,
    "read_chat": read_chat,
    "mark_telegram": mark_telegram,
    "send_telegram": send_telegram,
    "telegram_music": telegram_music,
}
