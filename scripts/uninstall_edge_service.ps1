<#
.SYNOPSIS
  Remove the Afon edge auto-start scheduled task (undo install_edge_service.ps1).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall_edge_service.ps1
#>
param(
    [string]$TaskName = "AfonEdge"
)
$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "[--] No scheduled task named '$TaskName'. Nothing to remove."
    return
}
# Stop it if it's running, then unregister.
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[OK] Removed scheduled task '$TaskName'. Afon will no longer auto-start on logon."
