param(
    [int]$VideoPort = 5000,
    [int]$ControlPort = 5001,
    [string]$GstLaunchPath = "C:\Users\pavel\AppData\Local\Programs\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe"
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-FirewallRule {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$Protocol,
        [Parameter(Mandatory = $true)][int]$LocalPort
    )

    $existing = Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        New-NetFirewallRule -DisplayName $DisplayName -Direction Inbound -Action Allow -Protocol $Protocol -LocalPort $LocalPort | Out-Null
        Write-Host "[ok] firewall rule created: $DisplayName"
    } else {
        Write-Host "[ok] firewall rule already exists: $DisplayName"
    }
}

if (-not (Test-IsAdmin)) {
    Write-Error "Run this script as Administrator."
}

Ensure-FirewallRule -DisplayName "On-windows RTP Video UDP 5000" -Protocol "UDP" -LocalPort $VideoPort
Ensure-FirewallRule -DisplayName "On-windows Romeo Control TCP 5001" -Protocol "TCP" -LocalPort $ControlPort

if (Test-Path $GstLaunchPath) {
    Write-Host "[ok] gst-launch found: $GstLaunchPath"
    & $GstLaunchPath --version
} else {
    Write-Warning "gst-launch not found at: $GstLaunchPath"
}

Write-Host ""
Write-Host "IPv4 addresses on this PC:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "169.254.*" -and
        $_.IPAddress -ne "127.0.0.1"
    } |
    Sort-Object InterfaceAlias, IPAddress |
    Format-Table InterfaceAlias, IPAddress -AutoSize

Write-Host ""
Write-Host "Recommended debug profile:"
Write-Host "  PC Parallel: RTSP + GStreamer (10.42.0.1)"
