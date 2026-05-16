#Requires -Version 5.1
<#
.SYNOPSIS
  Записывает WEBRTC_ICE_CONFIG_TOKEN в config\webrtc.ice.local.env (значение с VPS show-ice-client-env.sh).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\set-ice-token.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\set-ice-token.ps1 -Token "your-token-here"
#>
param(
    [string]$InstallDir = "",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
if (-not $InstallDir) {
    $InstallDir = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $InstallDir = Resolve-Path $InstallDir
}

$configDir = Join-Path $InstallDir "config"
$envPath = Join-Path $configDir "webrtc.ice.local.env"
$example = Join-Path $configDir "webrtc.ice.local.env.example"

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
if (-not (Test-Path $envPath)) {
    if (Test-Path $example) {
        Copy-Item -LiteralPath $example -Destination $envPath
    } else {
        @(
            "WEBRTC_ROOM=pi-camera",
            "WEBRTC_ICE_CONFIG_URL=http://116.203.148.254:8788/api/ice",
            "WEBRTC_ICE_CONFIG_TOKEN=",
            "WEBRTC_BROWSER=default"
        ) | Set-Content -LiteralPath $envPath -Encoding UTF8
    }
}

if (-not $Token) {
    Write-Host "Token from VPS: sudo /root/project/scripts/show-ice-client-env.sh"
    Write-Host "Paste WEBRTC_ICE_CONFIG_TOKEN (input hidden):"
    $sec = Read-Host -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

$Token = ($Token -replace "`r|`n", "").Trim()
if (-not $Token) {
    Write-Error "Empty token."
}

$lines = Get-Content -LiteralPath $envPath -Encoding UTF8
$found = $false
$out = foreach ($line in $lines) {
    if ($line -match '^\s*WEBRTC_ICE_CONFIG_TOKEN\s*=') {
        $found = $true
        "WEBRTC_ICE_CONFIG_TOKEN=$Token"
    } else {
        $line
    }
}
if (-not $found) {
    $out += "WEBRTC_ICE_CONFIG_TOKEN=$Token"
}
$out | Set-Content -LiteralPath $envPath -Encoding UTF8

Write-Host "[ok] Saved: $envPath"
Write-Host "Next:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\scripts\open_webrtc_operator.ps1`""
Write-Host "In browser: Test ICE (VPS) must show TURN, then Connect."
