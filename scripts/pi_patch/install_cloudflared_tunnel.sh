#!/usr/bin/env bash
# Pi: публичный HTTPS URL на operator-proxy :8888 (работает с любого устройства без Tailscale).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
LOCAL_PORT="${LOCAL_PORT:-8888}"
SERVICE="cloudflared-operator.service"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "bash -s" <<EOF
set -euo pipefail
ARCH=\$(uname -m)
case "\$ARCH" in
  aarch64|arm64) CF_ARCH=arm64 ;;
  armv7l|armv6l) CF_ARCH=arm ;;
  x86_64|amd64) CF_ARCH=amd64 ;;
  *) echo "Unsupported arch: \$ARCH"; exit 1 ;;
esac
CF=/usr/local/bin/cloudflared
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Installing cloudflared (\$CF_ARCH)..."
  tmp=\$(mktemp)
  curl -fsSL -o "\$tmp" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-\${CF_ARCH}"
  sudo install -m 755 "\$tmp" "\$CF"
  rm -f "\$tmp"
fi

sudo tee /etc/systemd/system/${SERVICE} >/dev/null <<UNIT
[Unit]
Description=Cloudflare quick tunnel to operator-proxy
After=network-online.target operator-proxy.service
Wants=network-online.target

[Service]
Type=simple
User=pavel
ExecStart=\$CF tunnel --url http://127.0.0.1:${LOCAL_PORT} --no-autoupdate
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE}
sudo systemctl restart ${SERVICE}
sleep 6
URL=\$(sudo journalctl -u ${SERVICE} -n 40 --no-pager 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)
echo ""
echo "cloudflared service: \$(systemctl is-active ${SERVICE})"
if [[ -n "\$URL" ]]; then
  echo "PUBLIC_URL=\$URL/cam"
else
  echo "PUBLIC_URL=(check: journalctl -u ${SERVICE} -f)"
fi
EOF
