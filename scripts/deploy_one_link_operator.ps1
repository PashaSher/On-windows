#Requires -Version 5.1
<#
.SYNOPSIS
  Деплой оператора + одна ссылка http://VPS/cam (bootstrap с VPS).

.EXAMPLE
  powershell -File scripts\deploy_one_link_operator.ps1
#>
param(
    [string]$VpsHost = "116.203.148.254",
    [string]$User = "root"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Remote = "${User}@${VpsHost}"
$IceEnv = Join-Path $RepoRoot "config\webrtc.ice.local.env"
$FbJs = Join-Path $RepoRoot "webrtc.firebase.local.js"
$FbExample = Join-Path $RepoRoot "config\webrtc.firebase.local.js.example"
$BootstrapLocal = Join-Path $env:TEMP "operator-bootstrap.json"

& (Join-Path $PSScriptRoot "deploy_operator_web_to_vps.ps1") -VpsHost $VpsHost -User $User

# Собрать bootstrap.json
$room = "pi-camera"
$token = ""
$iceUrl = "/api/ice"
if (Test-Path $IceEnv) {
    foreach ($line in Get-Content $IceEnv) {
        if ($line -match '^\s*WEBRTC_ROOM=(.+)$') { $room = $Matches[1].Trim().Trim('"') }
        if ($line -match '^\s*WEBRTC_ICE_CONFIG_TOKEN=(.+)$') { $token = $Matches[1].Trim().Trim('"') }
    }
}
$bootstrap = @{
    room             = $room
    iceConfigUrl     = $iceUrl
    iceConfigToken   = $token
}
if (Test-Path $FbJs) {
    $js = Get-Content $FbJs -Raw
    if ($js -match 'apiKey:\s*"([^"]+)"' -and $Matches[1] -notmatch 'YOUR_') {
        $bootstrap.firebase = @{
            apiKey            = $Matches[1]
            authDomain        = "bro-oppy.firebaseapp.com"
            databaseURL       = "https://bro-oppy-default-rtdb.firebaseio.com"
            projectId         = "bro-oppy"
            storageBucket     = "bro-oppy.appspot.com"
            messagingSenderId = if ($js -match 'messagingSenderId:\s*"([^"]+)"') { $Matches[1] } else { "" }
            appId             = if ($js -match 'appId:\s*"([^"]+)"') { $Matches[1] } else { "" }
        }
    }
}
$bootstrap | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $BootstrapLocal -Encoding UTF8

scp -o BatchMode=yes (Join-Path $RepoRoot "cloud\ice_config_server.py") "${Remote}:/root/project/cloud/ice_config_server.py"
scp -o BatchMode=yes (Join-Path $RepoRoot "cloud\webrtc_signal_store.py") "${Remote}:/root/project/cloud/webrtc_signal_store.py"
scp -o BatchMode=yes (Join-Path $RepoRoot "deploy\cam.html") "${Remote}:/var/www/operator/cam.html"
scp -o BatchMode=yes $BootstrapLocal "${Remote}:/etc/default/operator-bootstrap.json"

ssh -o BatchMode=yes $Remote "sed -i 's/\r$//' /etc/default/operator-bootstrap.json 2>/dev/null; chmod 600 /etc/default/operator-bootstrap.json; grep -q OPERATOR_BOOTSTRAP_FILE /etc/default/ice-config-server || echo OPERATOR_BOOTSTRAP_FILE=/etc/default/operator-bootstrap.json >> /etc/default/ice-config-server; systemctl restart ice-config-server; sleep 1; curl -s http://127.0.0.1:8788/api/operator-bootstrap | head -c 120"

Write-Host ""
Write-Host "One link for remote PC:"
Write-Host "  http://${VpsHost}/cam"
Write-Host ""
Write-Host "Pi: see deploy/PI_WEBRTC_VPS_SIGNALING.txt — switch stream_camera to pi_vps_signaling.py"
