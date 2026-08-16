"""Inbound Telegram bridge — reach Afon from ANY device, 24/7, even with the laptop off.

Telegram's Bot API lets you DM the bot from your phone, desktop, or web. The 24/7 VPS brain
long-polls ``getUpdates``, runs the SAME shared ``AfonAgent`` (so memory + conversation context
are unified with the voice path — text you send and things you say are one continuous relationship),
and replies via ``sendMessage``. This is the laptop-independent reachability channel: the brain runs
on the VPS, so messaging the bot works whenever your phone has signal, regardless of the PC.

Security: only messages from your own ``telegram_default_chat`` id are answered; anything else is
logged and ignored. Graceful no-op if the bot token or chat id isn't configured.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx
from loguru import logger

from afon.brain.loops import tick
from afon.config import settings

Responder = Callable[[str], Awaitable[str]]


class TelegramBridge:
    def __init__(self, respond: Responder, authorized_chat_id: str | None, token: str | None = None) -> None:
        # ``respond`` is an async (text)->reply that is already serialised against the voice path
        # and updates the shared agent history, so Telegram and voice share one context.
        self._respond = respond
        self._chat = str(authorized_chat_id) if authorized_chat_id else None
        # DEDICATED bridge bot only — never fall back to the OpenClaw bot (telegram_bot_token),
        # or we'd steal OpenClaw's incoming messages off the shared getUpdates stream.
        self._token = token or settings.telegram_bridge_bot_token
        self._offset = 0
        self._stop = False

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat)

    async def _api(self, method: str, **params) -> dict:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(url, json=params)
            return r.json()

    async def _download(self, file_id: str) -> bytes | None:
        """Resolve a Telegram file_id to its bytes (used for voice messages)."""
        try:
            info = await self._api("getFile", file_id=file_id)
            path = info.get("result", {}).get("file_path")
            if not path:
                return None
            url = f"https://api.telegram.org/file/bot{self._token}/{path}"
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.content
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telegram bridge: file download failed ({type(e).__name__}: {e})")
            return None

    async def _send_voice_reply(self, chat_id: str, text: str, user_text: str | None = None) -> bool:
        """Send Afon's reply as a true VOICE NOTE (OGG/Opus). Returns False if synthesis failed.

        ``user_text`` (C3): the owner's inbound message — its affect shapes Afon's voice prosody so a
        stressed/low message gets a steadier, gentler reply."""
        from afon.brain.voice_io import synthesize_voice_note

        voice_settings = None
        if user_text:
            from afon.brain.affect import affect_to_voice, infer_affect
            voice_settings = affect_to_voice(infer_affect(user_text))
        ogg = await synthesize_voice_note(text, voice_settings=voice_settings)
        if not ogg:
            return False
        try:
            url = f"https://api.telegram.org/bot{self._token}/sendVoice"
            files = {"voice": ("afon.ogg", ogg, "audio/ogg")}
            async with httpx.AsyncClient(timeout=60) as c:
                await c.post(url, data={"chat_id": chat_id}, files=files)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telegram bridge: sendVoice failed ({type(e).__name__}: {e})")
            return False

    async def send_proactive(self, text: str) -> bool:
        """Afon-INITIATED voice note to the authorized chat (proactive nudges, fired reminders).

        This is how proactive VOICE reaches the phone 24/7 when no live voice device is connected:
        the brain pushes a Telegram voice note you can tap to hear. Falls back to a text message if
        synthesis fails. No-op (returns False) if the bridge isn't configured.
        """
        if not self.enabled or not text:
            return False
        if await self._send_voice_reply(self._chat, text):
            return True
        try:
            await self._api("sendMessage", chat_id=self._chat, text=text)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def run(self) -> None:
        if not self.enabled:
            logger.info("telegram bridge: disabled (no bot token or authorized chat id)")
            return
        logger.info(f"telegram bridge: listening for DMs from chat {self._chat} (24/7 reachability)")
        # Skip any backlog so we don't replay old messages on a restart.
        try:
            init = await self._api("getUpdates", timeout=0, offset=-1)
            ups = init.get("result", [])
            if ups:
                self._offset = ups[-1]["update_id"] + 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telegram bridge: init getUpdates failed ({type(e).__name__}: {e})")

        while not self._stop:
            try:
                data = await self._api("getUpdates", timeout=30, offset=self._offset)
                for up in data.get("result", []):
                    self._offset = up["update_id"] + 1
                    msg = up.get("message") or up.get("edited_message")
                    if not msg:
                        continue
                    chat_id = str((msg.get("chat") or {}).get("id"))
                    if chat_id != self._chat:
                        logger.warning(f"telegram bridge: ignoring message from unauthorized chat {chat_id}")
                        continue
                    # Voice message → transcribe with Deepgram; reply in voice too (mirror modality).
                    voice = msg.get("voice") or msg.get("audio")
                    is_voice = bool(voice)
                    if is_voice:
                        await self._api("sendChatAction", chat_id=chat_id, action="typing")
                        audio = await self._download(voice.get("file_id"))
                        from afon.brain.voice_io import transcribe_audio
                        text = await transcribe_audio(audio or b"", voice.get("mime_type") or "audio/ogg")
                        if not text:
                            await self._api("sendMessage", chat_id=chat_id,
                                            text="Sorry sir, I couldn't make out that voice note.")
                            continue
                        logger.info(f"telegram voice in: {text!r}")
                    else:
                        text = (msg.get("text") or "").strip()
                        if not text:
                            continue
                        logger.info(f"telegram in: {text!r}")
                    await self._api("sendChatAction", chat_id=chat_id, action="typing")
                    try:
                        # The listener's "tick" is one inbound message answered — a full agent turn,
                        # which is what its 120s budget is sized for. Polling getUpdates is not a
                        # tick: it returns every 30s with nothing, and counting that would make an
                        # idle bridge look busy.
                        with tick("telegram-bridge"):
                            reply = await self._respond(text)
                    except Exception:  # noqa: BLE001
                        logger.exception("telegram bridge: respond failed")
                        reply = "Sorry sir, I hit an error handling that."
                    reply = reply or "Sorry sir, I didn't catch that."
                    # VOICE-ONLY replies (per the owner's preference): always answer with a voice note,
                    # whether the input was a voice note or text. Fall back to a text message ONLY if
                    # synthesis/transcode fails, so Afon is never silent.
                    await self._api("sendChatAction", chat_id=chat_id, action="record_voice")
                    if not await self._send_voice_reply(chat_id, reply, user_text=text):
                        await self._api("sendMessage", chat_id=chat_id, text=reply)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a transient network/API error must not kill the loop
                logger.warning(f"telegram bridge: poll error ({type(e).__name__}: {e}); retrying")
                await asyncio.sleep(3)

    def stop(self) -> None:
        self._stop = True
