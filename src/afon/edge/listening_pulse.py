"""A soft 'listening' pulse — a SINGLE audible ping shortly after the edge starts, so you KNOW it
is up and listening BEFORE you say the first wake word. It sounds exactly once and then stays
completely silent — no heartbeat, no repeat (the wake acknowledgement covers later turns).

Self-contained + dependency-free: a tiny sine 'ping' WAV is generated with the stdlib ``wave``
module and played with **winsound** on Windows, or ``afplay``/``aplay``/``ffplay`` elsewhere. If no
player is available it degrades to a silent no-op — it must never block or crash the pipeline.

It runs OUTSIDE the pipecat pipeline (a one-shot daemon thread), so it doesn't trip the half-duplex
mic gate or fight the TTS stream.
"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import threading
import wave
from pathlib import Path

from loguru import logger


def _make_pulse_wav() -> Path:
    """Generate a gentle ~140 ms sine 'ping' (soft attack + decay, low amplitude), 16 kHz mono."""
    sr = 16000
    dur = 0.14
    n = int(sr * dur)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        attack = min(1.0, t / 0.012)
        decay = max(0.0, 1.0 - (t - 0.02) / (dur - 0.02))
        env = max(0.0, attack * decay)
        s = 0.16 * env * (math.sin(2 * math.pi * 528 * t) + 0.4 * math.sin(2 * math.pi * 792 * t))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767))
    path = Path(tempfile.gettempdir()) / "afon_listening_pulse.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return path


class ListeningPulse:
    """Plays the soft ping ONCE, ``delay_s`` after start (giving the audio output time to open),
    then the thread exits — the pulse is present only before the first wake word. Start once."""

    def __init__(self, delay_s: float = 2.5, enabled: bool = True,
                 output_device_index: int | None = None) -> None:
        self._delay = max(0.5, float(delay_s))
        self._enabled = enabled
        # Play the ping through the SAME output device the edge chose (e.g. the AirPods), not the OS
        # default — winsound can only reach the default, so with headphones on you'd hear nothing.
        self._out_index = output_device_index
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wav: Path | None = None
        if enabled:
            try:
                self._wav = _make_pulse_wav()
            except Exception as e:  # noqa: BLE001 — never block the pipeline over a cosmetic cue
                logger.warning(f"listening pulse: could not build tone ({e}); disabled")
                self._enabled = False

    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="afon-listening-pulse", daemon=True)
        self._thread.start()
        logger.info("listening pulse: armed — one soft ping before the first wake word, then silent")

    def _run(self) -> None:
        # Wait briefly so the speaker is ready, then sound exactly one ping and stop for good.
        if self._stop.wait(self._delay):
            return  # shut down before we ever pinged
        try:
            self._play_once()
            logger.info("listening pulse: pinged once — now silent until restart")
        except Exception:  # noqa: BLE001
            pass

    def _play_to_device(self) -> bool:
        """Play the ping through the edge's CHOSEN output device via PyAudio (so it lands on the
        headphones, not the OS default). Returns True on success. Fail-open — any error → False so the
        caller falls back to winsound. The pipecat transport shares this device (WASAPI shared mode)."""
        if self._out_index is None:
            return False
        try:
            import pyaudio

            with wave.open(str(self._wav), "rb") as w:
                rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
                data = w.readframes(w.getnframes())
            pa = pyaudio.PyAudio()
            try:
                stream = pa.open(format=pa.get_format_from_width(width), channels=ch, rate=rate,
                                 output=True, output_device_index=self._out_index)
                stream.write(data)
                stream.stop_stream()
                stream.close()
            finally:
                pa.terminate()
            return True
        except Exception as e:  # noqa: BLE001 — cosmetic cue; never block/crash, just fall back
            logger.debug(f"listening pulse: device playback failed ({type(e).__name__}); winsound fallback")
            return False

    def _play_once(self) -> None:
        if not self._wav:
            return
        if self._play_to_device():   # preferred: the chosen output device (headphones)
            return
        p = str(self._wav)
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        import shutil
        import subprocess

        for player in ("afplay", "aplay", "ffplay"):
            exe = shutil.which(player)
            if not exe:
                continue
            args = [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", p] if player == "ffplay" else [exe, p]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

    def stop(self) -> None:
        self._stop.set()
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)  # silence any in-flight ping
            except Exception:  # noqa: BLE001
                pass
