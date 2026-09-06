$ErrorActionPreference = 'Stop'
$dashboardState = Join-Path $env:LOCALAPPDATA 'CodexOmpUsageDashboard'
$dashboardPidFile = Join-Path $dashboardState 'server.pid'
if (Test-Path -LiteralPath $dashboardPidFile) {
    $dashboardPid = [int](Get-Content -LiteralPath $dashboardPidFile)
    $dashboardProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $dashboardPid"
    $dashboardServer = Join-Path $PSScriptRoot 'server.py'
    if ($dashboardProcess -and $dashboardProcess.CommandLine.Contains($dashboardServer)) {
        Stop-Process -Id $dashboardPid
        Remove-Item -LiteralPath $dashboardPidFile
    }
}
