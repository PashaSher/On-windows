#Requires -Version 5.1
<#
.SYNOPSIS
  Локальный HTTP для webrtc-client (ES modules + отладка Chrome).

.EXAMPLE
  powershell -File .\scripts\serve_webrtc_operator.ps1
  powershell -File .\scripts\serve_webrtc_operator.ps1 -Profile hetzner-relay-only
#>
param(
    [string]$Root = "",
    [int]$Port = 8765,
    [string]$Profile = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $Root = Resolve-Path $Root
}

$py = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $py) {
    $py = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
if (-not $py) {
    Write-Error "Python not found (need python -m http.server)."
}

$qp = @()
if ($Profile) { $qp += "profile=" + [uri]::EscapeDataString($Profile) }
$envFile = Join-Path $Root "config\webrtc.ice.local.env"
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^WEBRTC_ROOM=(.+)$') { $qp += "room=" + [uri]::EscapeDataString($matches[1]) }
        if ($line -match '^WEBRTC_ICE_CONFIG_URL=(.+)$') { $qp += "ice=" + [uri]::EscapeDataString($matches[1]) }
        if ($line -match '^WEBRTC_ICE_CONFIG_TOKEN=(.+)$' -and $matches[1]) {
            $qp += "iceToken=" + [uri]::EscapeDataString($matches[1])
        }
    }
}
$q = if ($qp.Count) { "?" + ($qp -join "&") } else { "" }
$openUrl = "http://127.0.0.1:${Port}/webrtc-client.html${q}"

Write-Host "Serving: $Root"
Write-Host "Open:    $openUrl"
Write-Host "Stop:    Ctrl+C"
Write-Host ""

if (-not $NoBrowser) {
    Start-Process $openUrl
}

Set-Location $Root
& $py -m http.server $Port --bind 127.0.0.1
