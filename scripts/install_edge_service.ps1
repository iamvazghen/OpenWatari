<#
.SYNOPSIS
  Register Afon's edge (voice) process to auto-start on logon - hidden, no console window,
  auto-restart on crash. The last piece of Phase 4 ("true 24/7 on the desktop").

.DESCRIPTION
  Creates a Windows Scheduled Task that launches the edge assistant with the venv's pythonw.exe
  (windowless) at logon. Wake-word gating means it sits idle - no STT/LLM/TTS cost - until you
  say "Hey Afon". It will NOT start now; it starts at your next logon. Remove it any time with
  scripts\uninstall_edge_service.ps1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_edge_service.ps1
#>
param(
    [string]$TaskName = "AfonEdge",
    [string]$RepoRoot = "C:\Afon",
    [switch]$StartNow
)
$ErrorActionPreference = "Stop"

$pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "pythonw not found at $pythonw. Run 'uv sync' with the edge/cloud-voice/brain/local-voice extras first."
}

# pythonw.exe -m afon.edge.assistant, working dir = repo (so .env + the editable package load).
$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "-m afon.edge.assistant" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Survive crashes (effectively unlimited restarts, 1 min apart), no run-time limit, start if missed.
# 999 not 3: a 24/7 assistant must keep coming back even after a long run of hard crashes; the
# in-process supervisor handles soft failures, this task is the backstop for total-process death.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
# Interactive principal: the task runs in YOUR session so it can reach the mic + speakers.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Afon voice assistant (edge) - auto-start on logon" -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName'."
Write-Host "     Afon will start hidden at your next logon. Say 'Hey Afon' to wake him."
Write-Host "     Disable any time: powershell -ExecutionPolicy Bypass -File scripts\uninstall_edge_service.ps1"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[OK] Started '$TaskName' now."
}
