"""System-control tools — files, folders, processes, and PowerShell.

Afon manages the owner's own machine: create/delete files & folders, list/kill/start
processes, and run PowerShell (optionally **elevated**, which raises a Windows UAC prompt
the owner accepts). These are powerful and partly irreversible, so:

* destructive deletes refuse anything under ``settings.system_protected_paths`` or a drive root;
* the persona rule is to **confirm before anything destructive or outward-facing**;
* elevated PowerShell runs in its own UAC-approved window (its stdout isn't captured back).

All handlers return a short speakable string and never raise.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from afon.brain.tools.base import clip, tool_error
from afon.config import settings


_REPO_ROOT = Path(__file__).resolve().parents[4]
# Afon's OWN secrets/runtime state — a system delete must never touch these (parity with the
# coding tool's _safe_path). Without this, "delete the .env file" or removing the voiceprint/session
# would be allowed because they're not under a Windows system path.
_SENSITIVE_NAMES = {"voiceprint.json", "afon_jobs.sqlite"}
_SENSITIVE_SUFFIX = {".session", ".session-journal"}
_SENSITIVE_TOPDIRS = {".git", "audit", "backups", ".afon-browser"}


def _enabled() -> bool:
    return settings.system_tools_enabled


def _win_exe(name: str) -> str:
    """Absolute path to a Windows system executable, so it resolves even when the process was
    launched (e.g. by an elevated Task Scheduler job) with a minimal PATH that lacks System32."""
    if sys.platform != "win32":
        return name
    import os

    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = {
        "powershell": rf"{sysroot}\System32\WindowsPowerShell\v1.0\powershell.exe",
        "tasklist": rf"{sysroot}\System32\tasklist.exe",
        "taskkill": rf"{sysroot}\System32\taskkill.exe",
        "cmd": rf"{sysroot}\System32\cmd.exe",
    }
    p = candidates.get(name)
    return p if (p and os.path.isfile(p)) else name


# The edge host is pythonw.exe, which has NO console — so every console child (powershell, tasklist,
# taskkill) POPS A VISIBLE WINDOW unless suppressed. Worse, the task runs ELEVATED, so those show as
# "Administrator:" windows. CREATE_NO_WINDOW alone proved insufficient from the elevated context, so
# every spawn also gets a STARTUPINFO with SW_HIDE (belt-and-suspenders). It's 0/None off Windows.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Shell metacharacters that chain one "launch this app" request into several commands. The process-
# `start` path runs the model's string via cmd.exe (shell=True), so a chained/garbled/injected string
# (e.g. a webhook or non-owner message that reached the reasoning loop) could run destructive ops after
# the launch. A launch only ever needs ONE executable — any of these => refuse and make the owner
# confirm. NOT applied to the PowerShell tool, whose whole purpose is arbitrary pipelines (`;`/`|` are
# normal there). ponytail: a rare dir name containing `&` is a false positive — confirm and it runs.
_SHELL_CHAIN_OPS = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")


def _has_shell_chain(command: str) -> bool:
    return any(op in command for op in _SHELL_CHAIN_OPS)


def _hidden_startupinfo():
    """A STARTUPINFO that forces the child's window hidden (SW_HIDE) — pairs with CREATE_NO_WINDOW so
    even an elevated console child never shows. None off Windows."""
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


async def _dispatch(op: str, args: dict, local, timeout: float | None = None) -> str:
    """Run a PC op on the laptop executor if one is connected to THIS brain (the VPS case); else run
    it locally (correct when the brain itself runs on the laptop). This is what gives the 24/7 VPS
    brain full control of the laptop's files/processes.

    ``timeout`` overrides the default forward window for the rare long op (a full test run takes
    minutes, and the default would report a timeout while it was still succeeding)."""
    from afon.brain.pc_link import PC_LINK

    if PC_LINK.active:
        try:
            if timeout is None:
                return await PC_LINK.forward(op, args)
            return await PC_LINK.forward(op, args, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return (f"Your laptop didn't respond, sir ({type(e).__name__}) — it may be offline or the "
                    "executor isn't running.")
    return await local(args)


def _sensitive(p: Path) -> bool:
    """True for Afon's own credentials/state files (refused by destructive file ops)."""
    name = p.name
    if (name == ".env" or name.startswith(".env")
            or name in _SENSITIVE_NAMES or p.suffix in _SENSITIVE_SUFFIX):
        return True
    try:
        rp = p.resolve()
        if rp == _REPO_ROOT or _REPO_ROOT in rp.parents:
            parts = rp.relative_to(_REPO_ROOT).parts
            if parts and parts[0] in _SENSITIVE_TOPDIRS:
                return True
    except (OSError, ValueError):
        pass
    return False


def _protected(p: Path) -> bool:
    rp = p.resolve()
    # A drive root (C:\) or anything under a protected system path is off-limits.
    if rp.parent == rp:
        return True
    roots = [r.strip() for r in settings.system_protected_paths.split(",") if r.strip()]
    for root in roots:
        try:
            rootp = Path(root).resolve()
            if rp == rootp or rootp in rp.parents:
                return True
        except OSError:
            continue
    return False


async def _file_op_local(args: dict) -> str:
    if not _enabled():
        return "System tools are disabled, sir (AFON_SYSTEM_TOOLS_ENABLED)."
    action = (args.get("action") or "").strip().lower()
    raw = (args.get("path") or "").strip()
    if not raw:
        return "Which path, sir?"
    p = Path(raw).expanduser()
    try:
        if action == "create_folder":
            p.mkdir(parents=True, exist_ok=True)
            return f"Created the folder {p}, sir."
        if action == "create_file":
            if _sensitive(p):
                return f"I won't overwrite {p}, sir — that's one of my own credential/state files."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.get("content") or "", encoding="utf-8")
            return f"Created the file {p}, sir."
        if action == "delete_file":
            if not p.exists():
                return f"There's no file at {p}, sir."
            if _protected(p):
                return f"I won't delete {p}, sir — it's a protected system path."
            if _sensitive(p):
                return f"I won't delete {p}, sir — that's one of my own credential/state files."
            p.unlink()
            return f"Deleted the file {p}, sir."
        if action == "delete_folder":
            if not p.exists():
                return f"There's no folder at {p}, sir."
            if _protected(p) or _sensitive(p):
                return f"I won't delete {p}, sir — it's protected (system or my own state)."
            shutil.rmtree(p)
            return f"Deleted the folder {p} and its contents, sir."
        if action == "list":
            if not p.is_dir():
                return f"{p} isn't a folder, sir."
            entries = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
            return clip(f"{p} contains: " + ", ".join(entries) if entries else f"{p} is empty, sir.", 1500)
        return f"I don't know the file action '{action}', sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("file operation", e)


async def _process_op_local(args: dict) -> str:
    if not _enabled():
        return "System tools are disabled, sir."
    action = (args.get("action") or "").strip().lower()
    name = (args.get("name") or "").strip()
    pid = args.get("pid")
    command = (args.get("command") or "").strip()
    try:
        if action == "list":
            r = await asyncio.to_thread(
                subprocess.run,
                [_win_exe("tasklist"), "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo(),
            )
            lines = [ln for ln in r.stdout.splitlines() if (not name or name.lower() in ln.lower())]
            return clip(f"{len(lines)} process(es)" + (f" matching '{name}'" if name else "")
                        + ":\n" + "\n".join(lines[:25]), 1800)
        if action == "kill":
            if pid:
                cmd = [_win_exe("taskkill"), "/PID", str(pid), "/T", "/F"]
            elif name:
                cmd = [_win_exe("taskkill"), "/IM", name, "/T", "/F"]
            else:
                return "Tell me the process name or pid to kill, sir."
            r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=20,
                                        creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo())
            ok = r.returncode == 0
            return (f"Killed {name or pid}, sir." if ok
                    else f"Couldn't kill {name or pid}: {clip(r.stderr or r.stdout, 160)}")
        if action in ("suspend", "resume"):
            # Pause/continue an app WITHOUT killing it (freeze a game/video while the owner steps away,
            # then resume) — used by proactive interventions and by voice ("pause my game"). Suspends
            # ALL processes matching the name (a browser/game is often several). ntdll NtSuspend/Resume
            # is undocumented but present on every Windows and needs no external tool. ponytail: name/pid
            # match only; upgrade to window-scoped freeze if a workflow ever needs a single tab.
            if not (name or pid):
                return f"Tell me the process name or pid to {action}, sir."
            fn = "NtSuspendProcess" if action == "suspend" else "NtResumeProcess"
            sel = f"Get-Process -Id {int(pid)}" if pid else f"Get-Process -Name '{name.replace(chr(39), '')}'"
            ps = (
                "Add-Type -Namespace N -Name P -MemberDefinition '"
                "[DllImport(\"ntdll.dll\")] public static extern uint NtSuspendProcess(System.IntPtr h);"
                "[DllImport(\"ntdll.dll\")] public static extern uint NtResumeProcess(System.IntPtr h);';"
                f"$ps=@({sel} -ErrorAction Stop);foreach($p in $ps){{[N.P]::{fn}($p.Handle)|Out-Null}};"
                "Write-Output $ps.Count"
            )
            r = await asyncio.to_thread(subprocess.run,
                                        [_win_exe("powershell"), "-NoProfile", "-NonInteractive", "-Command", ps],
                                        capture_output=True, text=True, timeout=15,
                                        creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo())
            if r.returncode != 0:
                return f"Couldn't {action} {name or pid}: {clip(r.stderr or r.stdout, 160)}"
            n = (r.stdout or "0").strip().splitlines()[-1:] or ["0"]
            verb = "Paused" if action == "suspend" else "Resumed"
            return f"{verb} {name or pid} ({n[0]} process(es)), sir."
        if action == "start":
            if not command:
                return "What should I start, sir? Give me a command or executable."
            if _has_shell_chain(command):
                return (f"That start command chains shell operations ({clip(command, 120)}) — I won't "
                        "auto-run a chained command. Give me one executable to launch, or confirm it "
                        "explicitly as a PowerShell command, sir.")
            # Detached so it outlives this turn.
            flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED|NEW_GROUP
            await asyncio.to_thread(
                lambda: subprocess.Popen(command, shell=True, creationflags=flags))
            return f"Started: {clip(command, 120)}, sir."
        return f"I don't know the process action '{action}', sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("process operation", e)


async def _run_powershell_local(args: dict) -> str:
    if not _enabled():
        return "System tools are disabled, sir."
    command = (args.get("command") or "").strip()
    as_admin = bool(args.get("as_admin"))
    if not command:
        return "What PowerShell command, sir?"
    try:
        if as_admin:
            # Relaunch elevated via UAC. Output goes to its own window (not captured here).
            launcher = (
                f"Start-Process powershell -Verb RunAs -ArgumentList "
                f"'-NoProfile','-Command',{_ps_quote(command)}"
            )
            await asyncio.to_thread(
                subprocess.run,
                [_win_exe("powershell"), "-NoProfile", "-NonInteractive", "-Command", launcher],
                capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo(),
            )
            return ("I launched that as administrator, sir — accept the Windows prompt to let it run. "
                    "(Elevated output runs in its own window.)")
        r = await asyncio.to_thread(
            subprocess.run,
            [_win_exe("powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", command],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo(),
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return clip(out or "Done, sir — no output.", 1800)
    except Exception as e:  # noqa: BLE001
        return tool_error("PowerShell", e)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


async def _open_url_local(args: dict) -> str:
    if not _enabled():
        return "System tools are disabled, sir."
    url = (args.get("url") or "").strip()
    if not url:
        return "Which URL, sir?"
    if "://" not in url:
        url = "https://" + url
    try:
        if sys.platform == "win32":
            flags = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
            await asyncio.to_thread(
                lambda: subprocess.Popen([_win_exe("cmd"), "/c", "start", "", url], creationflags=flags))
        else:
            await asyncio.to_thread(lambda: subprocess.Popen(["xdg-open", url]))
        return f"Opened {clip(url, 100)} in your browser, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("open url", e)


async def _open_app_local(args: dict) -> str:
    if not _enabled():
        return "System tools are disabled, sir."
    app = (args.get("app") or "").strip()
    if not app:
        return "Which app should I open, sir?"
    try:
        if sys.platform == "win32":
            flags = 0x00000008 | 0x00000200
            await asyncio.to_thread(
                lambda: subprocess.Popen([_win_exe("cmd"), "/c", "start", "", app], shell=False, creationflags=flags))
        else:
            await asyncio.to_thread(lambda: subprocess.Popen([app]))
        return f"Launched {clip(app, 80)}, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("open app", e)


async def _screenshot_local(args: dict) -> str:
    """Capture the primary screen laptop-side and return it as JSON ``{"dims","b64"}`` (JPEG bytes,
    base64). Runs entirely on the machine with the display (the pc_agent host), so the brain — even
    on the VPS — gets the actual IMAGE back instead of a path it can't read. '{}' on error/non-Win.

    The PowerShell is a plain capture-to-file (same shape as the long-standing screenshot); the
    base64 is done here in Python, not in the scanned script, to keep the AV signature low."""
    if sys.platform != "win32":
        return "{}"
    import base64
    import json as _json
    import os
    import tempfile

    shot = os.path.join(tempfile.gettempdir(), "afon_screen.jpg")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Jpeg);"
        "Write-Output (\"{0}x{1}\" -f $b.Width,$b.Height)"
    )
    try:
        proc = await asyncio.to_thread(lambda: subprocess.run(
            [_win_exe("powershell"), "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo()))
        dims = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        with open(shot, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        try:
            os.remove(shot)
        except OSError:
            pass
        return _json.dumps({"dims": dims[0], "b64": b64})
    except Exception:  # noqa: BLE001 — capture must never raise into the caller
        return "{}"


async def _media_pause_local(args: dict) -> str:
    """Toggle play/pause on whatever is playing on the laptop by sending the media key
    (VK_MEDIA_PLAY_PAUSE, 0xB3) — works for browser video (YouTube/Netflix), Spotify, VLC, etc.
    Runs on the machine with the display. Returns a short note; never raises."""
    if sys.platform != "win32":
        return "media control isn't available on this host, sir."
    ps = (
        "Add-Type -Namespace W -Name K -MemberDefinition '[DllImport(\"user32.dll\")] public "
        "static extern void keybd_event(byte b, byte s, uint f, System.IntPtr e);';"
        "[W.K]::keybd_event(0xB3,0,0,[IntPtr]::Zero);"
        "[W.K]::keybd_event(0xB3,0,2,[IntPtr]::Zero)"
    )
    try:
        await asyncio.to_thread(lambda: subprocess.run(
            [_win_exe("powershell"), "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW, startupinfo=_hidden_startupinfo()))
        return "Toggled play/pause on your media, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("media pause", e)


def _activity_snapshot_ctypes() -> str:
    """Foreground window (process + title) + idle seconds via DIRECT Win32 calls — no subprocess.

    The presence poller runs this every ~45s. Spawning PowerShell that often popped an (elevated)
    console window each time and occasionally left hung processes; ctypes does it IN-PROCESS, so there
    is no window, no spawn, and nothing to hang. Returns the same JSON the PS version did."""
    import ctypes
    import json as _json
    import os
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Set proper 64-bit types: HWND is a pointer, so the default c_int restype would TRUNCATE a
    # high handle on 64-bit Windows and corrupt every downstream call. This is the likely cause of the
    # intermittent failures that fell back to PowerShell.
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    hwnd = user32.GetForegroundWindow()
    # Title.
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value or ""
    # Owning process id -> process name.
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        try:
            size = wintypes.DWORD(260)
            pbuf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                app = os.path.splitext(os.path.basename(pbuf.value))[0]
        finally:
            kernel32.CloseHandle(h)
    # Idle seconds since the last user input.
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    idle = 0.0
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        idle = max(0.0, (kernel32.GetTickCount() - lii.dwTime) / 1000.0)
    return _json.dumps({"app": app, "title": title, "idle": round(idle, 1)})


async def _activity_snapshot_local(args: dict) -> str:
    """Return a compact JSON snapshot of the active window + idle seconds, e.g.
    ``{"app":"chrome","title":"YouTube - …","idle":3.2}``. '{}' on non-Windows or any error.
    Read-only perception op — the presence poller calls this (not the LLM). Pure in-process ctypes:
    it NEVER spawns a subprocess, so there is no window and nothing to hang. If a call ever fails we
    just skip the sample ('{}') — the presence layer tolerates a gap; we do NOT fall back to spawning
    PowerShell (the old Add-Type path popped elevated windows and hung)."""
    if sys.platform != "win32":
        return "{}"
    try:
        snap = await asyncio.to_thread(_activity_snapshot_ctypes)
    except Exception as e:  # noqa: BLE001 — perception must never raise into the poller
        from loguru import logger
        logger.debug(f"activity_snapshot ctypes skipped ({type(e).__name__}: {e})")
        return "{}"
    # Merge the speaker-gate's room verdict (camera check fired on an unrecognized/overlapping voice)
    # so the brain knows who's in the room, not just which window is focused. Fresh = last 10 min.
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path
        rc = _json.loads((_Path.home() / ".afon" / "room_context.json").read_text(encoding="utf-8"))
        if _time.time() - float(rc.get("ts", 0)) < 600:
            d = _json.loads(snap or "{}")
            d["room"] = {"reason": rc.get("reason"), "verdict": rc.get("verdict"),
                         "age_s": int(_time.time() - float(rc.get("ts", 0)))}
            snap = _json.dumps(d)
    except Exception:  # noqa: BLE001 — no/stale/corrupt room context just means no merge
        pass
    return snap


# ---- Public handlers: forward to the laptop executor if connected, else run locally -------------
async def activity_snapshot(args: dict) -> str:
    """Perception op: forwards to the laptop executor (VPS case), else runs locally."""
    return await _dispatch("activity_snapshot", args, _activity_snapshot_local)


async def screenshot(args: dict) -> str:
    """Capture the screen and return JSON {dims,b64}. Forwards to the laptop so the VPS brain gets
    the actual image bytes (fixes the old capture-on-laptop / read-on-brain split)."""
    return await _dispatch("screenshot", args, _screenshot_local)


async def media_pause(args: dict) -> str:
    """Pause/resume the owner's media (sends the media play/pause key on the laptop)."""
    return await _dispatch("media_pause", args, _media_pause_local)


async def file_op(args: dict) -> str:
    return await _dispatch("file_op", args, _file_op_local)


async def process_op(args: dict) -> str:
    return await _dispatch("process_op", args, _process_op_local)


async def run_powershell(args: dict) -> str:
    return await _dispatch("run_powershell", args, _run_powershell_local)


async def open_url(args: dict) -> str:
    return await _dispatch("open_url", args, _open_url_local)


async def open_app(args: dict) -> str:
    return await _dispatch("open_app", args, _open_app_local)


# What the laptop executor (edge/pc_agent.py) runs LOCALLY for each forwarded op (no re-dispatch).
LOCAL_HANDLERS = {
    "file_op": _file_op_local,
    "process_op": _process_op_local,
    "run_powershell": _run_powershell_local,
    "open_url": _open_url_local,
    "open_app": _open_app_local,
    "activity_snapshot": _activity_snapshot_local,
    "screenshot": _screenshot_local,
    "media_pause": _media_pause_local,
}


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "file_op",
            "description": (
                "Create or delete files and folders on the owner's PC, or list a folder. "
                "Deleting is irreversible — confirm with him first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create_file", "create_folder", "delete_file", "delete_folder", "list"],
                    },
                    "path": {"type": "string", "description": "Absolute or ~ path."},
                    "content": {"type": "string", "description": "Text for create_file."},
                },
                "required": ["action", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_op",
            "description": (
                "Manage processes on the owner's PC: list (optionally filtered by name), kill by name "
                "or pid, start a new process/app, or SUSPEND/RESUME an app to pause then continue it "
                "without losing its state (e.g. freeze a game/video while he steps away, then resume). "
                "Killing apps is disruptive — confirm first; suspend/resume is safe and reversible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "kill", "start", "suspend", "resume"]},
                    "name": {"type": "string", "description": "Process image/name, e.g. 'chrome' or 'chrome.exe'."},
                    "pid": {"type": "integer", "description": "Process id (for kill/suspend/resume)."},
                    "command": {"type": "string", "description": "Command/executable to start."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": (
                "Run a PowerShell command on the owner's PC and get the output. Set as_admin=true to "
                "run elevated (raises a Windows UAC prompt he must accept). Use for system tasks, "
                "settings, installs. Confirm anything destructive first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The PowerShell command."},
                    "as_admin": {"type": "boolean", "description": "Run elevated (UAC). Default false."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the owner's default browser on his PC (e.g. open YouTube, a "
                           "website, a search). Use this to 'open the browser' / 'open YouTube'.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The URL or site, e.g. youtube.com"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch an application on the owner's PC by name or path ('notepad', "
                           "'spotify', 'code', 'explorer'). Use for 'open X', 'launch X', 'start "
                           "X'. Local apps only — for a website use open_url.",
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string", "description": "App name or path to launch."}},
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_pause",
            "description": "Pause (or resume) whatever media is playing on the owner's PC — a browser "
                           "video, Spotify, VLC, etc. Toggles play/pause. Use for 'pause the video', "
                           "'pause my music', 'resume it'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {
    "file_op": file_op, "process_op": process_op, "run_powershell": run_powershell,
    "open_url": open_url, "open_app": open_app, "media_pause": media_pause,
}
