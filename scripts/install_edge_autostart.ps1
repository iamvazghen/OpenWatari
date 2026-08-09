<#
.SYNOPSIS
    Ensure Afon's edge (pc_agent + assistant) auto-starts on boot AND self-heals if it ever dies.

.DESCRIPTION
    The edge runs as two Scheduled Tasks (AfonPcAgent = elevated, AfonEdge = limited). This script
    guarantees three layers of "always on", because no single one is sufficient:

      1. LOGON trigger        -> starts the edge when you log in (i.e. on every boot / restart).
      2. 5-MINUTE WATCHDOG    -> a repeating time trigger. With MultipleInstancesPolicy=IgnoreNew, a
                                 re-fire is IGNORED while the edge is alive, but RESTARTS it within
                                 <=5 min if the process ever crashed or was killed. This is the piece
                                 that actually makes it self-heal: Windows' own "Restart on failure"
                                 does NOT reliably relaunch an externally-killed long-running process
                                 (verified: a killed edge stayed dead until this watchdog was added).
      3. RestartOnFailure     -> belt-and-suspenders (999x / 1 min) for the cases it does catch.

    Idempotent: run it any time to (re)assert the correct config. Self-elevates (the pc_agent task is
    elevated, so it can only be re-registered from an elevated context).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_edge_autostart.ps1
    (Approve the one UAC prompt.)
#>
[CmdletBinding()]
param([string[]]$Tasks = @('AfonPcAgent', 'AfonEdge'), [int]$WatchdogMinutes = 5)

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating (approve the UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-WatchdogMinutes', $WatchdogMinutes
    )
    return
}

Write-Host "== Ensuring edge auto-start + self-heal ==" -ForegroundColor Cyan

foreach ($n in $Tasks) {
    $task = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "  ! $n not found - skipping" -ForegroundColor Red; continue }

    $xml = Export-ScheduledTask -TaskName $n
    $changed = $false

    # (2) Add the repeating watchdog TimeTrigger if it's not already there. Past StartBoundary +
    #     Interval + no Duration = repeat forever; StartWhenAvailable (already set) catches it up.
    if ($xml -notmatch '<TimeTrigger>') {
        $tt = ('<TimeTrigger><Repetition><Interval>PT{0}M</Interval>' -f $WatchdogMinutes) +
              '<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>' +
              '<StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>'
        $xml = $xml -replace '</Triggers>', "$tt</Triggers>"
        $changed = $true
    }
    # (1) Ensure a LogonTrigger exists (start on boot/login).
    if ($xml -notmatch '<LogonTrigger') {
        $xml = $xml -replace '</Triggers>', '<LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>'
        $changed = $true
    }
    # (3) Ensure RestartOnFailure is present.
    if ($xml -notmatch 'RestartOnFailure') {
        $rof = '<RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>'
        $xml = $xml -replace '</Settings>', "$rof</Settings>"
        $changed = $true
    }

    if ($changed) {
        Register-ScheduledTask -TaskName $n -Xml $xml -Force | Out-Null
        Write-Host "  $n : updated (watchdog + logon + restart-on-failure asserted)" -ForegroundColor Green
    } else {
        Write-Host "  $n : already correct (no change)"
    }
    # Make sure it's actually running right now.
    if ((Get-ScheduledTask -TaskName $n).State -ne 'Running') { Start-ScheduledTask -TaskName $n }

    $t = Get-ScheduledTask -TaskName $n
    $kinds = ($t.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task','' }) -join ', '
    Write-Host ("      triggers now: " + $kinds)
}

Write-Host "== done - edge will start on boot and self-heal within $WatchdogMinutes min if it dies ==" -ForegroundColor Cyan
Start-Sleep -Seconds 5
