#!/usr/bin/env bash
# Pi: крупнее картинка + чаще ключевые кадры (меньше слайдшоу при потерях TURN).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
ups = {
    "WEBRTC_AUDIO": "0",
    "CAMSTREAM_VIDEO_BITRATE": "850000",
    "CAMSTREAM_VIDEO_FPS": "20",
    "CAMSTREAM_VIDEO_WIDTH": "960",
    "CAMSTREAM_VIDEO_HEIGHT": "540",
    "CAMSTREAM_VIDEO_INTRA": "5",
}
lines = []
for ln in p.read_text().splitlines():
    if not ln.strip() or ln.strip().startswith("#"):
        lines.append(ln)
        continue
    k = ln.split("=", 1)[0].strip()
    if k.startswith("WEBRTC_AUDIO_"):
        continue
    if k in ups:
        lines.append(f"{k}={ups.pop(k)}")
    elif k not in ups:
        lines.append(ln)
for k, v in ups.items():
    lines.append(f"{k}={v}")
p.write_text("\n".join(lines) + "\n")
print(p.read_text())
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 2 && ps aux | grep 'stream_camera.py webrtc' | grep -v grep"
