#!/usr/bin/env bash
# Деплой дуплексного HTTP-аудио на VPS (116.203.148.254) + перенастройка Pi.
# Видео и управление остаются на WebRTC — не трогаем WEBRTC_AUDIO=0.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VPS="${VPS:-116.203.148.254}"
VPS_USER="${VPS_USER:-root}"
PI_HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
ROOM="${WEBRTC_ROOM:-pi-camera}"

log() { echo "[deploy-vps-audio] $*"; }

log "1/3 — обновление Pi (звук → VPS, WebRTC без изменений)"
VPS="$VPS" ICE_TOKEN="$ICE_TOKEN" bash "$REPO/scripts/pi_patch/enable_duplex_http_audio.sh"

log "2/3 — загрузка файлов на Pi /tmp"
TMP="/tmp/vps-audio-deploy-$$"
sshpass -e ssh -o StrictHostKeyChecking=no "$PI_HOST" "mkdir -p '$TMP'"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/cloud/ice_config_server.py" \
  "$REPO/cloud/audio_relay_store.py" \
  "$REPO/deploy/nginx/operator-web.conf.example" \
  "$REPO/webrtc-client.html" \
  "$PI_HOST:$TMP/"
sshpass -e scp -r -o StrictHostKeyChecking=no \
  "$REPO/examples" \
  "$PI_HOST:$TMP/"

log "3/3 — деплой на VPS через Pi (SSH jump)"
sshpass -e ssh -o StrictHostKeyChecking=no "$PI_HOST" "VPS='$VPS' VPS_USER='$VPS_USER' ROOM='$ROOM' TMP='$TMP' bash -s" <<'REMOTE'
set -euo pipefail
REPO_VPS=""
for d in /root/On-windows /root/project; do
  if ssh -o StrictHostKeyChecking=no -o BatchMode=yes "${VPS_USER}@${VPS}" "test -d ${d}/cloud" 2>/dev/null; then
    REPO_VPS="$d"
    break
  fi
done
if [[ -z "$REPO_VPS" ]]; then
  ssh -o StrictHostKeyChecking=no "${VPS_USER}@${VPS}" "mkdir -p /root/project/cloud"
  REPO_VPS="/root/project"
fi
echo "VPS repo: $REPO_VPS"
scp -o StrictHostKeyChecking=no "$TMP/ice_config_server.py" "${VPS_USER}@${VPS}:${REPO_VPS}/cloud/"
scp -o StrictHostKeyChecking=no "$TMP/audio_relay_store.py" "${VPS_USER}@${VPS}:${REPO_VPS}/cloud/"
scp -o StrictHostKeyChecking=no "$TMP/webrtc-client.html" "${VPS_USER}@${VPS}:/var/www/operator/webrtc-client.html"
scp -o StrictHostKeyChecking=no -r "$TMP/examples" "${VPS_USER}@${VPS}:/var/www/operator/"
scp -o StrictHostKeyChecking=no "$TMP/operator-web.conf.example" "${VPS_USER}@${VPS}:/tmp/operator-web.conf.example"
ssh -o StrictHostKeyChecking=no "${VPS_USER}@${VPS}" bash -s <<VPSIN
set -euo pipefail
PUBLIC_IP=\$(curl -4 -fsS --max-time 5 https://ifconfig.me/ip 2>/dev/null || hostname -I | awk '{print \$1}')
sed "s/116\\.203\\.148\\.254/\${PUBLIC_IP}/g" /tmp/operator-web.conf.example > /etc/nginx/sites-available/operator-web
ln -sf /etc/nginx/sites-available/operator-web /etc/nginx/sites-enabled/operator-web
nginx -t
systemctl restart ice-config-server
systemctl reload nginx
sleep 2
echo -n "status: "
curl -sS -m 5 "http://127.0.0.1/api/audio-relay/rooms/${ROOM}/status"
echo
curl -sS -m 5 "http://127.0.0.1/webrtc-client.html" | grep -o 'Operator build: [^"]*' | head -1 || true
VPSIN
rm -rf "$TMP"
REMOTE

sleep 2
log "Проверка снаружи:"
curl -sS -m 8 "http://${VPS}/api/audio-relay/rooms/${ROOM}/status" || true
echo
log "Готово: http://${VPS}/cam?autostart=1"
log "Звук — отдельный поток через VPS. Видео и управление — WebRTC без изменений."
