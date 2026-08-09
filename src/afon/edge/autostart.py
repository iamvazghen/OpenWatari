"""Cross-platform autostart installer for Afon's edge services.

ONE command installs OS-native autostart for the two background processes that make the laptop a
living endpoint, so they launch automatically at every boot/login and stay running (auto-restart
on crash), with no console window:

  * ``edge``     -> ``afon.edge.assistant``  — the always-listening "hey afon" voice front-end
  * ``pc_agent`` -> ``afon.edge.pc_agent``    — the PC-control executor the brain drives

Per-OS mechanism (each is the native "run in background, start at device start" facility):

  * Windows -> Scheduled Tasks, trigger ``AtLogOn``, launched with the venv ``pythonw.exe`` (no
               console window), ``RestartCount`` so it survives crashes. ``pc_agent`` runs with
               ``RunLevel Highest`` (elevated) so the brain gets true full control. Registering an
               elevated/All-users task needs admin, so on Windows this self-elevates once via UAC.
  * macOS   -> launchd LaunchAgents in ``~/Library/LaunchAgents`` with ``RunAtLoad`` + ``KeepAlive``.
  * Linux   -> systemd ``--user`` units (``Restart=always``) enabled + ``loginctl enable-linger`` so
               they start at boot even before you log in.

Usage::

    uv run python -m afon.edge.autostart install            # both services
    uv run python -m afon.edge.autostart install edge        # just one
    uv run python -m afon.edge.autostart uninstall
    uv run python -m afon.edge.autostart status

The installer is idempotent: re-running ``install`` refreshes the existing entries.
"""

from __future__ import annotations

import getpass
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

# repo root = .../src/afon/edge/autostart.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]

# Canonical Windows task names kept identical to the legacy installers so we never end up with two.
SERVICES: dict[str, dict] = {
    "edge": {
        "module": "afon.edge.assistant",
        "label": "Afon voice edge (always-listening 'hey afon')",
        "win_task": "AfonEdge",
        "unit": "afon-edge",          # linux systemd unit / macOS label suffix
        "elevated": False,
    },
    "pc_agent": {
        "module": "afon.edge.pc_agent",
        "label": "Afon PC-control agent (brain-driven control of this PC)",
        "win_task": "AfonPcAgent",
        "unit": "afon-pc-agent",
        "elevated": True,
    },
}


def _python_exe(windowless: bool) -> Path:
    """The venv interpreter. On Windows pick pythonw.exe (no console window)."""
    if platform.system() == "Windows":
        name = "pythonw.exe" if windowless else "python.exe"
        return REPO_ROOT / ".venv" / "Scripts" / name
    return REPO_ROOT / ".venv" / "bin" / "python"


def _resolve(names: list[str]) -> list[str]:
    if not names:
        return list(SERVICES)
    bad = [n for n in names if n not in SERVICES]
    if bad:
        raise SystemExit(f"unknown service(s): {bad}. Choose from {list(SERVICES)}.")
    return names


# --------------------------------------------------------------------------- Windows
def _win_is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _win_register_ps(names: list[str]) -> str:
    """Build a PowerShell script registering + starting the requested tasks."""
    pyw = _python_exe(windowless=True)
    user = f"{os.environ.get('USERDOMAIN', os.environ.get('COMPUTERNAME', '.'))}\\{getpass.getuser()}"
    blocks = [
        "$ErrorActionPreference = 'Stop'",
        f"$pyw = '{pyw}'",
        "if (-not (Test-Path $pyw)) { throw \"pythonw not found at $pyw — run 'uv sync' first.\" }",
    ]
    for n in names:
        s = SERVICES[n]
        level = "Highest" if s["elevated"] else "Limited"
        blocks.append(f"""
$task = '{s["win_task"]}'
$action = New-ScheduledTaskAction -Execute $pyw -Argument '-m {s["module"]}' -WorkingDirectory '{REPO_ROOT}'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel {level}
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description '{s["label"]}' -Force | Out-Null
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object {{ $_.CommandLine -match '{s["module"].split(".")[-1]}' }} |
    ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
Start-Sleep -Milliseconds 800
Start-ScheduledTask -TaskName $task
Write-Host ('[OK] ' + $task + ' registered (AtLogOn, {level}) and started.')
""")
    return "\n".join(blocks)


def _win_run_ps(script: str, elevated: bool) -> None:
    tmp = Path(tempfile.gettempdir()) / "afon_autostart.ps1"
    tmp.write_text(script, encoding="utf-8")
    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp)]
    if elevated and not _win_is_admin():
        # Relaunch this single PS script elevated; the UAC prompt is the one approval needed.
        print("Requesting administrator rights (accept the UAC prompt)…")
        quoted = ",".join(f"'{a}'" for a in args)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process powershell -Verb RunAs -Wait -ArgumentList {quoted}"],
            check=True,
        )
    else:
        subprocess.run(["powershell", *args], check=True)


def _win_install(names: list[str]) -> None:
    need_elev = any(SERVICES[n]["elevated"] for n in names)
    _win_run_ps(_win_register_ps(names), elevated=need_elev)


def _win_uninstall(names: list[str]) -> None:
    lines = ["$ErrorActionPreference='SilentlyContinue'"]
    for n in names:
        t = SERVICES[n]["win_task"]
        lines.append(f"Stop-ScheduledTask -TaskName '{t}'")
        lines.append(f"Unregister-ScheduledTask -TaskName '{t}' -Confirm:$false")
        lines.append(f"Write-Host '[OK] removed {t}'")
    _win_run_ps("\n".join(lines), elevated=any(SERVICES[n]["elevated"] for n in names))


def _win_status(names: list[str]) -> None:
    t = ",".join(f"'{SERVICES[n]['win_task']}'" for n in names)
    ps = (f"@({t}) | ForEach-Object {{ $i = Get-ScheduledTaskInfo -TaskName $_ -ErrorAction "
          "SilentlyContinue; if ($i) { '{0,-16} state={1} lastRun={2} lastResult=0x{3:X}' -f "
          "$_, (Get-ScheduledTask -TaskName $_).State, $i.LastRunTime, $i.LastTaskResult } "
          "else { '{0,-16} NOT INSTALLED' -f $_ } }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)


# --------------------------------------------------------------------------- macOS
def _mac_plist_path(name: str) -> Path:
    label = f"com.afon.{SERVICES[name]['unit'].replace('afon-', '')}"
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _mac_install(names: list[str]) -> None:
    py = _python_exe(windowless=False)
    logs = REPO_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    for n in names:
        s = SERVICES[n]
        label = f"com.afon.{s['unit'].replace('afon-', '')}"
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>-m</string><string>{s['module']}</string></array>
  <key>WorkingDirectory</key><string>{REPO_ROOT}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs / (s['unit'] + '.out.log')}</string>
  <key>StandardErrorPath</key><string>{logs / (s['unit'] + '.err.log')}</string>
</dict></plist>
"""
        p = _mac_plist_path(n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(p)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "load", "-w", str(p)], check=True)
        print(f"[OK] {label} loaded (RunAtLoad + KeepAlive).")
        if s["elevated"]:
            print(f"     NOTE: {n} runs as your user (a LaunchAgent). Root-level control would "
                  "need a LaunchDaemon installed with sudo — not done automatically.")


def _mac_uninstall(names: list[str]) -> None:
    for n in names:
        p = _mac_plist_path(n)
        subprocess.run(["launchctl", "unload", str(p)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if p.exists():
            p.unlink()
        print(f"[OK] removed {p.name}")


def _mac_status(names: list[str]) -> None:
    for n in names:
        label = f"com.afon.{SERVICES[n]['unit'].replace('afon-', '')}"
        r = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
        print(f"{n:10} {'loaded' if r.returncode == 0 else 'NOT loaded'}")


# --------------------------------------------------------------------------- Linux
def _linux_unit_path(name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICES[name]['unit']}.service"


def _linux_install(names: list[str]) -> None:
    py = _python_exe(windowless=False)
    for n in names:
        s = SERVICES[n]
        unit = f"""[Unit]
Description={s['label']}
After=default.target

[Service]
Type=simple
WorkingDirectory={REPO_ROOT}
ExecStart={py} -m {s['module']}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
        p = _linux_unit_path(n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(unit, encoding="utf-8")
        print(f"[OK] wrote {p}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for n in names:
        subprocess.run(["systemctl", "--user", "enable", "--now",
                        f"{SERVICES[n]['unit']}.service"], check=False)
        print(f"[OK] enabled + started {SERVICES[n]['unit']}.service")
        if SERVICES[n]["elevated"]:
            print(f"     NOTE: {n} runs as your user. Root-level control would need a system unit "
                  "(/etc/systemd/system) installed with sudo — not done automatically.")
    # Start at boot even before login.
    r = subprocess.run(["loginctl", "enable-linger", getpass.getuser()], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"[OK] lingering enabled for {getpass.getuser()} (starts at boot, no login needed).")
    else:
        print("     NOTE: 'loginctl enable-linger' failed (may need sudo). Services will start at "
              "login instead of boot. Run: sudo loginctl enable-linger " + getpass.getuser())


def _linux_uninstall(names: list[str]) -> None:
    for n in names:
        subprocess.run(["systemctl", "--user", "disable", "--now",
                        f"{SERVICES[n]['unit']}.service"], check=False)
        p = _linux_unit_path(n)
        if p.exists():
            p.unlink()
        print(f"[OK] removed {SERVICES[n]['unit']}.service")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def _linux_status(names: list[str]) -> None:
    for n in names:
        r = subprocess.run(["systemctl", "--user", "is-active", f"{SERVICES[n]['unit']}.service"],
                           capture_output=True, text=True)
        print(f"{n:10} {r.stdout.strip() or 'unknown'}")


# --------------------------------------------------------------------------- dispatch
_OS = platform.system()
_DISPATCH = {
    "Windows": (_win_install, _win_uninstall, _win_status),
    "Darwin": (_mac_install, _mac_uninstall, _mac_status),
    "Linux": (_linux_install, _linux_uninstall, _linux_status),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    names = _resolve(argv[1:])
    if _OS not in _DISPATCH:
        raise SystemExit(f"unsupported OS: {_OS}")
    install, uninstall, status = _DISPATCH[_OS]
    if cmd == "install":
        print(f"Installing autostart for {names} on {_OS} (repo: {REPO_ROOT})…")
        install(names)
        print("Done. They will start automatically at every boot/login.")
    elif cmd == "uninstall":
        uninstall(names)
    elif cmd == "status":
        status(names)
    else:
        raise SystemExit("usage: autostart {install|uninstall|status} [edge] [pc_agent]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
