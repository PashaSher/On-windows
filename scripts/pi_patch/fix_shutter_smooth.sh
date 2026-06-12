#!/usr/bin/env bash
# Pi: быстрая выдержка + низкий битрейт — стабильный FPS без слайдшоу на TURN.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

video = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_video.py")
text = video.read_text(encoding="utf-8")
if '"--shutter"' not in text:
    text = text.replace(
        '        "--framerate", f"{max(1.0, float(fps)):g}",\n',
        '        "--framerate", f"{max(1.0, float(fps)):g}",\n'
        '        "--shutter", "10000",\n',
    )
text = text.replace('        "--flush",\n', "")
text = text.replace(
    '"--bitrate", str(max(500_000, int(bitrate))),',
    '"--bitrate", str(max(150_000, int(bitrate))),',
)
video.write_text(text, encoding="utf-8")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "CAMSTREAM_WEBRTC_H264_PASSTHROUGH": "1",
    "CAMSTREAM_CAMERA_PRESET": "sport",
    "CAMSTREAM_EV": "0",
    "CAMSTREAM_VIDEO_BITRATE": "250000",
    "CAMSTREAM_VIDEO_FPS": "15",
    "CAMSTREAM_VIDEO_WIDTH": "320",
    "CAMSTREAM_VIDEO_HEIGHT": "180",
    "CAMSTREAM_VIDEO_INTRA": "15",
}
out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.strip().startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service && sleep 2 && systemctl is-active camstream.service"
