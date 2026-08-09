<#
.SYNOPSIS
    Register (or re-register) AfonEdgeRefresh: a daily "freshness" restart of the laptop edge.

.DESCRIPTION
    The voice edge (pipecat + pyaudio + wake-word models + brain WebSocket) is the process that
    accumulates over long uptimes — stale audio handles, reconnect churn, slow creep — and has a
    history of going "deaf". This registers a Scheduled Task that runs scripts\restart_edge.ps1 once
    a day deep in the owner's night (03:20 Europe/Berlin local), force-restarting both edge tasks
    (AfonEdge + the elevated AfonPcAgent) from current source.

    Recoverability integration: this does NOT add a new supervisor — it drives the existing one
    (Task Scheduler, which already relaunches the edge at logon). RunLevel Highest makes it elevated,
    so restart_edge.ps1 skips its interactive-UAC branch and runs unattended. LogonType Interactive
    means it only fires while the owner is logged in (when the laptop is off, there is nothing to
    refresh) and needs no stored password.

    Idempotent: re-running replaces the task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_edge_refresh.ps1
#>
[CmdletBinding()]
param([string]$At = '03:20')

$ErrorActionPreference = 'Stop'
$TaskName = 'AfonEdgeRefresh'
$script   = Join-Path $PSScriptRoot 'restart_edge.ps1'
if (-not (Test-Path $script)) { throw "restart_edge.ps1 not found at $script" }

# Registering a RunLevel-Highest task requires an elevated registrar. Self-elevate (one UAC prompt),
# logging the outcome so an unattended caller can verify.
$log = 'C:\Afon\logs\edge_refresh_install.log'
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating to register the scheduled task (approve the UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-At', $At
    )
    return
}
Start-Transcript -Path $log -Force | Out-Null

$action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
             -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger   = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
# Don't fight the audio pipeline mid-refresh; let it finish; skip a run if the laptop is asleep.
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Afon daily edge freshness restart (deep night, elevated).' -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
Write-Host ("Registered {0}: daily {1} local, RunLevel {2}, next run {3}" -f `
    $TaskName, $At, $t.Principal.RunLevel, (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)
Stop-Transcript | Out-Null
