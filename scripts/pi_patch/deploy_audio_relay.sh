#!/usr/bin/env bash
# Отдельный лёгкий аудио-канал Pi→VPS→браузер; WebRTC только видео+управление.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_publisher.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/audio_relay_publisher.py"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/cloud/audio_relay_store.py" \
  "$HOST:/tmp/audio_relay_store.py"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/cloud/ice_config_server.py" \
  "$HOST:/tmp/ice_config_server.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
out = []
skip_audio = False
for ln in lines:
    if ln.startswith("WEBRTC_AUDIO"):
        if not skip_audio:
            out.append("WEBRTC_AUDIO=0")
            out.append("WEBRTC_AUDIO_PLAYBACK=0")
            skip_audio = True
        continue
    if ln.startswith("WEBRTC_AUDIO_"):
        continue
    out.append(ln)
if not skip_audio:
    out.append("WEBRTC_AUDIO=0")
    out.append("WEBRTC_AUDIO_PLAYBACK=0")
extras = {
    "AUDIO_RELAY_ENABLED": "1",
    "AUDIO_RELAY_URL": "http://116.203.148.254/api/audio-relay",
}
seen = {x.split("=", 1)[0] for x in out if "=" in x and not x.startswith("#")}
for k, v in extras.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env ok")
PY

# systemd для publisher (перезапуск при обрыве)
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo tee /etc/systemd/system/pi-audio-relay.service > /dev/null" <<'UNIT'
[Unit]
Description=Pi microphone → VPS audio relay (separate from WebRTC video)
After=network-online.target camstream.service
Wants=network-online.target

[Service]
Type=simple
User=pavel
Group=pavel
WorkingDirectory=/home/pavel/projects/Mobile_Raspberry_5-
EnvironmentFile=-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env
ExecStart=/home/pavel/projects/Mobile_Raspberry_5-/.venv/bin/python /home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/audio_relay_publisher.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "chmod +x /home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/audio_relay_publisher.py; \
   sudo systemctl daemon-reload; \
   sudo systemctl enable pi-audio-relay.service; \
   sudo systemctl restart pi-audio-relay.service; \
   sleep 2; systemctl is-active pi-audio-relay.service; \
   journalctl -u pi-audio-relay -n 8 --no-pager; \
   sudo systemctl restart camstream.service; sleep 2; systemctl is-active camstream.service"

# VPS: залить relay через Pi (если есть root key)
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "scp -o StrictHostKeyChecking=no /tmp/audio_relay_store.py /tmp/ice_config_server.py root@116.203.148.254:/root/project/cloud/ 2>/dev/null && \
   ssh -o StrictHostKeyChecking=no root@116.203.148.254 'systemctl restart ice-config-server 2>/dev/null; sleep 1; curl -s http://127.0.0.1:8788/api/audio-relay/rooms/pi-camera/status' \
   || echo 'VPS: deploy cloud/*.py manually + systemctl restart ice-config-server'" 2>&1

echo "done — browser: отдельный audio relay; WebRTC без m=audio"
