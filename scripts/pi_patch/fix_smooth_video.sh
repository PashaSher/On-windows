#!/usr/bin/env bash
# Pi: равномерный FPS (pace recv) + легче битрейт для TURN — убирает слайдшоу.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path
import re

video = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_video.py")
text = video.read_text(encoding="utf-8")

if "_last_frame_index" not in text:
    text = text.replace(
        "        self._packets_total = 0\n        self._t0 = 0.0\n",
        "        self._packets_total = 0\n        self._t0 = 0.0\n"
        "        self._last_frame_index = -1\n        self._last_nal_mono = 0.0\n",
        1,
    )

text = text.replace(
    "                packet.pts = self._frame_pts()\n",
    "                now = time.monotonic()\n"
    "                gap = now - self._last_nal_mono if self._last_nal_mono > 0 else 999.0\n"
    "                if (\n"
    "                    packet.is_keyframe\n"
    "                    or self._last_frame_index < 0\n"
    "                    or gap > (0.6 / max(1.0, float(self._fps)))\n"
    "                ):\n"
    "                    self._last_frame_index += 1\n"
    "                self._last_nal_mono = now\n"
    "                packet.pts = self._last_frame_index * self._pts_step\n",
)

old_recv = """        self._got_first_frame = True
        return packet"""

new_recv = """        self._got_first_frame = True
        return packet"""

if old_recv not in text:
    raise SystemExit("recv() block not found — webrtc_video.py changed?")
text = text.replace(old_recv, new_recv, 1)

video.write_text(text, encoding="utf-8")
print("patched", video)

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "CAMSTREAM_VIDEO_BITRATE": "550000",
    "CAMSTREAM_VIDEO_FPS": "20",
    "CAMSTREAM_VIDEO_WIDTH": "640",
    "CAMSTREAM_VIDEO_HEIGHT": "360",
    "CAMSTREAM_VIDEO_INTRA": "8",
}
out = []
seen = set()
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
  "curl -s -X POST 'http://116.203.148.254/api/signal/clear?room=pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' >/dev/null 2>&1 || true; \
   sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && \
   ps aux | grep stream_camera | grep -v grep"
