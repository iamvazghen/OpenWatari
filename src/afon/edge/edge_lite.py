"""Android (Termux) edge-lite (TODO 8.5) — a stripped edge: mic → brain WS → TTS.

The full laptop edge is a Pipecat pipeline (wake word, VAD, barge-in, speaker ID, local Whisper +
Piper). None of that ports cleanly to a phone in Termux. This is the honest minimum that still gives
a real voice loop on Android, reusing the SAME brain protocol as every other device:

    push-to-talk  →  termux-microphone-record  →  STT  →  BrainClient.send_utterance
                                                              ↓  (StreamEvent deltas)
                                                          termux-tts-speak

It registers as ``device_id="android"`` so the brain's Phase-6 routing already treats it as a phone
speaker (no barge-in, phone-speaker output) — nothing new needed server-side.

Deliberate simplifications (ponytail — this is a phone, not a workstation):
  * Push-to-talk, not always-on wake word. onnxruntime wake-word models are heavy/flaky under Termux;
    a keypress (or a Termux widget) to talk is robust and battery-cheap. Wake word is a later add.
  * No barge-in / VAD. The phone speaks a whole reply, then listens again.
  * Audio I/O + STT are shell-outs to Termux:API (``termux-microphone-record``, ``termux-tts-speak``)
    and a cloud STT, so there's no native-audio Python dependency to fight on Android.

Everything external (record, transcribe, speak, and the client) is INJECTABLE, so the loop is fully
unit-testable off-device; the Termux implementations are just the defaults. Runtime acceptance still
needs a real Android device — see ``deploy/termux/README.md``.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from afon.config import settings
from afon.edge.brain_client import BrainClient
from afon.shared.protocol import StreamEvent, StreamKind
from afon.shared.session import device_session_id

RecordFn = Callable[[float], Awaitable[Path | None]]      # seconds -> wav path (or None)
TranscribeFn = Callable[[Path], Awaitable[str]]           # wav path -> text
SpeakFn = Callable[[str], Awaitable[None]]                # text -> spoken


# ---- Termux:API default implementations (shell-outs; no native deps) ----------------------
async def _termux_record(seconds: float) -> Path | None:
    """Record from the phone mic via Termux:API. Returns the wav path, or None on failure."""
    out = Path(tempfile.gettempdir()) / "afon_termux_rec.wav"
    try:
        proc = await asyncio.create_subprocess_exec(
            "termux-microphone-record", "-f", str(out), "-l", str(int(max(1, seconds))),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await proc.wait()
        # -l already stops after the limit; make sure the recorder released the file.
        await (await asyncio.create_subprocess_exec(
            "termux-microphone-record", "-q",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)).wait()
        return out if out.exists() and out.stat().st_size > 0 else None
    except FileNotFoundError:
        logger.error("termux-microphone-record not found — install Termux:API (pkg install termux-api)")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"termux record failed ({type(e).__name__})")
        return None


async def _groq_transcribe(wav: Path) -> str:
    """Transcribe a wav via Groq's Whisper endpoint (fast, cheap). Empty string on any failure."""
    key = settings.groq_api_key
    if not key:
        logger.error("no groq_api_key set — edge-lite STT needs a cloud transcriber on Termux")
        return ""
    try:
        import httpx

        with wav.open("rb") as fh:
            r = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (wav.name, fh, "audio/wav")},
                data={"model": "whisper-large-v3-turbo", "response_format": "text"},
                timeout=30,
            )
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"edge-lite transcription failed ({type(e).__name__})")
        return ""


async def _termux_speak(text: str) -> None:
    """Speak via the phone TTS engine (Termux:API)."""
    text = (text or "").strip()
    if not text:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "termux-tts-speak", text,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await proc.wait()
    except FileNotFoundError:
        logger.error("termux-tts-speak not found — install Termux:API")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"termux speak failed ({type(e).__name__})")


class EdgeLite:
    """A minimal push-to-talk voice loop against the shared brain. Reuses the resilient BrainClient."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        record: RecordFn = _termux_record,
        transcribe: TranscribeFn = _groq_transcribe,
        speak: SpeakFn = _termux_speak,
        client: BrainClient | None = None,
        record_seconds: float = 6.0,
        reply_timeout: float = 60.0,
    ) -> None:
        self._record = record
        self._transcribe = transcribe
        self._speak = speak
        self._record_seconds = record_seconds
        self._reply_timeout = reply_timeout
        self._reply_buf: list[str] = []
        self._turn_done = asyncio.Event()
        self._client = client or BrainClient(   # 07.F1 — one conversation across devices
            session_id=session_id or device_session_id("android"),
            device_id="android", on_event=self._on_event)

    def _on_event(self, ev: StreamEvent) -> None:
        """Accumulate spoken deltas; speak the whole reply when the turn ends (no barge-in on phone)."""
        if ev.kind == StreamKind.assistant and ev.delta:
            self._reply_buf.append(ev.delta)
        if ev.final:
            self._turn_done.set()

    async def _speak_reply(self) -> str:
        reply = "".join(self._reply_buf).strip()
        self._reply_buf.clear()
        if reply:
            await self._speak(reply)
        return reply

    async def one_turn(self) -> str:
        """Record → transcribe → send → speak one exchange. Returns the spoken reply (for tests)."""
        wav = await self._record(self._record_seconds)
        if wav is None:
            return ""
        text = (await self._transcribe(wav)).strip()
        if not text:
            return ""
        self._turn_done.clear()
        if not await self._client.send_utterance(text):
            await self._speak("I've lost the link to the brain, sir — reconnecting.")
            return ""
        try:
            await asyncio.wait_for(self._turn_done.wait(), timeout=self._reply_timeout)
        except asyncio.TimeoutError:
            logger.warning("edge-lite: brain reply timed out")
        return await self._speak_reply()

    async def run(self, prompt_to_talk: Callable[[], Awaitable[bool]] | None = None) -> None:
        """Supervised link + a push-to-talk loop. ``prompt_to_talk`` returns True to record a turn,
        False to quit; default is a simple stdin Enter-to-talk (works in a Termux shell)."""
        supervisor = asyncio.create_task(self._client.run())

        async def _default_prompt() -> bool:
            loop = asyncio.get_event_loop()
            line = await loop.run_in_executor(None, lambda: input("[Enter]=talk  q=quit > "))
            return line.strip().lower() != "q"

        gate = prompt_to_talk or _default_prompt
        try:
            while await gate():
                await self.one_turn()
        finally:
            await self._client.stop()
            supervisor.cancel()


async def main() -> None:
    logger.info("Afon edge-lite (Android/Termux) — push-to-talk against the shared brain")
    await EdgeLite().run()


if __name__ == "__main__":
    asyncio.run(main())
