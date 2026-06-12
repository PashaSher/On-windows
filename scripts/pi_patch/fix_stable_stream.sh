#!/usr/bin/env bash
# Pi: ровный PTS + pace по кадрам — стабильный поток без дропов в браузере.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

video = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_video.py")
text = video.read_text(encoding="utf-8")

old_init = """        self._packets_total = 0
        self._t0 = 0.0

    def _frame_pts(self) -> int:
        elapsed = max(0.0, time.monotonic() - self._t0)
        frame_index = int(elapsed * self._fps)
        return frame_index * self._pts_step"""

new_init = """        self._packets_total = 0
        self._t0 = 0.0
        self._nal_in_frame = 0
        self._frame_counter = 0
        self._last_out_pts: int | None = None
        self._last_frame_mono = 0.0
        self._nals_per_frame = 4"""

if old_init not in text:
    raise SystemExit("init block not found")
text = text.replace(old_init, new_init, 1)

text = text.replace(
    "        self._packets_total = 0\n        self._t0 = time.monotonic()\n",
    "        self._packets_total = 0\n        self._t0 = time.monotonic()\n"
    "        self._nal_in_frame = 0\n        self._frame_counter = 0\n"
    "        self._last_out_pts = None\n        self._last_frame_mono = 0.0\n",
)

text = text.replace(
    "                packet.pts = self._frame_pts()\n",
    "                if self._nal_in_frame == 0:\n"
    "                    self._frame_counter += 1\n"
    "                packet.pts = self._frame_counter * self._pts_step\n"
    "                self._nal_in_frame = (self._nal_in_frame + 1) % self._nals_per_frame\n",
)

old_recv = """        self._got_first_frame = True
        return packet"""

new_recv = """        self._got_first_frame = True
        if self._last_out_pts is None or packet.pts != self._last_out_pts:
            interval = 1.0 / max(1.0, float(self._fps))
            now = time.monotonic()
            if self._last_frame_mono > 0:
                delay = interval - (now - self._last_frame_mono)
                if delay > 0.001:
                    await asyncio.sleep(delay)
            self._last_frame_mono = time.monotonic()
            self._last_out_pts = packet.pts
        return packet"""

if old_recv not in text:
    raise SystemExit("recv block not found")
text = text.replace(old_recv, new_recv, 1)

video.write_text(text, encoding="utf-8")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "CAMSTREAM_VIDEO_BITRATE": "420000",
    "CAMSTREAM_VIDEO_FPS": "15",
    "CAMSTREAM_VIDEO_WIDTH": "480",
    "CAMSTREAM_VIDEO_HEIGHT": "270",
    "CAMSTREAM_VIDEO_INTRA": "8",
    "CAMSTREAM_WEBRTC_H264_PASSTHROUGH": "1",
    "WEBRTC_AUDIO": "0",
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
  "curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && \
   ps aux | grep 'stream_camera.py webrtc' | grep -v grep"
