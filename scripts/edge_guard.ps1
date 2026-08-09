# Afon edge watchdog — is the voice assistant still alive?
#
# Runs every 30 minutes from the AfonEdgeGuard scheduled task. If the edge process is
# gone, and the owner did not deliberately stop it, restart it.
#
# LAUNCHED VIA afon-guard-hidden.vbs, NOT directly. That matters: powershell.exe is a
# console-subsystem program, so Windows allocates a console window at process creation —
# before PowerShell has parsed a single argument. `-WindowStyle Hidden` is applied after
# startup, far too late, so a task that calls powershell.exe directly flashes an empty
# black window on the desktop every time it fires. Forty-eight times a day, in this case.
# The VBS wrapper starts it with window style 0, so no console is ever shown.
# OpenClaw-Vault-Sync solved the identical problem the same way; see vps-sync-hidden.vbs.

$stopped = 'C:\Afon\logs\edge_stopped_by_owner'
$edge = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'afon\.edge\.' })
if ($edge.Count -eq 0) {
  # Protocol goodnight drops this marker: that silence was ORDERED, not a crash. Without the check
  # the guard would undo an explicit "goodnight" within 30 minutes. restart_edge.ps1 clears it.
  if (Test-Path $stopped) { return }
  Add-Content -Encoding utf8 'C:\Afon\logs\edge_guard.log' ("{0} no edge process - starting AfonEdgeRefresh" -f (Get-Date -Format s))
  Start-ScheduledTask AfonEdgeRefresh
}
