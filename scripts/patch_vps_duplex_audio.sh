#!/usr/bin/env bash
# Однократное обновление VPS для дуплексного HTTP-аудио (отдельно от WebRTC).
# Запуск на сервере 116.203.148.254 от root:
#   curl -fsSL https://raw.githubusercontent.com/PashaSher/On-windows/main/scripts/patch_vps_duplex_audio.sh | sudo bash
# или после git pull:
#   sudo bash /root/On-windows/scripts/patch_vps_duplex_audio.sh
set -euo pipefail

REPO="${REPO_DIR:-}"
for d in /root/On-windows /root/project; do
  if [[ -f "$d/cloud/ice_config_server.py" ]]; then
    REPO="$d"
    break
  fi
done
if [[ -z "$REPO" ]]; then
  echo "Клонируем репозиторий…"
  REPO="/root/On-windows"
  git clone --depth 1 https://github.com/PashaSher/On-windows.git "$REPO"
fi

PUBLIC_IP="${PUBLIC_IP:-$(curl -4 -fsS --max-time 5 https://ifconfig.me/ip 2>/dev/null || hostname -I | awk '{print $1}')}"
WEB_ROOT="${WEB_ROOT:-/var/www/operator}"
NGINX_SITE="/etc/nginx/sites-available/operator-web"
ICE_ENV="/etc/default/ice-config-server"
BOOTSTRAP="/etc/default/operator-bootstrap.json"

log() { echo "[patch-vps-audio] $*"; }

[[ "$(id -u)" -eq 0 ]] || { echo "Запустите от root: sudo bash $0" >&2; exit 1; }

log "repo=$REPO public_ip=$PUBLIC_IP"

mkdir -p "$WEB_ROOT"
cp "$REPO/webrtc-client.html" "$WEB_ROOT/webrtc-client.html"
cp -r "$REPO/examples" "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"

sed "s/116\\.203\\.148\\.254/${PUBLIC_IP}/g" \
  "$REPO/deploy/nginx/operator-web.conf.example" >"$NGINX_SITE"
ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/operator-web
rm -f /etc/nginx/sites-enabled/default
nginx -t

if [[ -f "$BOOTSTRAP" ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("/etc/default/operator-bootstrap.json")
data = json.loads(p.read_text(encoding="utf-8"))
if isinstance(data, dict):
    data["audioRelayBase"] = "/api/audio-relay"
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("bootstrap: audioRelayBase added")
PY
fi

systemctl restart ice-config-server
systemctl reload nginx
sleep 2

ROOM="${WEBRTC_ROOM:-pi-camera}"
TOKEN=""
if [[ -f "$ICE_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$ICE_ENV"
fi
TOKEN="${ICE_CONFIG_TOKEN:-}"

log "Проверка:"
curl -sS -m 5 "http://127.0.0.1/api/audio-relay/rooms/${ROOM}/status"
echo
code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Version: 13' \
  "http://127.0.0.1/api/audio-relay/rooms/${ROOM}/listen-ws?token=${TOKEN}")
echo "listen-ws HTTP $code (ожидали 101)"
grep -o 'Operator build: [^"]*' "$WEB_ROOT/webrtc-client.html" | head -1 || true
log "Готово. Откройте http://${PUBLIC_IP}/cam?autostart=1"
