#Requires -Version 5.1
param(
    [string]$VpsHost = "116.203.148.254",
    [string]$User = "root",
    [string]$RemoteWebRoot = "/var/www/operator"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundleScript = Join-Path $PSScriptRoot "build_operator_web_bundle.ps1"
$Www = Join-Path $RepoRoot "deploy\www"
$DeployDir = Join-Path $RepoRoot "deploy"
$Remote = "${User}@${VpsHost}"

& $BundleScript -RepoRoot $RepoRoot

ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $Remote "mkdir -p '$RemoteWebRoot' /root/project/deploy/nginx"

scp -o BatchMode=yes -r "$Www\*" "${Remote}:${RemoteWebRoot}/"
scp -o BatchMode=yes "$DeployDir\nginx\operator-web.conf.example" "${Remote}:/etc/nginx/sites-available/operator-web"

ssh -o BatchMode=yes $Remote "sed -i 's/\r$//' /etc/nginx/sites-available/operator-web; ln -sf /etc/nginx/sites-available/operator-web /etc/nginx/sites-enabled/operator-web; rm -f /etc/nginx/sites-enabled/default; chown -R www-data:www-data '$RemoteWebRoot'; nginx -t && systemctl enable nginx && systemctl restart nginx; systemctl is-active nginx; curl -s -o /dev/null -w 'index:%{http_code} ice:%{http_code}\n' http://127.0.0.1/webrtc-client.html http://127.0.0.1/api/ice"

Write-Host ""
Write-Host "Deployed: http://${VpsHost}/webrtc-client.html"
