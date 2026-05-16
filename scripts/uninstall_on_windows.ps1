#Requires -Version 5.1
<#
.SYNOPSIS
  Remove On-windows install directory (venv and launchers).

.PARAMETER InstallDir
  Same path as install (default: %LOCALAPPDATA%\Programs\On-windows)
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\On-windows")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $InstallDir)) {
    Write-Host "Nothing to remove: $InstallDir"
    exit 0
}

Write-Host "Removing: $InstallDir"
Remove-Item -LiteralPath $InstallDir -Recurse -Force

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -and ($userPath -like "*$InstallDir*")) {
    $parts = $userPath -split ";" | Where-Object { $_ -and ($_ -ne $InstallDir) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
    Write-Host "Removed install dir from user PATH."
}

$Wsh = New-Object -ComObject WScript.Shell
$desk = [Environment]::GetFolderPath("Desktop")
foreach ($n in @("Pi ReceiveStream.lnk", "Pi PcParallelClient.lnk", "Pi WebRTC Operator.lnk")) {
    $p = Join-Path $desk $n
    if (Test-Path $p) {
        Remove-Item -LiteralPath $p -Force
        Write-Host "Removed shortcut: $n"
    }
}

Write-Host "Done."
