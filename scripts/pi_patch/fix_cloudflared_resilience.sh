#!/usr/bin/env bash
# Cloudflare tunnel: перезапуск при смене сети + сохранение актуального URL.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL_PORT="${LOCAL_PORT:-8888}"
SERVICE="cloudflared-operator.service"
REMOTE_PROXY="/home/pavel/operator-proxy"
REMOTE_WEB="/home/pavel/operator-web"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/run_cloudflared_tunnel.sh" \
  "$REPO/scripts/pi_patch/operator_proxy_server.py" \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_PROXY/"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_WEB/webrtc-client.html"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "bash -s" <<EOF
set -euo pipefail
chmod +x "$REMOTE_PROXY/run_cloudflared_tunnel.sh"
sudo cp "$REMOTE_PROXY/run_cloudflared_tunnel.sh" /usr/local/bin/run_cloudflared_tunnel.sh 2>/dev/null || \
  sudo install -m 755 "$REMOTE_PROXY/run_cloudflared_tunnel.sh" /usr/local/bin/run_cloudflared_tunnel.sh

sudo tee /etc/systemd/system/${SERVICE} >/dev/null <<UNIT
[Unit]
Description=Cloudflare quick tunnel to operator-proxy
After=network-online.target operator-proxy.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=pavel
Environment=LOCAL_PORT=${LOCAL_PORT}
Environment=URL_FILE=${REMOTE_WEB}/public-url.txt
ExecStart=/usr/local/bin/run_cloudflared_tunnel.sh
Restart=always
RestartSec=8
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/cloudflared-network-restart.service >/dev/null <<'NET'
[Unit]
Description=Restart cloudflared after network is online
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart cloudflared-operator.service

[Install]
WantedBy=network-online.target
NET

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE}
# Не перезапускать tunnel при каждом network-online — иначе URL меняется и старый даёт 1033.
sudo systemctl disable cloudflared-network-restart.service 2>/dev/null || true
sudo systemctl restart operator-proxy.service
sleep 2
sudo systemctl restart cloudflared-operator.service
sleep 8
URL=\$(cat ${REMOTE_WEB}/public-url.txt 2>/dev/null || sudo journalctl -u ${SERVICE} -n 50 --no-pager | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com/cam' | tail -1)
echo "cloudflared: \$(systemctl is-active ${SERVICE})"
echo "PUBLIC_CAM=\${URL:-unknown}"
echo "STABLE_VPS=http://116.203.148.254/cam"
EOF
