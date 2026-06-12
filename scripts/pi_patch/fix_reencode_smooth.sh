#!/usr/bin/env bash
# Pi: decode+re-encode вместо passthrough — ровный FPS и битрейт для TURN.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "CAMSTREAM_WEBRTC_H264_PASSTHROUGH": "0",
    "CAMSTREAM_VIDEO_BITRATE": "280000",
    "CAMSTREAM_VIDEO_FPS": "10",
    "CAMSTREAM_VIDEO_WIDTH": "424",
    "CAMSTREAM_VIDEO_HEIGHT": "240",
    "CAMSTREAM_VIDEO_INTRA": "30",
    "CAMSTREAM_CAMERA_PRESET": "sport",
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
print("updated", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && \
   ps aux | grep stream_camera | grep -v grep"
