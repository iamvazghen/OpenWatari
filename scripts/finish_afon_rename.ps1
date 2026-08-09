# Finish the Watari/Jarvis -> Afon rename: the parts that need elevation.
#
# JarvisEdge, WatariPcAgent and WatariEdgeRefresh cannot be re-registered from a normal
# session ("Access is denied") because they run at RunLevel Highest. AfonEdgeGuard was
# migrated already; this script does the remaining three, drops the old tasks, and starts
# the edge again.
#
# Run elevated:  powershell -ExecutionPolicy Bypass -File C:\Afon\scripts\finish_afon_rename.ps1

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating (approve the UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
    )
    return
}

$map = [ordered]@{
    'JarvisEdge'        = 'AfonEdge'
    'WatariPcAgent'     = 'AfonPcAgent'
    'WatariEdgeRefresh' = 'AfonEdgeRefresh'
}

foreach ($old in $map.Keys) {
    $new = $map[$old]
    if (-not (Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue)) {
        Write-Host "  $old already gone, skipping" -ForegroundColor DarkGray
        continue
    }
    # Transform the exported XML rather than rebuilding the task, so triggers, principal
    # and settings carry over byte-for-byte instead of being guessed at.
    $xml = Export-ScheduledTask -TaskName $old
    $xml = $xml.Replace('C:\Jarvis', 'C:\Afon').
                Replace('jarvis.edge', 'afon.edge').
                Replace('Watari', 'Afon').
                Replace('watari', 'afon').
                Replace('Jarvis', 'Afon')
    Register-ScheduledTask -TaskName $new -Xml $xml -Force | Out-Null
    Unregister-ScheduledTask -TaskName $old -Confirm:$false
    Write-Host "  $old -> $new" -ForegroundColor Green
}

Enable-ScheduledTask -TaskName 'AfonEdgeGuard' | Out-Null
Write-Host "  AfonEdgeGuard re-enabled" -ForegroundColor Green

foreach ($t in 'AfonPcAgent', 'AfonEdge') {
    Start-ScheduledTask -TaskName $t
    Start-Sleep -Milliseconds 1500
}
Start-Sleep -Seconds 6

$edge = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'")
Write-Host ("`n  pythonw processes now running: {0}" -f $edge.Count) -ForegroundColor Cyan
foreach ($p in $edge) {
    $cl = if ($p.CommandLine) { $p.CommandLine } else { '(elevated - command line not readable)' }
    Write-Host ("    " + $cl)
}
Write-Host "`n  Remaining Afon tasks:" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -match 'Afon|Watari|Jarvis' } |
    ForEach-Object { Write-Host ("    " + $_.TaskName + "  [" + $_.State + "]") }

Write-Host "`n== done ==" -ForegroundColor Cyan
Start-Sleep -Seconds 5
