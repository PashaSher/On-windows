#!/usr/bin/env bash
# Pi: выкл. WebRTC m=audio (лагает), звук только через pi-audio DC (лёгкий).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
SIGNAL_URL="${SIGNAL_URL:-http://116.203.148.254/api/signal/rooms/pi-camera}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_dc.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/audio_relay_dc.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = host.read_text(encoding="utf-8")
old = "                    self._audio_dc_relay.start(self._audio_dc)"
new = "                    self._audio_dc_relay.start(self._audio_dc, self._async_loop)"
if new not in text:
    if old not in text:
        raise SystemExit("webrtc_host start() anchor missing")
    text = text.replace(old, new, 1)
    host.write_text(text, encoding="utf-8")
    print("patched webrtc_host loop pass")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "WEBRTC_AUDIO": "0",
    "WEBRTC_AUDIO_PLAYBACK": "0",
    "AUDIO_RELAY_DC": "1",
    "CAMSTREAM_VIDEO_BITRATE": "450000",
    "CAMSTREAM_VIDEO_FPS": "20",
    "CAMSTREAM_VIDEO_WIDTH": "480",
    "CAMSTREAM_VIDEO_HEIGHT": "270",
    "CAMSTREAM_VIDEO_INTRA": "6",
}
out, seen = [], set()
for ln in lines:
    if ln.startswith("#") or "=" not in ln:
        out.append(ln)
        continue
    key = ln.split("=", 1)[0]
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    elif key.startswith("WEBRTC_AUDIO_") and key not in updates:
        continue
    else:
        out.append(ln)
        if key not in ("WEBRTC_AUDIO", "AUDIO_RELAY_DC"):
            seen.add(key)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "grep -E 'WEBRTC_AUDIO|AUDIO_RELAY|CAMSTREAM_VIDEO_FPS' /home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service; sleep 4; systemctl is-active camstream.service"

echo ""
echo "Готово: видео+управление на http://116.203.148.254/cam (без WebRTC-звука)"
echo "Звук без лагов: http://100.73.9.95:8888/cam (pi-audio DC)"
