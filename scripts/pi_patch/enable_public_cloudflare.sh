#!/usr/bin/env bash
# Публичный оператор через Cloudflare quick tunnel (Pi operator-proxy :8888).
# Видео/управление → VPS через proxy; звук → локальный duplex HTTP relay.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_PROXY="/home/pavel/operator-proxy"
REMOTE_WEB="/home/pavel/operator-web"

echo "== public operator (cloudflared + duplex audio) =="
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/operator_proxy_server.py" \
  "$REPO/scripts/pi_patch/audio_relay_store.py" \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_PROXY/"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_WEB/webrtc-client.html"

bash "$REPO/scripts/pi_patch/install_cloudflared_tunnel.sh"

export SSHPASS="${PI_SSH_PASS:-2214}"
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart operator-proxy.service; sleep 2; \
   curl -sS -m 3 http://127.0.0.1:8888/api/operator-bootstrap; echo; \
   URL=\$(sudo journalctl -u cloudflared-operator -n 60 --no-pager | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1); \
   echo PUBLIC_CAM=\${URL}/cam"
