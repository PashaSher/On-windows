#!/usr/bin/env bash
# Pi: полностью без аудио в WebRTC + чуть больше битрейт под видео.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = []
for raw in env.read_text().splitlines():
    if raw.startswith("WEBRTC_AUDIO") or raw.startswith("WEBRTC_AUDIO_"):
        continue
    lines.append(raw)
lines.append("WEBRTC_AUDIO=0")
lines.append("CAMSTREAM_VIDEO_BITRATE=500000")
lines.append("CAMSTREAM_VIDEO_FPS=20")
lines.append("CAMSTREAM_VIDEO_WIDTH=480")
lines.append("CAMSTREAM_VIDEO_HEIGHT=270")
lines.append("CAMSTREAM_VIDEO_INTRA=10")
# dedupe keys
out, seen = [], set()
for ln in lines:
    k = ln.split("=", 1)[0] if "=" in ln and not ln.startswith("#") else ln
    if k in seen:
        continue
    if "=" in ln and not ln.startswith("#"):
        seen.add(k)
    out.append(ln)
env.write_text("\n".join(out) + "\n")
print("updated", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "grep -E 'WEBRTC_AUDIO|CAMSTREAM_VIDEO' /home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env; sudo systemctl restart camstream.service; sleep 2; systemctl is-active camstream.service; ps aux | grep 'stream_camera.py webrtc' | grep -v grep"
echo "Pi: audio removed, camstream restarted"
