#Requires -Version 5.1
<#
.SYNOPSIS
  Install On-windows operator files into a user folder (no admin).

.DESCRIPTION
  Copies receive_stream.py, examples\*.py, webrtc-client.html, requirements.txt
  Creates venv, pip install -r requirements.txt
  Writes ReceiveStream.cmd, PcParallelClient.cmd, OpenWebRtcOperator.cmd

.PARAMETER InstallDir
  Target directory (default: %LOCALAPPDATA%\Programs\On-windows)

.PARAMETER DesktopShortcuts
  Create .lnk shortcuts on the desktop

.PARAMETER AddToUserPath
  Append InstallDir to the user PATH (run ReceiveStream.cmd from anywhere)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install_on_windows.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install_on_windows.ps1 -DesktopShortcuts -AddToUserPath
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\On-windows"),
    [switch]$DesktopShortcuts,
    [switch]$AddToUserPath
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Find-Python {
    foreach ($name in @("python", "python3")) {
        try {
            $p = Get-Command $name -ErrorAction Stop | Select-Object -ExpandProperty Source
            $ver = & $p -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ([version]$ver -ge [version]"3.10") {
                return $p
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Error "Python 3.10+ required in PATH. Install from https://www.python.org/ (check Add to PATH)."
}

Write-Host "Python: $py"
Write-Host "Repo: $RepoRoot"
Write-Host "Install dir: $InstallDir"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$copyItems = @(
    @{ Src = "receive_stream.py"; Dst = "receive_stream.py" },
    @{ Src = "requirements.txt"; Dst = "requirements.txt" },
    @{ Src = "webrtc-client.html"; Dst = "webrtc-client.html" },
    @{ Src = "webrtc-client-hetzner-debug.html"; Dst = "webrtc-client-hetzner-debug.html" },
    @{ Src = "webrtc_ice_operator_fetch.js"; Dst = "webrtc_ice_operator_fetch.js" }
)
foreach ($it in $copyItems) {
    $from = Join-Path $RepoRoot $it.Src
    if (-not (Test-Path $from)) { Write-Error "Missing file: $from" }
    Copy-Item -LiteralPath $from -Destination (Join-Path $InstallDir $it.Dst) -Force
}

$cloudSrc = Join-Path $RepoRoot "cloud"
$cloudDst = Join-Path $InstallDir "cloud"
if (Test-Path $cloudSrc) {
    New-Item -ItemType Directory -Force -Path $cloudDst | Out-Null
    Get-ChildItem -LiteralPath $cloudSrc -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $cloudDst $_.Name) -Force
    }
    Write-Host "Copied cloud\ (ICE server + docs)"
}

$exSrc = Join-Path $RepoRoot "examples"
$exDst = Join-Path $InstallDir "examples"
if (-not (Test-Path $exSrc)) { Write-Error "Missing folder: $exSrc" }
New-Item -ItemType Directory -Force -Path $exDst | Out-Null
Get-ChildItem -LiteralPath $exSrc -File | Where-Object {
    $_.Extension -eq ".py" -or ($_.Extension -eq ".js" -and $_.Name -like "webrtc_*")
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $exDst $_.Name) -Force
}

$configSrc = Join-Path $RepoRoot "config"
$configDst = Join-Path $InstallDir "config"
if (Test-Path $configSrc) {
    New-Item -ItemType Directory -Force -Path $configDst | Out-Null
    Get-ChildItem -LiteralPath $configSrc -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $configDst $_.Name) -Force
    }
    Write-Host "Copied config\ (ICE/Firebase examples)"
}

$scriptsDst = Join-Path $InstallDir "scripts"
New-Item -ItemType Directory -Force -Path $scriptsDst | Out-Null
foreach ($sn in @(
        "open_webrtc_operator.ps1",
        "open_webrtc_operator_hetzner_debug.ps1",
        "set-ice-token.ps1"
    )) {
    $src = Join-Path $RepoRoot "scripts\$sn"
    if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $scriptsDst $sn) -Force
    }
}

$venvPy = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating venv..."
    & $py -m venv (Join-Path $InstallDir "venv")
}

$venvExe = Join-Path $InstallDir "venv\Scripts\python.exe"
& $venvExe -m pip install --upgrade pip -q
$req = Join-Path $InstallDir "requirements.txt"
& $venvExe -m pip install -r $req

@(
    @"
@echo off
"%~dp0venv\Scripts\python.exe" "%~dp0receive_stream.py" %*
exit /b %ERRORLEVEL%
"@,
    @"
@echo off
"%~dp0venv\Scripts\python.exe" "%~dp0examples\pc_parallel_client.py" %*
exit /b %ERRORLEVEL%
"@,
    @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_webrtc_operator.ps1" -InstallDir "%~dp0"
if errorlevel 1 start "" "%~dp0webrtc-client.html"
"@,
    @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_webrtc_operator_hetzner_debug.ps1" -InstallDir "%~dp0"
if errorlevel 1 start "" "%~dp0webrtc-client-hetzner-debug.html"
"@
) | ForEach-Object -Begin { $i = 0 } -Process {
    $names = @("ReceiveStream.cmd", "PcParallelClient.cmd", "OpenWebRtcOperator.cmd", "OpenWebRtcHetznerDebug.cmd")
    $out = Join-Path $InstallDir $names[$i]
    [System.IO.File]::WriteAllText($out, $_.TrimStart() + "`r`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host "Wrote: $out"
    $i++
}

if ($DesktopShortcuts) {
    $Wsh = New-Object -ComObject WScript.Shell
    $desk = [Environment]::GetFolderPath("Desktop")
    $links = @(
        @{ Name = "Pi ReceiveStream.lnk"; Target = (Join-Path $InstallDir "ReceiveStream.cmd") },
        @{ Name = "Pi PcParallelClient.lnk"; Target = (Join-Path $InstallDir "PcParallelClient.cmd") },
        @{ Name = "Pi WebRTC Operator.lnk"; Target = (Join-Path $InstallDir "OpenWebRtcOperator.cmd") },
        @{ Name = "Pi WebRTC Hetzner debug.lnk"; Target = (Join-Path $InstallDir "OpenWebRtcHetznerDebug.cmd") }
    )
    foreach ($L in $links) {
        $sc = $Wsh.CreateShortcut((Join-Path $desk $L.Name))
        $sc.TargetPath = $L.Target
        $sc.WorkingDirectory = $InstallDir
        $sc.Save()
        Write-Host "Shortcut: $($L.Name)"
    }
}

if ($AddToUserPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$InstallDir*") {
        $newPath = if ($userPath) { "$userPath;$InstallDir" } else { $InstallDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "Added to user PATH: $InstallDir"
        Write-Host "Open a new terminal for PATH to apply."
    } else {
        Write-Host "PATH already contains install dir."
    }
}

Write-Host ""
Write-Host "Done. Run:"
Write-Host "  ReceiveStream.cmd connect --host <PI_IP>"
Write-Host "  PcParallelClient.cmd --host <PI_IP> ..."
Write-Host "  OpenWebRtcOperator.cmd   (default browser, webrtc-client.html)"
Write-Host ""
Write-Host "GStreamer and ffplay are not installed by this script."
