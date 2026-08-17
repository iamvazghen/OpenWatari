"""A parked deployment must STAY parked, even when the operator cannot disable the launcher.

Found the hard way on 2026-08-13, taking Afon out of production for a working session:

  * the laptop's `AfonEdge` / `AfonPcAgent` scheduled tasks carry a **5-minute repetition
    trigger**, and are registered such that `Disable-ScheduledTask` returns access denied without
    elevation. Killing the processes bought five minutes;
  * the VPS `jarvis-ticker` is a **system** unit (`/etc/systemd/system`, `Restart=always`) and the
    `openclaw` user has no passwordless sudo. Stopping the identically-named *user* unit did
    nothing to it.

So "stop running" was not expressible through either launcher, and the only symptom was that the
assistant kept answering after being told to stop — which is the worst possible way to learn that a
shutdown did not take.

The lock is a FILE rather than a config flag or an env var, for three reasons: it survives reboots
and re-logins, it is discoverable by whoever is wondering why nothing starts (its contents say who
parked it and why), and removing it is the entire undo — no elevation, no service surgery.

    park:    echo "reason" > ~/.afon/MAINTENANCE
    unpark:  rm ~/.afon/MAINTENANCE
"""

from __future__ import annotations

import sys
from pathlib import Path
from afon.shared.paths import state_dir

LOCK = state_dir() / "MAINTENANCE"


def parked() -> str | None:
    """The reason this deployment is parked, or None. Never raises: an unreadable lock still parks."""
    try:
        if not LOCK.exists():
            return None
        return LOCK.read_text(encoding="utf-8", errors="replace").strip() or "no reason recorded"
    except OSError:
        return "lock present but unreadable"


def halt_if_parked(role: str) -> None:
    """Exit immediately when parked. Call FIRST in every entry point, before any device or socket.

    Exits 0, not 1: a parked process is doing what it was told, and a launcher that logs failures
    would otherwise fill the log with alarms about a state somebody chose.
    """
    why = parked()
    if why is None:
        return
    print(f"afon {role}: parked by {LOCK} — {why}", file=sys.stderr)
    raise SystemExit(0)
