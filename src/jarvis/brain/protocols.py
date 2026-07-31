"""Protocols — named, password-gated executable routines (FRIDAY/JARVIS style).

A protocol is a small standalone script Jarvis runs ONLY when given the matching password.
This is the identity gate: Jarvis asks for the password first (persona rule), and the runner
verifies it in constant time before launching anything. The scripts live in
``src/jarvis/protocols/`` and are launched **detached** so they survive Jarvis being killed
(needed for the stop/restart protocols).

The three shipped protocols:
  * ``goodnight`` — stops Jarvis (terminates the running edge process).
  * ``phoenix``   — restarts Jarvis (kills the old process, starts a fresh one).
  * ``ragnarok``  — restarts the laptop.

Passwords come from settings (``JARVIS_PROTOCOL_*_PASSWORD``) — CHANGE the defaults in .env.
"""

from __future__ import annotations

import hmac
import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from jarvis.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_DIR = _REPO_ROOT / "src" / "jarvis" / "protocols"


def _registry() -> dict[str, dict]:
    return {
        "goodnight": {
            "script": "goodnight.py",
            "password": settings.protocol_goodnight_password,
            "spoken": "Goodnight, sir. Powering down.",
            "description": "stops Jarvis",
            # Stop the voice edge only — pc_agent stays up so phoenix can revive him remotely. The
            # marker tells WatariEdgeGuard this silence was ORDERED, otherwise the guard would treat
            # a missing edge as a crash and undo goodnight within 30 minutes. restart_edge.ps1
            # clears it, so phoenix/the daily refresh bring him back.
            "pc_command": ("New-Item -ItemType File -Force -Path 'C:\\Jarvis\\logs\\edge_stopped_by_owner' "
                           "| Out-Null; Stop-ScheduledTask -TaskName JarvisEdge; "
                           "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                           "Where-Object { $_.CommandLine -match 'jarvis\\.edge\\.assistant' } | "
                           "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"),
        },
        "phoenix": {
            "script": "phoenix.py",
            "password": settings.protocol_phoenix_password,
            "spoken": "Rebooting myself, sir. Back in a moment.",
            "description": "restarts Jarvis",
            "pc_command": "Start-ScheduledTask -TaskName WatariEdgeRefresh",
        },
        "ragnarok": {
            "script": "ragnarok.py",
            "password": settings.protocol_ragnarok_password,
            "spoken": "Restarting the machine, sir. Save your work.",
            "description": "restarts the laptop",
            "pc_command": 'shutdown /r /t 15 /c "Watari protocol Ragnarok: restarting."',
        },
        "backup": {
            "script": "backup.py",
            "password": settings.protocol_backup_password,
            "spoken": "Memory backup started, sir.",
            "description": "backs up Jarvis memory",
        },
        "ping": {
            "script": "ping.py",
            "password": settings.protocol_ping_password,
            "spoken": "Pinging your phone, sir.",
            "description": "sends a phone push test",
        },
        "diagnostics": {
            "script": "diagnostics.py",
            "password": settings.protocol_diagnostics_password,
            "spoken": "Diagnostics started, sir.",
            "description": "writes a local diagnostics report",
        },
        "auditpack": {
            "script": "auditpack.py",
            "password": settings.protocol_auditpack_password,
            "spoken": "Audit archive started, sir.",
            "description": "archives audit logs",
        },
        "checkpoint": {
            "script": "checkpoint.py",
            "password": settings.protocol_checkpoint_password,
            "spoken": "Checkpoint started, sir.",
            "description": "archives key non-secret Jarvis context",
        },
    }


def protocol_names() -> list[str]:
    return list(_registry().keys())


def describe_protocols() -> str:
    return "; ".join(f"{n} ({p['description']})" for n, p in _registry().items())


class ProtocolResult:
    def __init__(self, ok: bool, message: str, spoken: str | None = None) -> None:
        self.ok = ok
        self.message = message
        self.spoken = spoken


async def run_protocol_async(name: str, password: str, drill: bool = False) -> ProtocolResult:
    """Run a protocol, sending the machine-level ones to the LAPTOP when it's connected.

    The recovery protocols were written when the brain ran on the laptop, and moving the brain to the
    VPS silently broke all three while still reporting success (verified 2026-07-30): ``ragnarok`` ran
    ``shutdown`` as a non-sudo VPS user (nothing happened), ``phoenix`` shelled out to ``taskkill``
    (absent on Linux) and would have spawned a mic-less edge on the server, and ``goodnight`` killed a
    brain that ``Restart=always`` revived five seconds later. Anything whose target is the owner's
    machine now travels over PC_LINK to the executor that is actually on it; the rest (backup, ping,
    diagnostics, auditpack, checkpoint) are brain-side work and still run here."""
    reg = _registry()
    entry = reg.get((name or "").strip().lower())
    pc_command = (entry or {}).get("pc_command")
    if not pc_command:
        return run_protocol(name, password, drill=drill)

    from jarvis.brain.pc_link import PC_LINK

    gate = run_protocol(name, password, drill=True)   # password + script gate, never launches
    if not gate.ok:
        return gate
    target = "your laptop" if PC_LINK.active else "this machine"
    if drill:
        return ProtocolResult(
            True,
            f"Drill OK: protocol {name} verified — password accepted, and it would {entry['description']} "
            f"on {target}. Not executed (drill).",
            spoken=f"Drill passed, sir — {name} is ready and would {entry['description']}. I didn't run it.",
        )
    if not PC_LINK.active:
        return ProtocolResult(
            False,
            f"Protocol {name} acts on your laptop, sir, and it isn't connected right now — nothing was run.",
            spoken=f"Your laptop isn't connected, sir, so I couldn't run {name}.",
        )
    try:
        await PC_LINK.forward("run_powershell", {"command": pc_command})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"protocol '{name}' laptop dispatch failed: {type(e).__name__}: {e}")
        return ProtocolResult(False, f"I couldn't reach your laptop to run {name}, sir ({type(e).__name__}).")
    logger.info(f"protocol '{name}' authorized and dispatched to the laptop")
    return ProtocolResult(True, f"Protocol {name} authorized. {entry['description'].capitalize()}.",
                          spoken=entry["spoken"])


def run_protocol(name: str, password: str, drill: bool = False) -> ProtocolResult:
    """Verify the password and, if correct, launch the protocol's detached script.

    ``drill=True`` REHEARSES instead: it runs the full authorization path (password gate + script
    presence) but does NOT launch the script — so recovery protocols (which stop/restart the machine)
    can be verified for readiness without actually firing. This is the drill that keeps the recovery
    runbook honest: each protocol is proven invocable + correctly gated, on demand and in tests."""
    if not settings.protocols_enabled:
        return ProtocolResult(False, "Protocols are disabled, sir.")
    name = (name or "").strip().lower()
    reg = _registry()
    if name not in reg:
        return ProtocolResult(False, f"There's no protocol '{name}', sir. I have: {describe_protocols()}.")
    proto = reg[name]
    if not password:
        return ProtocolResult(False, f"Protocol {name} needs the password, sir.")
    # Constant-time compare so a wrong guess leaks no timing.
    if not hmac.compare_digest(str(password).strip(), str(proto["password"])):
        logger.warning(f"protocol '{name}' refused: bad password")
        return ProtocolResult(False, f"That password is incorrect, sir. Protocol {name} was not run.")

    script = _SCRIPT_DIR / proto["script"]
    if not script.is_file():
        return ProtocolResult(False, f"Protocol script {proto['script']} is missing, sir.")
    if drill:
        logger.info(f"protocol '{name}' DRILL: authorized + script present, not launched")
        return ProtocolResult(
            True,
            f"Drill OK: protocol {name} verified — password accepted, script {proto['script']} present. "
            f"Live, it would {proto['description']}. Not executed (drill).",
            spoken=f"Drill passed, sir — {name} is ready and would {proto['description']}. I didn't run it.",
        )
    try:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        subprocess.Popen(
            [sys.executable, str(script), str(os.getpid()), str(_REPO_ROOT), sys.executable],
            cwd=str(_REPO_ROOT),
            creationflags=flags,
            close_fds=True,
        )
        logger.info(f"protocol '{name}' authorized and launched")
        return ProtocolResult(True, f"Protocol {name} authorized. {proto['description'].capitalize()}.",
                              spoken=proto["spoken"])
    except Exception as e:  # noqa: BLE001
        logger.exception("protocol launch failed")
        return ProtocolResult(False, f"I couldn't launch protocol {name}, sir: {type(e).__name__}.")
