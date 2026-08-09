"""Protocols — named, password-gated executable routines (FRIDAY/AFON style).

A protocol is a small standalone script Afon runs ONLY when given the matching password.
This is the identity gate: Afon asks for the password first (persona rule), and the runner
verifies it in constant time before launching anything. The scripts live in
``src/afon/protocols/`` and are launched **detached** so they survive Afon being killed
(needed for the stop/restart protocols).

The eight shipped protocols, in two kinds.

ROUTED — they act on the OWNER'S LAPTOP, so they carry a ``pc_command`` and may only leave via
PC_LINK (``run_protocol_async``). Running one locally on the VPS would aim laptop-era logic at the
server — ragnarok's POSIX branch is ``shutdown -r +1``, pointed at the brain host — so the sync
launcher refuses them outright:
  * ``goodnight`` — stops Afon (terminates the running edge process).
  * ``phoenix``   — restarts Afon (kills the old process, starts a fresh one).
  * ``ragnarok``  — restarts the laptop.

BRAIN-SIDE — they run here, on whichever host the brain is:
  * ``backup``      — backs up Afon's memory.
  * ``ping``        — sends a phone push test.
  * ``diagnostics`` — writes a health report.
  * ``auditpack``   — archives the audit logs.
  * ``checkpoint``  — archives key non-secret context (README/SECURITY/TODO/pyproject + memory).

Passwords come from settings (``AFON_PROTOCOL_*_PASSWORD``) — CHANGE the defaults in .env.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_DIR = _REPO_ROOT / "src" / "afon" / "protocols"


def _registry() -> dict[str, dict]:
    return {
        "goodnight": {
            "script": "goodnight.py",
            "password": settings.protocol_goodnight_password,
            "spoken": "Goodnight, sir. Powering down.",
            "description": "stops Afon",
            # Stop the voice edge only — pc_agent stays up so phoenix can revive him remotely. The
            # marker tells AfonEdgeGuard this silence was ORDERED, otherwise the guard would treat
            # a missing edge as a crash and undo goodnight within 30 minutes. restart_edge.ps1
            # clears it, so phoenix/the daily refresh bring him back.
            "pc_command": ("New-Item -ItemType File -Force -Path 'C:\\Afon\\logs\\edge_stopped_by_owner' "
                           "| Out-Null; Stop-ScheduledTask -TaskName AfonEdge; "
                           "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                           "Where-Object { $_.CommandLine -match 'afon\\.edge\\.assistant' } | "
                           "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"),
        },
        "phoenix": {
            "script": "phoenix.py",
            "password": settings.protocol_phoenix_password,
            "spoken": "Rebooting myself, sir. Back in a moment.",
            "description": "restarts Afon",
            "pc_command": "Start-ScheduledTask -TaskName AfonEdgeRefresh",
        },
        "ragnarok": {
            "script": "ragnarok.py",
            "password": settings.protocol_ragnarok_password,
            "spoken": "Restarting the machine, sir. Save your work.",
            "description": "restarts the laptop",
            "pc_command": 'shutdown /r /t 15 /c "Afon protocol Ragnarok: restarting."',
        },
        "backup": {
            "script": "backup.py",
            "password": settings.protocol_backup_password,
            "spoken": "Memory backup started, sir.",
            "description": "backs up Afon memory",
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
            "description": "archives key non-secret Afon context",
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


#: Brain-side protocols that produce a FILE, and the glob that finds it. Three of the five wrote
#: their output to ``backups/`` on the brain host and stopped there — which was fine while the brain
#: was the laptop, and became useless the moment it moved to the VPS: the owner was told "diagnostics
#: written" and had no way to read a word of it. Anything listed here gets delivered to him.
_REPORTS = {
    "diagnostics": "afon-diagnostics-*.txt",
    "auditpack": "afon-audit-*.zip",
    "checkpoint": "afon-checkpoint-*.zip",
}
#: How long to wait for a detached script to finish writing before giving up on delivery.
_REPORT_WAIT_S = 90.0
# Tolerance on the "written after this run started" test. A file written strictly AFTER a
# `time.time()` reading can still carry an mtime a hair BEFORE it — filesystem timestamp
# granularity is coarser than the clock, and it rounds down. Measured on this machine:
# 292 of 3000 writes (~10%) landed earlier than a timestamp taken immediately before the write.
# Without this slop the check calls a brand-new report stale, waits the full 90s and delivers
# nothing — silently, which is the exact failure _deliver_report exists to prevent. Found because
# it made bench/test_protocol_reports.py flaky (green standalone, red in the gate); the flake was
# the product defect showing through, not a test artefact.
# 2s is far below the gap to any genuinely stale report (the next run is hours away) and far above
# any plausible timestamp granularity.
_MTIME_SLOP_S = 2.0


async def _deliver_report(name: str, since: float) -> None:
    """Wait for a brain-side protocol's artifact, then send it to the owner.

    Fail-quiet: this is a courtesy on top of a protocol that has already run. If Telegram is not
    configured, or the script wrote nothing, the owner is no worse off than before — but the failure
    is logged, because silence here is exactly the bug being fixed.
    """
    pattern = _REPORTS.get(name)
    if not pattern:
        return
    backups = _REPO_ROOT / "backups"
    deadline = asyncio.get_running_loop().time() + _REPORT_WAIT_S
    newest = None
    while asyncio.get_running_loop().time() < deadline:
        # Only files written AFTER this run started — otherwise a failed run happily delivers the
        # previous week's report and looks like it succeeded.
        fresh = [p for p in backups.glob(pattern)
                 if p.is_file() and p.stat().st_mtime >= since - _MTIME_SLOP_S]
        if fresh:
            newest = max(fresh, key=lambda p: p.stat().st_mtime)
            break
        await asyncio.sleep(2.0)
    if newest is None:
        logger.warning(f"protocol '{name}': no {pattern} appeared within {_REPORT_WAIT_S:.0f}s — "
                       "nothing to deliver")
        return
    try:
        from afon.brain.tools.telegram import send_telegram

        kb = newest.stat().st_size / 1024
        caption = f"Protocol {name} — {newest.name} ({kb:.0f} KB), sir."
        if newest.suffix == ".txt":
            # A text report is more use read than downloaded, so lead with the content and attach
            # the file behind it.
            body = newest.read_text(encoding="utf-8", errors="replace").strip()
            caption = f"{caption}\n\n{body[:2500]}" + ("\n…(truncated)" if len(body) > 2500 else "")
        out = await send_telegram({"message": caption, "file": str(newest)})
        logger.info(f"protocol '{name}': delivered {newest.name} to the owner ({out[:60]})")
    except Exception as e:  # noqa: BLE001 — delivery is best-effort; the protocol itself already ran
        logger.warning(f"protocol '{name}': could not deliver {newest.name}: {type(e).__name__}: {e}")


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
        started = time.time()
        res = run_protocol(name, password, drill=drill)
        # A brain-side protocol that writes a file: follow it up and hand the file to the owner. Not
        # awaited — the script is detached and takes seconds to minutes, and the turn must not block
        # on it. Skipped for drills, which deliberately write nothing.
        if res.ok and not drill and name.strip().lower() in _REPORTS:
            asyncio.create_task(_deliver_report(name.strip().lower(), started))
        return res

    from afon.brain.pc_link import PC_LINK

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
    # A routed protocol targets the OWNER'S machine and must only ever leave via PC_LINK. Reaching
    # this line means some caller took the sync path for one of them, which on the VPS would execute
    # laptop-era logic against the brain host — ragnarok's POSIX branch is `shutdown -r +1`, aimed at
    # the server. No caller does that today; the guard is here because the function is public and the
    # failure would be catastrophic and silent rather than noisy.
    if proto.get("pc_command"):
        logger.error(f"protocol '{name}' reached the sync launcher — routed protocols must go through "
                     "run_protocol_async; refusing to execute it on this host")
        return ProtocolResult(
            False,
            f"Protocol {name} acts on your laptop and can only be sent there, sir — I won't run it here.",
            spoken=f"{name} has to run on your laptop, sir, so I didn't run it.",
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
