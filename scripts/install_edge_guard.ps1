<#
.SYNOPSIS
    Register AfonEdgeGuard: a cheap periodic check that the edge is actually RUNNING.

.DESCRIPTION
    Three things can leave the laptop without a live edge, and each has its own recovery:

      * code went stale / long uptime  -> AfonEdgeRefresh, daily 03:20 (already installed)
      * machine resumed from sleep     -> the edge detects the wall-clock jump itself and rebuilds
                                          (audio_watchdog.watch_audio_liveness, suspend_limit_s)
      * the edge PROCESS died entirely -> nothing. The in-process watchdog is gone with it, and the
                                          Task Scheduler logon trigger won't refire until the next
                                          logon. THIS task is that gap.

    Every 30 minutes it looks for a pythonw running afon.edge.* and, only if none is found, kicks
    AfonEdgeRefresh. Finding a live edge costs one CIM query and does nothing — so this never
    interrupts a working session (unlike a resume-event trigger, which fires ~8x/day on this laptop's
    modern standby for brief maintenance wakes).

    Runs as the limited user: starting a task needs no elevation, so registering needs no UAC.
    Idempotent: re-running replaces the task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_edge_guard.ps1
#>
[CmdletBinding()]
param([int]$EveryMinutes = 30)
$ErrorActionPreference = 'Stop'

$check = @'
$stopped = 'C:\Afon\logs\edge_stopped_by_owner'
$edge = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'afon\.edge\.' })
if ($edge.Count -eq 0) {
  # Protocol goodnight drops this marker: that silence was ORDERED, not a crash. Without the check
  # the guard would undo an explicit "goodnight" within 30 minutes. restart_edge.ps1 clears it.
  if (Test-Path $stopped) { return }
  Add-Content -Encoding utf8 'C:\Afon\logs\edge_guard.log' ("{0} no edge process - starting AfonEdgeRefresh" -f (Get-Date -Format s))
  Start-ScheduledTask AfonEdgeRefresh
}
'@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($check))

$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
           -Argument "-NoProfile -WindowStyle Hidden -EncodedCommand $enc"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
           -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName 'AfonEdgeGuard' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Afon: restart the edge if its process is missing (checked every 30 min).' -Force | Out-Null

Write-Host ("Registered AfonEdgeGuard (every {0} min, restarts only when no edge process exists); state: {1}" -f `
    $EveryMinutes, (Get-ScheduledTask AfonEdgeGuard).State)
