"""Cloud voice (Deepgram STT + ElevenLabs TTS) health + automatic local failover.

Policy the owner asked for: **cloud is the PRIMARY route** (accuracy/latency); Whisper + Piper are
LOCAL fallbacks that AUTOMATICALLY take over when the cloud fails, and hand back to cloud once it
recovers. Two independent signals drive the switch:

  1. **Startup probe** — every time the edge starts, a quick REST call checks that Deepgram and
     ElevenLabs actually answer with a valid key. If not (network down / key bad / service down),
     the edge builds the LOCAL engine instead, so it always comes up able to hear and speak.

  2. **Runtime error counter** — the cloud STT/TTS services (subclassed in stt.py/tts.py) call
     ``record_cloud_error`` whenever pipecat escalates a failure (the WebSocket handshake/keepalive
     deaths that a REST probe can't see). When too many land in a short window, the audio watchdog
     trips a restart; a cooldown marker keeps the restart on LOCAL for a while so a genuinely flaky
     cloud doesn't flap back every minute. Once the cooldown lapses and the cloud answers again, the
     watchdog upgrades back to cloud on the next restart.

Stdlib only (urllib) — no new dependency for a health check.
"""

from __future__ import annotations

import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from loguru import logger

from jarvis.config import settings

# --- runtime cloud-error tracking ------------------------------------------------------------------
_ERR_WINDOW_S = 45.0     # look back this far when deciding "cloud is failing now"
_ERR_THRESHOLD = 3       # this many TRANSIENT cloud errors in the window = failing -> fail over
_errors: deque[float] = deque(maxlen=64)
_cloud_active = False     # set True when a monitored CLOUD service is the built route this session
_permanent = False        # a quota/credit/auth error seen -> fail over on the FIRST one (won't self-heal)

# Substrings marking a PERMANENT failure (out of credits / bad or unpaid key). Unlike a transient 1011
# timeout, retrying won't help — so one is enough to fail over, and the cooldown is long (credits don't
# refill in 30 min). A 1011/timeout message contains none of these, so transient errors aren't misread.
_PERMANENT_CUES = ("quota", "credit", "insufficient", "unauthorized", "payment", "exceeded",
                   "invalid api key", "invalid_api_key", "forbidden", " 401", " 402", " 403")

# Errors that say nothing about CLOUD health and must not count toward failover. Deepgram closes an
# idle socket with 1011 "did not receive audio data ... within the timeout window" (net0001) — that is
# OUR mic being silent (or gated), not Deepgram being down. Counting it demoted a perfectly healthy
# ElevenLabs key to local Piper overnight (2026-07-30: key had 12.9k/63k chars left). A genuinely dead
# mic is already caught by the audio watchdog's frame-gap check, which is the right detector for it.
_NOT_CLOUD_FAULT_CUES = ("did not receive audio data", "net0001")


def record_cloud_error(detail: str = "") -> None:
    """A monitored cloud STT/TTS service escalated an error (called from its push_error_frame).
    ``detail`` is the error text; a quota/auth match flips the permanent flag for immediate failover.

    Trips the LOCAL cooldown HERE the instant the cloud is judged failing — NOT only from the watchdog.
    When pipecat's TTS service exhausts its own reconnects it tears the pipeline down (and main()'s
    finally cancels the watchdog) before the watchdog can call note_failover(); the supervisor then
    restarts straight back onto the dead cloud → a flap loop the owner hears as silence/garbled output.
    Writing the cooldown at the moment of failure makes the next restart come up on LOCAL regardless of
    which teardown path wins the race. Idempotent with the watchdog's note_failover()."""
    global _permanent
    text = (detail or "").lower()
    if any(c in text for c in _NOT_CLOUD_FAULT_CUES):
        logger.info(f"cloud voice error ignored (our silent mic, not a cloud fault): {detail[:100]}")
        return
    now = time.monotonic()
    _errors.append(now)
    if any(c in text for c in _PERMANENT_CUES):
        _permanent = True
        logger.error(f"cloud voice PERMANENT error (out of credits / bad key) — failing over: {detail[:120]}")
    recent = sum(1 for t in _errors if now - t <= _ERR_WINDOW_S)
    logger.warning(f"cloud voice error recorded ({recent} in {_ERR_WINDOW_S:.0f}s)")
    if _permanent or recent >= _ERR_THRESHOLD:
        trip_cooldown(_PERMANENT_COOLDOWN_S if _permanent else _COOLDOWN_S)
        logger.error("cloud voice failing — tripped LOCAL cooldown so the next (re)start comes up on "
                     "local Piper/Whisper (survives a pipeline crash that skips the watchdog)")


def cloud_failing() -> bool:
    """True on a permanent (quota/auth) error, OR when transient errors cross the threshold in-window."""
    now = time.monotonic()
    return _permanent or sum(1 for t in _errors if now - t <= _ERR_WINDOW_S) >= _ERR_THRESHOLD


def note_failover() -> None:
    """Called by the watchdog when it fails over to local. A permanent (credit/auth) failure earns a
    LONG cooldown so we don't flap back to a dead-credit key every few minutes; a transient one, short."""
    trip_cooldown(_PERMANENT_COOLDOWN_S if _permanent else _COOLDOWN_S)


def mark_cloud_active(active: bool) -> None:
    global _cloud_active
    _cloud_active = active


def cloud_is_active() -> bool:
    return _cloud_active


# --- cooldown marker (keeps a flaky cloud from flapping back immediately) ---------------------------
_COOLDOWN = Path(tempfile.gettempdir()) / "watari_cloud_voice_cooldown"
_COOLDOWN_S = 1800.0            # 30 min on LOCAL after a TRANSIENT cloud failure before retrying cloud
_PERMANENT_COOLDOWN_S = 21600.0  # 6 h after an out-of-credits/auth failure (credits don't refill fast)


def trip_cooldown(seconds: float = _COOLDOWN_S) -> None:
    """Stay on LOCAL until now+seconds. Stores the EXPIRY epoch so the duration survives a restart."""
    try:
        _COOLDOWN.write_text(str(time.time() + seconds))
    except Exception as e:  # noqa: BLE001 — never block failover on a temp-file write
        logger.warning(f"could not write cloud cooldown marker: {e}")


def in_cooldown() -> bool:
    try:
        return time.time() < float(_COOLDOWN.read_text())
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        return False


# --- startup REST reachability probes ---------------------------------------------------------------
def _ok_200(url: str, headers: dict, timeout: float = 4.0) -> bool:
    """True only on HTTP 200 — proves the service is reachable AND the key is valid. Any other
    status, or any network error, means 'don't rely on this cloud right now'."""
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        logger.warning(f"cloud probe {url} -> HTTP {e.code}")
        return False
    except Exception as e:  # noqa: BLE001 — DNS/timeout/refused all mean 'not usable'
        logger.warning(f"cloud probe {url} failed: {type(e).__name__}")
        return False


def deepgram_ok() -> bool:
    if not settings.deepgram_api_key:
        return False
    return _ok_200("https://api.deepgram.com/v1/projects",
                   {"Authorization": f"Token {settings.deepgram_api_key}"})


def elevenlabs_ok() -> bool:
    if not settings.elevenlabs_api_key:
        return False
    return _ok_200("https://api.elevenlabs.io/v1/user",
                   {"xi-api-key": settings.elevenlabs_api_key})


def cloud_stt_healthy() -> bool:
    """Use cloud STT this startup? No if we're cooling down from a recent failure, else probe live."""
    if in_cooldown():
        logger.info("cloud STT in cooldown after a recent failure — starting on LOCAL Whisper")
        return False
    return deepgram_ok()


def cloud_tts_healthy() -> bool:
    if in_cooldown():
        logger.info("cloud TTS in cooldown after a recent failure — starting on LOCAL Piper")
        return False
    return elevenlabs_ok()


def cloud_recovered() -> bool:
    """While degraded (running LOCAL), is it safe to go back to cloud? Cooldown lapsed AND both
    services answer again. Only called from the watchdog while degraded, so it doesn't poll in the
    healthy steady state."""
    return (not in_cooldown()) and deepgram_ok() and elevenlabs_ok()


if __name__ == "__main__":
    # Self-check: transient threshold, permanent (quota) immediate failover, cooldown expiry (no network).
    assert not cloud_failing()
    for _ in range(_ERR_THRESHOLD - 1):          # below threshold, transient -> not failing yet
        record_cloud_error("received 1011 (internal error) timeout")
    assert not cloud_failing(), "transient below threshold -> not failing"
    record_cloud_error("received 1011 (internal error) timeout")
    assert cloud_failing(), "transient at threshold -> failing"
    _errors.clear(); _permanent = False
    # ...but ONE quota error fails over immediately:
    record_cloud_error("401 Unauthorized: quota exceeded, insufficient credits")
    assert cloud_failing(), "a permanent (quota) error fails over on the first one"
    note_failover()
    assert in_cooldown(), "cooldown active after failover"
    assert float(_COOLDOWN.read_text()) - time.time() > _COOLDOWN_S, "permanent failure -> LONG cooldown"
    _COOLDOWN.unlink(missing_ok=True)
    assert not in_cooldown(), "no marker -> not in cooldown"
    # Deepgram's idle-socket close is OUR silent mic, not a cloud fault — it must never fail over.
    _errors.clear(); _permanent = False
    for _ in range(_ERR_THRESHOLD + 2):
        record_cloud_error("received 1011 (internal error) Deepgram did not receive audio data or a "
                           "text message within the timeout window. See https://dpgr.am/net0001")
    assert not cloud_failing(), "idle-mic timeouts must not count as cloud failure"
    assert not in_cooldown(), "idle-mic timeouts must not trip the local cooldown"
    print("voice_health self-check OK")
