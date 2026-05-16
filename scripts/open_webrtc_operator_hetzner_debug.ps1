#Requires -Version 5.1
<#
.SYNOPSIS
  WebRTC operator: только TURN через Hetzner (iceTransportPolicy=relay).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\open_webrtc_operator_hetzner_debug.ps1
#>
param(
    [string]$InstallDir = "",
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
if (-not $InstallDir) {
    $InstallDir = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $InstallDir = Resolve-Path $InstallDir
}

if (-not $EnvFile) {
    $EnvFile = Join-Path $InstallDir "config\webrtc.ice.local.env"
}

function Read-DotEnvFile {
    param([string]$Path)
    $out = @{}
    if (-not (Test-Path $Path)) { return $out }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        if ($v.StartsWith('"') -and $v.EndsWith('"')) { $v = $v.Substring(1, $v.Length - 2) }
        $out[$k] = $v
    }
    return $out
}

$cfg = Read-DotEnvFile -Path $EnvFile
$room = if ($cfg["WEBRTC_ROOM"]) { $cfg["WEBRTC_ROOM"] } else { "pi-camera" }
$ice = if ($cfg["WEBRTC_ICE_CONFIG_URL"]) { $cfg["WEBRTC_ICE_CONFIG_URL"] } else { "http://116.203.148.254:8788/api/ice" }
$token = $cfg["WEBRTC_ICE_CONFIG_TOKEN"]
$browser = "default"
if ($cfg["WEBRTC_BROWSER"] -and ($cfg["WEBRTC_BROWSER"].Trim())) {
    $browser = $cfg["WEBRTC_BROWSER"].Trim().ToLowerInvariant()
}

$HtmlPath = Join-Path $InstallDir "webrtc-client.html"
if (-not (Test-Path $HtmlPath)) { Write-Error "Not found: $HtmlPath" }
if (-not $token) {
    Write-Warning "WEBRTC_ICE_CONFIG_TOKEN empty in $EnvFile - run scripts\set-ice-token.ps1 first."
}

$qp = @(
    "profile=hetzner-relay-only",
    "room=" + [uri]::EscapeDataString($room),
    "ice=" + [uri]::EscapeDataString($ice)
)
if ($token) { $qp += "iceToken=" + [uri]::EscapeDataString($token) }
$query = $qp -join "&"
$openUrl = [Uri]::new($HtmlPath).AbsoluteUri + "?" + $query

Write-Host "Mode: Hetzner relay-only (iceTransportPolicy=relay)"
Write-Host "Room: $room"
if ($token) { Write-Host "ICE token: (set)" } else { Write-Warning "No ICE token" }
Write-Host "Open: $openUrl"

function Find-BrowserExe {
    param([string]$Name)
    $candidates = @()
    if ($Name -eq "chrome") {
        $candidates = @(
            "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        )
    } elseif ($Name -eq "edge") {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
            "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
        )
    }
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

$exe = Find-BrowserExe -Name $browser
if ($exe) { Start-Process -FilePath $exe -ArgumentList @($openUrl) }
else { Start-Process $openUrl }

Write-Host "Test ICE, then Connect. Pi should also use TURN for stable relay path."
Write-Host ""
Write-Host "If buttons show 'module not loaded', use HTTP server instead:"
Write-Host "  powershell -File scripts\serve_webrtc_operator.ps1 -Profile hetzner-relay-only"
