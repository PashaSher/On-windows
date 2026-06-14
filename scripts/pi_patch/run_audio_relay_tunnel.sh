#!/usr/bin/env bash
# Pi: ALSA → audio_relay_tunnel (C) → local operator-proxy
set -euo pipefail

ENV_FILE="${ENV_FILE:-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env}"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

TOKEN="${ICE_CONFIG_TOKEN:?ICE_CONFIG_TOKEN required}"
ROOM="${WEBRTC_ROOM:-pi-camera}"
PUBLISH_URL="${AUDIO_RELAY_PUBLISH_URL:-http://127.0.0.1:8888/api/audio-relay/rooms/${ROOM}/publish}"
DEVICE="${AUDIO_RELAY_ALSA:-${WEBRTC_AUDIO_ALSA:-plughw:2,0}}"
BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
TUNNEL_BIN="${BIN_DIR}/audio_relay_tunnel"

if [[ ! -x "$TUNNEL_BIN" ]]; then
  echo "compile audio_relay_tunnel.c first" >&2
  exit 1
fi

exec "$TUNNEL_BIN" "$PUBLISH_URL" "$TOKEN" "$DEVICE"
