"""Laptop PC-control executor — gives the 24/7 VPS brain full control of THIS PC.

Run this on the laptop as a background task. It connects OUT to the brain's ``/control`` socket over
Tailscale (no inbound ports) and executes the PC ops the brain forwards: create/delete/edit files &
folders, list/kill/start processes (task-manager), run PowerShell, open the browser / a URL / an app.
So when you tell Watari from your phone "open YouTube on my laptop" or "kill Chrome" or "clean up these
processes", it runs here. The existing system-tool guards still apply (protected paths, Watari's own
secrets). When this isn't running, the brain reports the laptop offline instead of acting on the VPS.

    uv run python -m jarvis.edge.pc_agent
"""

from __future__ import annotations

import asyncio
import json
import platform
import random
from urllib.parse import urlparse, urlunparse

from loguru import logger

from jarvis.brain.tools.system import LOCAL_HANDLERS as _SYS_HANDLERS
from jarvis.brain.tools.camera import LOCAL_HANDLERS as _CAM_HANDLERS
from jarvis.brain.tools.browser import LOCAL_HANDLERS as _BROWSER_HANDLERS
from jarvis.brain.tools.coding import LOCAL_HANDLERS as _REPO_HANDLERS
from jarvis.brain.tools.localplay import LOCAL_HANDLERS as _AUDIO_HANDLERS
from jarvis.brain.tools.documents import LOCAL_HANDLERS as _DOC_HANDLERS
from jarvis.brain.tools.audioout import LOCAL_HANDLERS as _AUDIO_OUT_HANDLERS
from jarvis.config import settings

# The laptop executor runs system ops (files/processes/screenshot), camera ops (presence/enroll/
# capture), the interactive BROWSER, and the REPO ops locally — the camera, the owner's face refs and
# the browser profile with his logged-in sessions are all on this machine, not on the VPS brain. A
# browser driven brain-side would be headless on a server he can't see, with an empty cookie jar.
# Repo ops matter for the same reason: the VPS copy is a deploy artefact whose git points at the real
# remote and whose files are overwritten by every deploy, so edits and commits belong here. AUDIO too:
# the VPS has ffplay installed, so playing there succeeded silently into a machine with no speakers.
# Documents likewise: "read this file" names a path on HIS disk, which doesn't exist on the VPS.
LOCAL_HANDLERS = {**_SYS_HANDLERS, **_CAM_HANDLERS, **_BROWSER_HANDLERS, **_REPO_HANDLERS,
                  **_AUDIO_HANDLERS, **_DOC_HANDLERS, **_AUDIO_OUT_HANDLERS}

# Bumped when the executor's behaviour changes, so the brain log confirms which code is live after a
# restart (e.g. the elevated-session PATH / absolute-exe fixes).
CODE_VERSION = "2026-06-14-winexe"


def _control_url() -> str:
    if settings.pc_control_url:
        return settings.pc_control_url
    u = urlparse(settings.brain_ws_url)
    return urlunparse(u._replace(path="/control"))


# Last-line refuse-list for an ELEVATED process. The brain's own guards (system.py path/secret
# checks + confirm tier) run first; this catches a compromised/confused brain anyway. Substring
# match on the flattened command — crude on purpose, these strings have no legitimate use here.
_REFUSED_SUBSTRINGS = (
    "format-volume", "format c:", "format d:", "clear-disk", "initialize-disk",
    "remove-item c:\\ ", "remove-item -path c:\\ ", "rd /s /q c:\\", "del /f /s /q c:\\",
    "cipher /w", "bcdedit", "vssadmin delete", "reg delete hklm", "diskpart",
)


def _refused(op: str, args: dict) -> str | None:
    blob = f"{op} {json.dumps(args, ensure_ascii=False)}".lower()
    for bad in _REFUSED_SUBSTRINGS:
        if bad in blob:
            return bad
    return None


def _proc_running(name: str) -> bool:
    """Is a process with this image name in the task list? Windows tasklist; best-effort."""
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"],
                             capture_output=True, text=True, timeout=10).stdout
        return name.lower() in (out or "").lower()
    except Exception:  # noqa: BLE001 — no tasklist / timeout -> can't verify
        return False


def _verify_effect(op: str, args: dict) -> str | None:
    """C5 see→act→VERIFY: after an op runs, confirm the intended effect actually happened, so Watari
    reports "done and verified" (or flags a mismatch) instead of blindly trusting the exit. Returns a
    short note, or None when the op isn't verifiable (open_url/open_app/run_powershell are best-effort —
    there's no reliable post-state to check). ponytail: existence checks + a tasklist probe; the upgrade
    path is per-app window/clipboard assertions if a specific workflow needs them."""
    import os
    if op == "file_op":
        action, path = args.get("action"), args.get("path")
        if action in ("create_file", "create_folder") and path:
            return "verified — it exists now" if os.path.exists(path) else "WARNING: not found after create"
        if action in ("delete_file", "delete_folder") and path:
            return "verified — it's gone" if not os.path.exists(path) else "WARNING: still present after delete"
    if op == "process_op" and args.get("action") == "kill" and args.get("name"):
        return "verified — not running" if not _proc_running(args["name"]) else "WARNING: still running after kill"
    return None


async def _run_op(op: str, args: dict) -> tuple[bool, str]:
    """Execute one forwarded op. Every laptop-side capability — camera, screenshots, file and
    process control, PowerShell, the interactive browser — funnels through here, so this is where
    they all get end-to-end tracking: outcome, duration, and the op's arguments (scrubbed)."""
    import time as _time

    from jarvis.shared import errors as _err

    started = _time.monotonic()

    def _track(ok: bool, detail: str = "") -> None:
        _err.record_op("pc", op, ok=ok, detail=detail,
                       duration_ms=(_time.monotonic() - started) * 1000,
                       slow_ms=20_000,   # a PC op the owner is waiting on shouldn't take 20s
                       context={"args": str(args)[:200]})

    fn = LOCAL_HANDLERS.get(op)
    if fn is None:
        _track(False, f"unknown PC op '{op}'")
        return False, f"unknown PC op '{op}'"
    hit = _refused(op, args)
    if hit:
        logger.warning(f"pc-agent: REFUSED catastrophic op {op} (matched '{hit}')")
        _track(False, f"refused: matched destructive pattern '{hit}'")
        return False, "I won't run that on the laptop — it's on the destructive-op refuse list, sir."
    try:
        out = str(await fn(args or {}))
        note = _verify_effect(op, args or {})   # C5: confirm the effect landed
        if note:
            out = f"{out} ({note})"
            if note.startswith("WARNING"):
                logger.warning(f"pc-agent: {op} verify mismatch — {note}")
        # A handler that returns a failure sentence instead of raising still failed the owner —
        # "no webcam, it's in use, or access is blocked" is an outage, not an answer.
        failed = _err.looks_failed(out) or (note or "").startswith("WARNING")
        _track(not failed, out[:200] if failed else "")
        return True, out
    except Exception as e:  # noqa: BLE001
        logger.exception(f"pc-agent: op {op} failed")
        _track(False, f"{type(e).__name__}: {e}")
        return False, f"That failed on the laptop ({type(e).__name__}), sir."


async def _session(url: str, token: str | None) -> None:
    from websockets.asyncio.client import connect

    headers = {"Authorization": f"Bearer {token}"} if token else None
    # ping_timeout 75s (not 20): a brief network blip on a 24/7 idle link shouldn't drop the
    # control channel. Matches the brain<->edge keepalive; stops the ~15-min reconnect churn.
    async with connect(url, additional_headers=headers, ping_interval=20,
                       ping_timeout=75, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "pc_hello", "host": platform.node(), "ver": CODE_VERSION}))
        logger.info(f"pc-agent: connected to {url} as '{platform.node()}' — ready for commands")

        # Ship this executor's failures to the brain too. Camera checks, PC control and file ops all
        # run HERE, so without this the owner could hit a laptop-side failure that the brain's
        # journal never learns about. Fire-and-forget and fail-quiet: the local journal is the
        # source of truth, and reporting must never disturb the command channel.
        from jarvis.shared import errors as _err

        def _ship(entry: dict) -> None:
            try:
                asyncio.get_running_loop().create_task(
                    ws.send(json.dumps({"type": "pc_error", "entry": entry}))
                )
            except Exception:  # noqa: BLE001
                pass

        _err.set_shipper(_ship)
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("type") != "pc_command":
                    continue
                op, args, cid = data.get("op"), data.get("args") or {}, data.get("id")
                # Adopt the brain's turn id when it forwards one, so a laptop-side failure lands
                # under the same correlation key as the turn that caused it.
                _err.new_turn(data.get("turn_id") or None)
                logger.info(f"pc-agent: exec {op}({args})")
                ok, output = await _run_op(op, args)
                await ws.send(json.dumps({"type": "pc_result", "id": cid, "ok": ok, "output": output}))
        finally:
            _err.set_shipper(None)   # the socket is gone; go back to local-only journalling


def _ensure_windows_path() -> None:
    """A process launched by an elevated Task Scheduler job can start with a minimal PATH that
    lacks System32 — so powershell/tasklist/taskkill/cmd come back FileNotFoundError. Prepend the
    standard Windows system dirs so every forwarded op resolves its executable."""
    if platform.system() != "Windows":
        return
    import os

    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    wanted = [rf"{sysroot}\System32", rf"{sysroot}\System32\WindowsPowerShell\v1.0", sysroot,
              rf"{sysroot}\System32\Wbem"]
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    for d in reversed(wanted):
        if d.lower() not in [p.lower() for p in parts]:
            parts.insert(0, d)
    os.environ["PATH"] = os.pathsep.join(parts)


async def main() -> None:
    _ensure_windows_path()
    url = _control_url()
    token = settings.api_auth_token
    logger.info(f"pc-agent starting → {url}")
    backoff = 1.0
    while True:
        try:
            await _session(url, token)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — any drop -> reconnect with backoff
            logger.warning(f"pc-agent link error ({type(e).__name__}: {e}); reconnecting")
        delay = min(backoff, 30.0) * (0.7 + 0.6 * random.random())
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, 30.0)


if __name__ == "__main__":
    from jarvis.edge._supervisor import run_supervised

    run_supervised("pc_agent", main)
