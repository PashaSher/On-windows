#!/usr/bin/env bash
# Pi: ffmpeg (C) Opus/Ogg → audio_relay_publish (C) → local operator-proxy
set -euo pipefail

ENV_FILE="${ENV_FILE:-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env}"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

TOKEN="${ICE_CONFIG_TOKEN:?ICE_CONFIG_TOKEN required}"
ROOM="${WEBRTC_ROOM:-pi-camera}"
PUBLISH_URL="${AUDIO_RELAY_PUBLISH_URL:-http://127.0.0.1:8888/api/audio-relay/rooms/${ROOM}/publish}"
DEVICE="${AUDIO_RELAY_ALSA:-${WEBRTC_AUDIO_ALSA:-plughw:2,0}}"
BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLISH_BIN="${BIN_DIR}/audio_relay_publish"

if [[ ! -x "$PUBLISH_BIN" ]]; then
  echo "compile audio_relay_publish.c first" >&2
  exit 1
fi

exec 2>&1
echo "audio relay: ffmpeg Opus → $PUBLISH_URL (device=$DEVICE)"

while true; do
  ffmpeg -hide_banner -loglevel error \
    -f alsa -i "$DEVICE" \
    -ac 1 -ar 48000 \
    -c:a libopus -b:a 32k -application voip -frame_duration 20 \
    -f ogg -flush_packets 1 pipe:1 \
  | "$PUBLISH_BIN" "$PUBLISH_URL" "$TOKEN" "audio/ogg" \
  || echo "relay cycle ended — retry in 2s"
  sleep 2
done
