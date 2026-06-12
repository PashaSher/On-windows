#!/usr/bin/env bash
# Pi: легче видео для TURN relay — выше FPS, меньше задержка управления.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
ENV="/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
text = p.read_text()
updates = {
    "CAMSTREAM_VIDEO_BITRATE=650000": "CAMSTREAM_VIDEO_BITRATE=380000",
    "CAMSTREAM_VIDEO_FPS=20": "CAMSTREAM_VIDEO_FPS=15",
    "CAMSTREAM_VIDEO_WIDTH=640": "CAMSTREAM_VIDEO_WIDTH=480",
    "CAMSTREAM_VIDEO_HEIGHT=360": "CAMSTREAM_VIDEO_HEIGHT=270",
    "CAMSTREAM_VIDEO_INTRA=20": "CAMSTREAM_VIDEO_INTRA=10",
    "WEBRTC_AUDIO_SAMPLE_RATE=24000": "WEBRTC_AUDIO_SAMPLE_RATE=16000",
}
for old, new in updates.items():
    if old in text:
        text = text.replace(old, new)
    elif new.split("=")[0] not in text:
        text += f"\n{new}\n"
p.write_text(text)
print("updated", p)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 2 && systemctl is-active camstream.service"
