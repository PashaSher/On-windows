#!/usr/bin/env bash
# Pi: patched operator UI + local proxy (звук pi-audio DC + управление без деплоя VPS HTML).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WEB_SRC="$REPO/deploy/www"
EXAMPLES_SRC="$REPO/examples"
HTML_SRC="$REPO/webrtc-client.html"
PROXY="$REPO/scripts/pi_patch/operator_proxy_server.py"
REMOTE_WEB="/home/pavel/operator-web"
REMOTE_PROXY_DIR="/home/pavel/operator-proxy"
REMOTE_PROXY="$REMOTE_PROXY_DIR/server.py"
SERVICE="/etc/systemd/system/operator-proxy.service"
TOKEN="698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"

echo "== sync operator web to Pi =="
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "mkdir -p '$REMOTE_WEB/examples' '$REMOTE_PROXY_DIR'"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$WEB_SRC/webrtc_ice_operator_fetch.js" \
  "$WEB_SRC/cam.html" \
  "$WEB_SRC/ping.html" \
  "$HOST:$REMOTE_WEB/"
sshpass -e scp -o StrictHostKeyChecking=no -r "$EXAMPLES_SRC" "$HOST:$REMOTE_WEB/"
sshpass -e scp -o StrictHostKeyChecking=no "$HTML_SRC" "$HOST:$REMOTE_WEB/webrtc-client.html"
sshpass -e scp -o StrictHostKeyChecking=no "$PROXY" "$HOST:$REMOTE_PROXY"

echo "== sync operator web to Pi =="
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo tee '$SERVICE' >/dev/null" <<EOF
[Unit]
Description=Operator web proxy (patched UI, VPS API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pavel
Environment=OPERATOR_WEB_ROOT=$REMOTE_WEB
Environment=OPERATOR_VPS_ORIGIN=http://116.203.148.254
ExecStart=/usr/bin/python3 $REMOTE_PROXY --host 0.0.0.0 --port 8888
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl daemon-reload && sudo systemctl restart operator-proxy.service && \
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8888/cam"

IP=$(sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "hostname -I | awk '{print \$1}'")
TS=$(sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "tailscale ip -4 2>/dev/null || true")
echo ""
echo "Готово."
echo "  Tailscale (нужен включённый Tailscale на телефоне/ПК):"
echo "    http://${TS:-100.73.9.95}:8888/cam"
echo "  Домашний Wi‑Fi (тот же роутер, что Pi):"
echo "    http://${IP}:8888/cam"
echo "  Публичный VPS (без кнопки 🔊 — только видео+управление):"
echo "    http://116.203.148.254/cam"
