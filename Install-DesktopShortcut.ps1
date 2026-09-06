$ErrorActionPreference = 'Stop'
$dashboardRoot = $PSScriptRoot
$dashboardStart = Join-Path $dashboardRoot 'Start.ps1'
$dashboardDesktop = [Environment]::GetFolderPath('Desktop')
$dashboardShortcutPath = Join-Path $dashboardDesktop 'Codex OMP Usage Dashboard.lnk'
$dashboardShell = New-Object -ComObject WScript.Shell
$dashboardShortcut = $dashboardShell.CreateShortcut($dashboardShortcutPath)
$dashboardShortcut.TargetPath = 'powershell.exe'
$dashboardShortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $dashboardStart + '"'
$dashboardShortcut.WorkingDirectory = $dashboardRoot
$dashboardShortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$dashboardShortcut.Description = 'Start the local Codex App and OMP usage dashboard'
$dashboardShortcut.Save()
Write-Output "Created desktop shortcut: $dashboardShortcutPath"
