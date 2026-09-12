"""Brain-side voice I/O — transcribe an audio clip and synthesize a spoken reply.

Used by the Telegram bridge (and later the iPhone HTTPS client) so the 24/7 VPS brain can take a
VOICE message from your phone, hear it, and answer in Afon's voice — no laptop involved. Unlike
``edge/stt.py`` (Deepgram *live* streaming for the local mic), these are one-shot REST calls over a
whole clip: Deepgram prerecorded for STT, ElevenLabs REST for TTS. Both degrade to empty/None if a
key is missing, so the caller can fall back to text.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from afon.config import settings


async def transcribe_audio(audio: bytes, content_type: str = "audio/ogg") -> str:
    """Deepgram prerecorded STT over a whole clip. Returns the transcript (or "" on failure)."""
    if not settings.deepgram_api_key or not audio:
        return ""
    params = {"model": settings.deepgram_model or "nova-3", "smart_format": "true"}
    if settings.deepgram_language:
        params["language"] = settings.deepgram_language
    headers = {"Authorization": f"Token {settings.deepgram_api_key}", "Content-Type": content_type}
    try:
        from afon.brain.tools.base import http_post

        r = await http_post("https://api.deepgram.com/v1/listen",
                            params=params, headers=headers, content=audio)
        data = r.json()
        return (data["results"]["channels"][0]["alternatives"][0]["transcript"] or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"transcribe_audio failed ({type(e).__name__}: {e})")
        return ""


async def synthesize(text: str, *, voice_settings: dict | None = None) -> bytes | None:
    """ElevenLabs REST TTS → mp3 bytes (or None on failure). Afon's configured voice.

    ``voice_settings`` (C3): per-utterance prosody (stability/style/speed) — pass the output of
    ``affect.affect_to_voice`` to make the voice adapt to the owner's mood. None = the voice's defaults."""
    if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id) or not text.strip():
        return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    body = {"text": text[:2500], "model_id": settings.elevenlabs_model or "eleven_flash_v2_5"}
    if voice_settings:
        body["voice_settings"] = voice_settings
    try:
        from afon.brain.tools.base import http_post

        return (await http_post(url, headers=headers, json=body)).content  # mp3
    except Exception as e:  # noqa: BLE001
        logger.warning(f"synthesize failed ({type(e).__name__}: {e})")
        return None


async def synthesize_voice_note(text: str, *, voice_settings: dict | None = None) -> bytes | None:
    """Afon's reply as a true Telegram VOICE NOTE: ElevenLabs mp3 -> OGG/Opus via ffmpeg (the
    round push-to-talk bubble Telegram requires). Returns OGG bytes, or None to let the caller fall
    back to text. Needs ffmpeg+libopus (present on the VPS). ``voice_settings`` (C3): affect prosody."""
    mp3 = await synthesize(text, voice_settings=voice_settings)
    if not mp3:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "48k", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await proc.communicate(mp3)
        if proc.returncode == 0 and out:
            return out
        logger.warning(f"voice-note transcode failed (rc={proc.returncode})")
    except Exception as e:  # noqa: BLE001 — ffmpeg missing/err -> caller falls back to text/audio
        logger.warning(f"voice-note transcode error ({type(e).__name__}: {e})")
    return None


async def send_voice_note(text: str, chat_id: str | None = None) -> bool:
    """The one-call "Afon says X to the owner's phone" primitive: synthesize a true Telegram
    voice note and send it via the Afon bot. False = caller may fall back to text (the ONLY
    time text is acceptable — a lost alert is worse than a typed one)."""
    chat = chat_id or settings.telegram_default_chat
    token = settings.telegram_bot_token
    if not (chat and token and text.strip()):
        return False
    ogg = await synthesize_voice_note(text)
    if not ogg:
        return False
    try:
        from afon.brain.tools.base import http_post

        r = await http_post(
            f"https://api.telegram.org/bot{token}/sendVoice",
            data={"chat_id": chat},
            files={"voice": ("afon.ogg", ogg, "audio/ogg")},
        )
        return bool(r.json().get("ok"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"send_voice_note failed ({type(e).__name__}: {e})")
        return False


if __name__ == "__main__":  # shell-scriptable: python -m afon.brain.voice_io "<text>"
    import sys

    _text = " ".join(sys.argv[1:]).strip()
    _ok = asyncio.run(send_voice_note(_text)) if _text else False
    print("voice-note sent" if _ok else "voice-note FAILED")
    raise SystemExit(0 if _ok else 1)
