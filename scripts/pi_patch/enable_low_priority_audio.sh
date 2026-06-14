#!/usr/bin/env bash
# Pi: включить микрофон → браузер с низким приоритетом (16 kHz mono, ~16 kbit/s Opus).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
SIGNAL_URL="${SIGNAL_URL:-http://116.203.148.254/api/signal/rooms/pi-camera}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/webrtc_audio.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_audio.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = host.read_text(encoding="utf-8")

if "apply_low_priority_audio_sdp" not in text:
    text = text.replace(
        "from rpi_tools.webrtc_audio import WebRTCAudioBridge",
        "from rpi_tools.webrtc_audio import (\n"
        "    WebRTCAudioBridge,\n"
        "    apply_low_priority_audio_sdp,\n"
        "    limit_audio_sender_bitrate,\n"
        ")",
        1,
    )

if "limit_audio_sender_bitrate(self._audio_tx.sender)" not in text:
    old = (
        "                self._audio_tx.sender.replaceTrack(mic_track)\n"
        "                log.info(\n"
        "                    \"WebRTC: I2S-микрофон привязан к audio transceiver (direction=%s)\",\n"
    )
    new = (
        "                self._audio_tx.sender.replaceTrack(mic_track)\n"
        "                await limit_audio_sender_bitrate(self._audio_tx.sender)\n"
        "                log.info(\n"
        "                    \"WebRTC: I2S-микрофон привязан к audio transceiver (direction=%s)\",\n"
    )
    if old not in text:
        raise SystemExit("webrtc_host.py: mic replaceTrack anchor missing")
    text = text.replace(old, new, 1)

sdp_old = "        answer_sdp = self._pc.localDescription.sdp\n"
sdp_new = (
    "        answer_sdp = apply_low_priority_audio_sdp(self._pc.localDescription.sdp)\n"
)
if sdp_new.strip() not in text:
    if sdp_old not in text:
        raise SystemExit("webrtc_host.py: answer_sdp anchor missing")
    text = text.replace(sdp_old, sdp_new, 1)

host.write_text(text, encoding="utf-8")
print("patched", host)

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "WEBRTC_AUDIO": "1",
    "WEBRTC_AUDIO_PLAYBACK": "0",
    "WEBRTC_AUDIO_SAMPLE_RATE": "16000",
    "WEBRTC_AUDIO_CHANNELS": "1",
    "WEBRTC_AUDIO_MAX_BITRATE": "16000",
    "WEBRTC_AUDIO_BUFFER_SIZE": "4096",
    "WEBRTC_AUDIO_PERIOD_SIZE": "512",
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
        if key not in ("WEBRTC_AUDIO",):
            seen.add(key)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "grep WEBRTC_AUDIO /home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service; sleep 4; \
   systemctl is-active camstream.service; \
   journalctl -u camstream -n 8 --no-pager | tail -5"

echo "Pi: low-priority audio enabled (16 kHz / 16 kbit/s)"
