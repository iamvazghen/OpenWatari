"""Process-level keep-alive for the edge daemons (voice assistant, PC agent).

Two real problems this solves:
  1. ``pythonw.exe`` (used so nothing flashes a console at logon) discards stdout/stderr — so when
     the edge died we had NO logs to say why. This tees everything to ``logs/<name>.log``.
  2. A daemon can exit either by crashing OR by ``main()`` returning cleanly (e.g. the audio
     pipeline ended). Windows Task Scheduler only restarts on a NON-zero exit, so a clean return
     left the edge silently dead. This wraps ``main()`` in a loop that relaunches it on ANY exit
     with backoff, so the only thing that stops it is a real shutdown (Ctrl-C / the task ending).

Used by ``jarvis.edge.assistant`` and ``jarvis.edge.pc_agent`` in their ``__main__``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _capture_raw_output(logdir: Path, name: str) -> None:
    """Point the process's REAL fds 1/2 at ``logs/<name>.err.log``.

    loguru only records what Python logs. Under pythonw there is no console, so anything written
    below that layer is destroyed: a C-level crash from PortAudio (which took the whole edge down
    natively), an import-time NameError traceback (before logging is configured), a library printing
    to stderr. Those are exactly the failures that left us with an empty log and no cause. Duplicating
    the file's fd onto 1/2 catches all of it, including writes from native code.

    Best-effort: if the platform refuses the dup, logging still works — never block startup on it."""
    try:
        f = open(logdir / f"{name}.err.log", "a", buffering=1, encoding="utf-8", errors="replace")
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
        sys.stdout = sys.stderr = f     # pythonw leaves these as None; give Python a real sink too
    except Exception:  # noqa: BLE001
        pass


def run_supervised(name: str, main: Callable[[], Awaitable[None]]) -> None:
    """Run ``main()`` forever: relaunch on any exit (crash or clean) with capped backoff, log to file."""
    logdir = _REPO_ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    _capture_raw_output(logdir, name)
    logger.add(logdir / f"{name}.log", rotation="5 MB", retention=5, enqueue=True, level="INFO")
    # Structured error journal + uncaught-exception hooks for this process. Installed here so BOTH
    # laptop daemons (voice edge and pc_agent) get it from one place, before main() can fail.
    try:
        from jarvis.shared import errors as _err

        _err.install(name)
    except Exception as e:  # noqa: BLE001 — observability must never block startup
        logger.warning(f"error tracking unavailable: {type(e).__name__}: {e}")
    # Exactly one live process per role. Two edges both hold the microphone and both answer; two
    # pc_agents race the same forwarded command. Newest wins, so an overlapping restart heals itself
    # instead of leaving a stale process serving old code. A watcher keeps checking, because the
    # duplicate usually appears AFTER startup (a scheduled refresh landing on a manual one).
    try:
        from jarvis.shared import singleton

        if not singleton.claim(name):
            logger.warning(f"{name}: a newer instance already owns this role — exiting")
            return
    except Exception as e:  # noqa: BLE001 — fail open; never refuse to start Watari
        logger.warning(f"singleton guard unavailable: {type(e).__name__}: {e}")
    logger.info(f"{name}: supervisor up — logs at logs/{name}.log")
    backoff = 2.0
    while True:
        started = time.time()

        async def _guarded() -> None:
            """Install the asyncio exception handler on the loop that will actually run, then run.
            Fire-and-forget tasks (proactive signals, room checks) otherwise report failures to
            stderr, which pythonw discards — that is where silent background errors were lost."""
            try:
                from jarvis.shared import errors as _err

                _err.install_asyncio_handler(asyncio.get_running_loop())
            except Exception:  # noqa: BLE001
                pass
            await main()

        try:
            asyncio.run(_guarded())
            logger.warning(f"{name}: main() returned — relaunching to stay alive")
        except KeyboardInterrupt:
            logger.info(f"{name}: stopped by user")
            return
        except Exception:
            logger.exception(f"{name}: crashed — relaunching")
        # Reset backoff if it ran a healthy while; otherwise back off so a hard-crash loop is gentle.
        backoff = 2.0 if (time.time() - started) > 60 else min(backoff * 2, 60.0)
        logger.info(f"{name}: restarting in {backoff:.0f}s")
        time.sleep(backoff)
