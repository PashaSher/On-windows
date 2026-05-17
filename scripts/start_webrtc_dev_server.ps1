#Requires -Version 5.1
<#
.SYNOPSIS
  Запускает http.server на 8765 (если ещё не слушает) и открывает webrtc-client в браузере.

.EXAMPLE
  powershell -File .\scripts\start_webrtc_dev_server.ps1
  powershell -File .\scripts\start_webrtc_dev_server.ps1 -Profile hetzner-relay-only
#>
param(
    [string]$Root = "",
    [int]$Port = 8765,
    [string]$Profile = "",
    [switch]$NoBrowser,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Resolve-Path (Join-Path $PSScriptRoot "..")
} else {
    $Root = Resolve-Path $Root
}

function Test-PortListening {
    param([int]$P)
    try {
        $c = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop | Select-Object -First 1
        return $null -ne $c
    } catch {
        return $false
    }
}

function Stop-PortListener {
    param([int]$P)
    $conns = Get-NetTCPConnection -LocalPort $P -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        if ($procId -and $procId -gt 0) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 400
}

$py = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $py) {
    $py = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
if (-not $py) {
    Write-Error "Python not found."
}

if ($ForceRestart -and (Test-PortListening -P $Port)) {
    Write-Host "Restarting server on port $Port..."
    Stop-PortListener -P $Port
}

$serverRunning = Test-PortListening -P $Port

if (-not $serverRunning) {
    $serveScript = Join-Path $PSScriptRoot "serve_webrtc_operator.ps1"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $serveScript,
        "-Root", $Root,
        "-Port", $Port,
        "-NoBrowser"
    )
    if ($Profile) {
        $args += @("-Profile", $Profile)
    }
    Write-Host "Starting HTTP server in new window (port $Port)..."
    Start-Process powershell -ArgumentList $args -WorkingDirectory $Root
    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -P $Port) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-PortListening -P $Port)) {
        Write-Error "Server did not start on port $Port. Run manually: python -m http.server $Port --bind 127.0.0.1"
    }
    Write-Host "[ok] Server listening on http://127.0.0.1:$Port/"
} else {
    Write-Host "[ok] Server already on port $Port"
}

$qp = @("_=" + [int][double]::Parse((Get-Date -UFormat %s)))
if ($Profile) {
    $qp += "profile=" + [uri]::EscapeDataString($Profile)
}
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
$openUrl = "http://127.0.0.1:${Port}/webrtc-client.html?" + ($qp -join "&")

Write-Host "URL: $openUrl"
Write-Host ""
Write-Host "Debug:"
Write-Host "  - Keep the 'WebRTC HTTP server' PowerShell window open (Ctrl+C stops the site)"
Write-Host "  - Hard reload: Ctrl+F5 in browser"
Write-Host "  - DevTools: F12 -> Console / Network"
Write-Host "  - VS Code: Run -> WebRTC: оператор (http localhost) starts server + Chrome debugger"

if (-not $NoBrowser) {
    Start-Process $openUrl
}
