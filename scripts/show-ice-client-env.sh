#!/usr/bin/env bash
# Показать переменные для Pi / Windows (ICE + signaling).
set -euo pipefail
ENV_FILE="${ICE_CONFIG_ENV:-/etc/default/ice-config-server}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
IP="${PUBLIC_IP:-116.203.148.254}"
echo "WEBRTC_ICE_CONFIG_URL=http://${IP}/api/ice"
echo "WEBRTC_ICE_CONFIG_TOKEN=${ICE_CONFIG_TOKEN:-}"
echo "WEBRTC_SIGNAL_URL=http://${IP}/api/signal"
echo "WEBRTC_ROOM=${WEBRTC_ROOM:-pi-camera}"
echo ""
echo "Browser:"
echo "  http://${IP}/cam?iceToken=${ICE_CONFIG_TOKEN:-}"
