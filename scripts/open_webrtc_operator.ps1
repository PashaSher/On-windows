#Requires -Version 5.1
<#
.SYNOPSIS
  Открывает webrtc-client.html с room / ICE URL / token из config\webrtc.ice.local.env (VPS TURN-туннель).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\open_webrtc_operator.ps1
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
$HtmlPath = Join-Path $InstallDir "webrtc-client.html"
if (-not (Test-Path $HtmlPath)) {
    Write-Error "Not found: $HtmlPath"
}

function Read-DotEnvFile {
    param([string]$Path)
    $out = @{}
    if (-not (Test-Path $Path)) {
        return $out
    }
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

if (-not (Test-Path $EnvFile)) {
    Write-Warning "Create $EnvFile from config\webrtc.ice.local.env.example (token from VPS show-ice-client-env.sh)."
}

$qp = @("room=" + [uri]::EscapeDataString($room))
if ($ice) { $qp += "ice=" + [uri]::EscapeDataString($ice) }
if ($token) { $qp += "iceToken=" + [uri]::EscapeDataString($token) }
$query = $qp -join "&"
$fileUri = [Uri]::new($HtmlPath).AbsoluteUri
$openUrl = if ($query) { "${fileUri}?${query}" } else { $fileUri }

Write-Host "Room: $room"
Write-Host "ICE:  $ice"
if ($token) { Write-Host "ICE token: (set, hidden)" } else { Write-Warning "WEBRTC_ICE_CONFIG_TOKEN empty - VPS /api/ice may return 401." }
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
if ($exe) {
    Start-Process -FilePath $exe -ArgumentList @($openUrl)
} else {
    Start-Process $openUrl
}

Write-Host "In the page: Test ICE, then Connect (Firebase signaling + VPS STUN/TURN)."
