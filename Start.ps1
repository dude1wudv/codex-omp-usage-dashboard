$ErrorActionPreference = 'Stop'
$dashboardRoot = $PSScriptRoot
$dashboardUrl = 'http://127.0.0.1:8766'
$dashboardReady = $false
try {
    $dashboardResponse = Invoke-WebRequest -Uri $dashboardUrl -TimeoutSec 2 -UseBasicParsing
    $dashboardReady = $dashboardResponse.StatusCode -eq 200 -and $dashboardResponse.Content.Contains('<title>Codex')
} catch { }
if (-not $dashboardReady) {
    $dashboardPython = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $dashboardPython) { $dashboardPython = (Get-Command python.exe).Source }
    $dashboardProcess = Start-Process -FilePath $dashboardPython -ArgumentList @('"' + (Join-Path $dashboardRoot 'server.py') + '"') -WorkingDirectory $dashboardRoot -WindowStyle Hidden -PassThru
    $dashboardState = Join-Path $env:LOCALAPPDATA 'CodexOmpUsageDashboard'
    New-Item -ItemType Directory -Force -Path $dashboardState | Out-Null
    Set-Content -LiteralPath (Join-Path $dashboardState 'server.pid') -Value $dashboardProcess.Id
    for ($dashboardAttempt = 0; $dashboardAttempt -lt 25; $dashboardAttempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $dashboardResponse = Invoke-WebRequest -Uri $dashboardUrl -TimeoutSec 1 -UseBasicParsing
            if ($dashboardResponse.StatusCode -eq 200) { $dashboardReady = $true; break }
        } catch { }
    }
}
if (-not $dashboardReady) { throw 'Dashboard could not start. Run python server.py to see the error.' }
Start-Process $dashboardUrl
