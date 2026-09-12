"""Local audio playback — make Afon actually PLAY a sound file out loud (not just deliver it).

This is what lets Afon play your personal Telegram tracks audibly: the track is downloaded and
piped to **ffplay** (from ffmpeg), launched detached and windowless so it plays in the background
through the current default output device. A single module-level handle holds the current player so
``stop_music`` can stop it.

**Playback runs on the laptop, never on the brain host.** The brain lives on a headless VPS which
*does* have ffplay installed, so the old in-process version started cleanly, logged "local playback
started" and reported "Playing … out loud now, sir" — into a server with no speakers. Nothing failed,
nothing warned; the only symptom was silence in the room. So the three primitives (play, stop, what's
playing) go through ``_dispatch`` to the laptop executor, and the *audio bytes* travel with the play
op because the Telegram session that fetched them lives on the brain.

Unlike the coding tools, a missing PC_LINK falls back to running here: on a single-host install the
brain IS the laptop, and playing locally is then exactly right.

(For YouTube / YouTube Music, playback already happens in the autoplay browser — that path makes real
sound too; this module covers local files like the Telegram playlist.)
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from afon.brain.tools.base import not_configured

# The currently-playing ffplay process + its label (module-level so stop_music can reach it).
_PROC: subprocess.Popen | None = None
_NOW: str | None = None
#: Temp file the current track was written to, so the next play can clean it up. ffplay holds the
#: handle while it runs, so deletion waits until playback is replaced or stopped.
_TMP: str | None = None

#: Ceiling on the audio we'll push down the wire. pc_agent connects with max_size=8MB, and base64
#: inflates by 4/3, so anything past ~5.5MB raw would be dropped by the transport as an oversized
#: frame — which surfaces as an unexplained failure rather than an honest "too big". Ordinary tracks
#: sit far below this; the caller degrades to phone delivery when it trips.
MAX_STREAM_BYTES = 5_500_000


# --------------------------------------------------------------------------- laptop-side executor

async def _pc_audio_play(args: dict) -> str:
    """Write the shipped bytes to a temp file and play them out loud. Runs ON THE LAPTOP."""
    global _PROC, _NOW, _TMP
    if not shutil.which("ffplay"):
        return json.dumps({"ok": False, "out": not_configured(
            "local audio playback", "ffplay (install ffmpeg — e.g. scoop install ffmpeg)")})
    label = str(args.get("label") or "that track")
    try:
        raw = base64.b64decode(args.get("b64") or "")
    except Exception as e:  # noqa: BLE001 — malformed payload must not kill the executor
        return json.dumps({"ok": False, "out": f"the audio didn't arrive intact ({type(e).__name__})"})
    if not raw:
        return json.dumps({"ok": False, "out": "no audio arrived to play"})

    _stop_local()  # only one local track at a time
    fd, tmp = tempfile.mkstemp(suffix=str(args.get("suffix") or ".mp3"))
    os.close(fd)
    try:
        Path(tmp).write_bytes(raw)
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        _PROC = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _NOW, _TMP = label, tmp
        logger.info(f"local playback started: {label}")
        return json.dumps({"ok": True, "out": label})
    except Exception as e:  # noqa: BLE001
        Path(tmp).unlink(missing_ok=True)
        return json.dumps({"ok": False, "out": f"I couldn't start playback: {e}"})


async def _pc_audio_stop(_args: dict) -> str:
    """Stop whatever is playing here. Runs ON THE LAPTOP."""
    return json.dumps({"ok": True, "out": _stop_local() or ""})


async def _pc_audio_now(_args: dict) -> str:
    """What's playing here, if anything. Runs ON THE LAPTOP."""
    return json.dumps({"ok": True, "out": _now_playing_local() or ""})


def _stop_local() -> str | None:
    """Stop the current playback and bin its temp file. Returns the label that was playing."""
    global _PROC, _NOW, _TMP
    was = _NOW
    if _PROC is not None and _PROC.poll() is None:
        try:
            _PROC.terminate()
        except Exception:  # noqa: BLE001
            pass
    if _TMP:
        # ffplay has let go by now; if it hasn't, leaving one temp file behind beats raising here.
        Path(_TMP).unlink(missing_ok=True)
    _PROC = _NOW = _TMP = None
    return was


def _now_playing_local() -> str | None:
    """The label currently playing, or None (also clears once the player has exited)."""
    global _PROC, _NOW, _TMP
    if _PROC is not None and _PROC.poll() is not None:
        if _TMP:
            Path(_TMP).unlink(missing_ok=True)
        _PROC = _NOW = _TMP = None
    return _NOW


# --------------------------------------------------------------------------- brain-side entry points

async def _forward(op: str, args: dict, local, timeout: float = 90.0) -> dict:
    from afon.brain.tools.system import _dispatch

    try:
        raw = await _dispatch(op, args, local, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — laptop dropped mid-op
        return {"ok": False, "out": f"the laptop didn't answer ({type(e).__name__})"}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"ok": False, "out": str(raw)}


async def play_file(path: str, label: str | None = None) -> str | None:
    """Play a local audio file out loud on the LAPTOP. Returns an error string, or None on success.

    ``path`` is read on whichever host holds the file (the brain, for a Telegram download) and its
    bytes ride along to the machine with the speakers.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return f"I couldn't open that track, sir: {type(e).__name__}."
    if size > MAX_STREAM_BYTES:
        mb = size / 1_000_000
        return (f"That track is {mb:.0f} MB, sir — too large for me to stream to your laptop. "
                "I can send it to your phone instead.")
    # Transport window scales with payload: a 5MB frame over a slow uplink outlasts the default.
    d = await _forward("audio_play",
                       {"b64": base64.b64encode(p.read_bytes()).decode("ascii"),
                        "label": label or p.name, "suffix": p.suffix or ".mp3"},
                       _pc_audio_play, timeout=180.0)
    return None if d.get("ok") else str(d.get("out") or "I couldn't start playback, sir.")


async def stop_desktop_playback() -> str:
    """Stop the desktop player and return the label it was playing, or "" if it was idle.

    Split out of ``stop_music`` so the music-room stop path can reach it without calling the tool
    (and without the two tools calling each other in a loop) — see J3.5.
    """
    d = await _forward("audio_stop", {}, _pc_audio_stop)
    return str(d.get("out") or "")


async def desktop_now_playing() -> str:
    """The label the desktop player is playing, or "". Raises if the laptop can't be asked.

    Split out of ``now_playing`` so ``brain.playback`` can own the question (42.F2). The tool used
    to ask only this route, which is why "what's playing?" answered "nothing" while a track was
    streaming into the music room.
    """
    d = await _forward("audio_now", {}, _pc_audio_now)
    return str(d.get("out") or "")


async def stop_music(_args: dict) -> str:
    # J3.5: "stop the music" is ONE instruction, and music has two homes — the desktop player here
    # and the Telegram music-room stream in voicechat.py. Whichever tool the model picked, the
    # owner meant "stop it". Answering "nothing was playing" while a track streams into the voice
    # chat is a confident wrong answer AND leaves it playing, which is the worse half.
    from afon.brain.playback import DESKTOP, ROOM, current

    playing = await current()
    if playing.where == DESKTOP:
        was = await stop_desktop_playback()
        return f"Stopped '{was or playing.label}', sir."
    if playing.where == ROOM:
        from afon.brain.tools import voicechat

        return await voicechat.stop_music_room({})
    return "Nothing was playing out loud, sir. (If it's a YouTube tab, I can close the browser.)"


async def now_playing(_args: dict) -> str:
    from afon.brain.playback import ROOM, current

    playing = await current()
    if not playing:
        return "Nothing is playing out loud right now, sir."
    where = " in the music room" if playing.where == ROOM else " out loud"
    return f"'{playing.label}' is playing{where}, sir."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "stop_music",
            "description": (
                "Stop music that's playing OUT LOUD on the desktop (a local track, e.g. from the "
                "Telegram playlist). For YouTube/YouTube Music playing in the browser, close the "
                "browser tab instead."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now_playing",
            "description": (
                "What track is currently playing OUT LOUD on the desktop, if any. Use for "
                "'what's playing?', 'what is this song?', 'what am I listening to?'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {"stop_music": stop_music, "now_playing": now_playing}

# What the laptop executor (edge/pc_agent.py) runs LOCALLY for each forwarded audio op. The speakers
# are there, so the player process and its temp file live there too — only the bytes and the verdict
# cross the wire. No re-dispatch (these are the _local fns).
LOCAL_HANDLERS = {
    "audio_play": _pc_audio_play,
    "audio_stop": _pc_audio_stop,
    "audio_now": _pc_audio_now,
}
