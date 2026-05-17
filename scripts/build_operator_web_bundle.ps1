#Requires -Version 5.1
<#
.SYNOPSIS
  Собирает статический бандл для публикации на VPS (nginx).

.EXAMPLE
  powershell -File .\scripts\build_operator_web_bundle.ps1
#>
param(
    [string]$RepoRoot = "",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) {
    $RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DeployDir = Join-Path $RepoRoot "deploy"
} else {
    $RepoRoot = Resolve-Path $RepoRoot
}
if (-not $OutDir) {
    $OutDir = Join-Path $RepoRoot "deploy\www"
}

if (Test-Path $OutDir) {
    Remove-Item -LiteralPath $OutDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "examples") | Out-Null

$files = @(
    "webrtc-client.html",
    "webrtc-client-hetzner-debug.html",
    "webrtc_ice_operator_fetch.js"
)
foreach ($f in $files) {
    $src = Join-Path $RepoRoot $f
    $destName = Split-Path $f -Leaf
    if (-not (Test-Path $src)) { Write-Error "Missing: $src" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $OutDir $destName) -Force
}

$pingSrc = Join-Path $DeployDir "ping.html"
if (Test-Path $pingSrc) {
    Copy-Item -LiteralPath $pingSrc -Destination (Join-Path $OutDir "ping.html") -Force
}
$camSrc = Join-Path $DeployDir "cam.html"
if (Test-Path $camSrc) {
    Copy-Item -LiteralPath $camSrc -Destination (Join-Path $OutDir "cam.html") -Force
}

Get-ChildItem (Join-Path $RepoRoot "examples") -Filter "webrtc_*.js" |
    Where-Object { $_.Name -notmatch '^webrtc_firebase_' } |
    ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path (Join-Path $OutDir "examples") $_.Name) -Force
}

Write-Host ""
Write-Host "Bundle ready: $OutDir"
Write-Host "Upload to VPS: /var/www/operator/ (see deploy\OPERATOR_WEB_PUBLIC.txt)"
