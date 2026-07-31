<#
.SYNOPSIS
    Cleanly restart Watari's laptop edge (pc_agent + voice assistant), force-killing stale instances.

.DESCRIPTION
    The edge runs as two Scheduled Tasks:
        WatariPcAgent  -> pythonw -m jarvis.edge.pc_agent   (RunLevel Highest / ELEVATED)
        JarvisEdge     -> pythonw -m jarvis.edge.assistant  (RunLevel Limited)

    IMPORTANT lesson (why this script self-elevates): `Stop-ScheduledTask` does NOT reliably terminate
    the pc_agent PROCESS — it's ELEVATED and, once running since logon, a non-elevated stop just bounces
    its WebSocket while the old process keeps executing OLD code. That left a stale pc_agent serving the
    brain and spawning PowerShell windows even after a code fix. So this script relaunches itself as
    admin and FORCE-KILLS every pythonw (and any orphaned activity-snapshot PowerShell) before starting
    the tasks fresh — guaranteeing a single, current-code instance.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\restart_edge.ps1
    (Approve the one UAC prompt.)
#>
[CmdletBinding()]
param([string[]]$Tasks = @('WatariPcAgent', 'JarvisEdge'))

$ErrorActionPreference = 'SilentlyContinue'

# --- self-elevate: an elevated pc_agent can only be killed by an elevated process ---------------
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating (approve the UAC prompt to force-restart the edge cleanly)..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
    )
    return
}

Write-Host "== Watari edge restart (elevated) ==" -ForegroundColor Cyan

# Any deliberate restart (phoenix, the daily refresh, a manual run) cancels a prior "goodnight":
# clear the marker so WatariEdgeGuard resumes guarding the process from here on.
Remove-Item 'C:\Jarvis\logs\edge_stopped_by_owner' -Force -ErrorAction SilentlyContinue

# 1. Stop the tasks (best effort) so the scheduler doesn't fight the kill.
foreach ($t in $Tasks) { Stop-ScheduledTask -TaskName $t }
Start-Sleep -Seconds 2

# 2. FORCE-KILL the edge python processes. Now that we're elevated their command lines + exe paths are
#    readable, so we target PRECISELY: our own .venv interpreter, or any python whose command line is
#    a jarvis.edge module (covers a system-python child too). We do NOT kill by a null/blank command
#    line — that would catch unrelated pythonw apps.
#
#    Any PowerShell they spawned is killed by PARENT (a child of an edge python), never by matching a
#    command-line STRING: that string-match is what falsely flagged this very script's own monitoring
#    commands (their command line literally contains the search terms). Parent-based is immune to that
#    and can't hit an unrelated user PowerShell. (Note: activity_snapshot is pure ctypes now and spawns
#    nothing — this stays only to sweep up legacy orphans.)
$edge = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
          Where-Object { $_.CommandLine -match 'jarvis\.edge\.' -or $_.ExecutablePath -match 'Jarvis\\\.venv' })
$edgePids = @($edge.ProcessId)
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $edgePids -contains $_.ParentProcessId } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $killed++ }
$edge | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $killed++ }
Write-Host "   force-killed $killed edge process(es)"
Start-Sleep -Seconds 2

# 3. Start each task once, fresh from current source on disk.
foreach ($t in $Tasks) { Start-ScheduledTask -TaskName $t; Start-Sleep -Milliseconds 1500 }
Start-Sleep -Seconds 4

# 4. Report. Verify a REAL process restart (not just a WebSocket bounce) via the fresh log marker.
$now = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
         Where-Object { $_.CommandLine -match 'jarvis\.edge\.' -or $_.ExecutablePath -match 'Jarvis\\\.venv' })
Write-Host ("   edge pythonw running now: {0}" -f $now.Count)
$pyPids = @((Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'").ProcessId)
$leftoverPS = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $pyPids -contains $_.ParentProcessId })
Write-Host ("   PowerShell spawned by edge python: {0} (want 0)" -f $leftoverPS.Count)
$log = 'C:\Jarvis\logs\pc_agent.log'
if (Test-Path $log) {
    $marker = Get-Content $log -Tail 8 | Where-Object { $_ -match 'supervisor up|pc-agent starting' } | Select-Object -Last 1
    if ($marker) { Write-Host ("   restart confirmed: " + ($marker -replace '\x1b\[[0-9;]*m','')) }
    else { Write-Host "   (no fresh restart marker yet - check logs/pc_agent.log)" -ForegroundColor Yellow }
}
Write-Host "== done ==" -ForegroundColor Cyan
Start-Sleep -Seconds 4   # keep the window up briefly so the user sees the result
