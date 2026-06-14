#!/usr/bin/env bash
# Основная рабочая версия (production):
#   - WebRTC: только видео + управление (WEBRTC_AUDIO=0)
#   - Звук: дуплексный HTTP relay (Pi↔браузер), отдельно от WebRTC
#   - Публичный URL: Cloudflare quick tunnel → operator-proxy :8888
#
# Использование:
#   PI_HOST=pavel@100.73.9.95 PI_SSH_PASS=... ICE_TOKEN=... bash scripts/deploy_production.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== [1/2] Duplex audio on Pi (C tunnel + talk player) ==="
bash "$REPO/scripts/pi_patch/enable_duplex_http_audio.sh"

echo ""
echo "=== [2/2] Public operator (cloudflared + bootstrap) ==="
bash "$REPO/scripts/pi_patch/enable_public_cloudflare.sh"

echo ""
echo "Production deploy complete."
echo "  Tailscale:  http://100.73.9.95:8888/cam"
echo "  Public:     see PUBLIC_CAM above (cloudflared URL /cam)"
