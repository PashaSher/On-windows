#!/usr/bin/env bash
# Pi: без pi-audio DC; микрофон только по команде pi_listen с кнопки в браузере.
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

if "self._listen_active" not in text:
    text = text.replace(
        "        self._audio_dc = None\n",
        "        self._audio_dc = None\n        self._listen_active = False\n",
        1,
    )

listen_method = '''
    async def _set_pi_listen(self, on: bool) -> None:
        if not self._audio_bridge.enabled or self._audio_tx is None:
            log.warning("WebRTC: pi_listen — нет audio transceiver")
            return
        if on:
            if self._listen_active:
                return
            self._listen_active = True
            track = self._audio_bridge.start_capture()
            if track is None:
                self._listen_active = False
                log.warning("WebRTC: pi_listen ON — микрофон недоступен")
                return
            self._audio_tx.direction = "sendonly"
            self._audio_tx.sender.replaceTrack(track)
            await limit_audio_sender_bitrate(self._audio_tx.sender)
            log.info("WebRTC: pi_listen ON — микрофон")
            return
        if not self._listen_active:
            return
        self._listen_active = False
        self._audio_bridge.stop_capture()
        self._audio_tx.sender.replaceTrack(self._audio_bridge.get_silent_track())
        log.info("WebRTC: pi_listen OFF — только видео")
'''

if "async def _set_pi_listen" not in text:
    anchor = "    _MOTION_ACTIONS = frozenset({\"drive\", \"turret_smooth\", \"turret_stop\", \"home\"})"
    if anchor not in text:
        raise SystemExit("_MOTION_ACTIONS anchor missing")
    text = text.replace(anchor, listen_method + "\n" + anchor, 1)

handler_snip = "        action = self._dc_action(obj)\n        silent = action in self._MOTION_ACTIONS"
handler_new = (
    "        action = self._dc_action(obj)\n"
    "        if action == \"pi_listen\":\n"
    "            await self._set_pi_listen(bool(obj.get(\"on\")))\n"
    "            return\n"
    "        silent = action in self._MOTION_ACTIONS"
)
if handler_new not in text:
    if handler_snip not in text:
        raise SystemExit("async handler anchor missing")
    text = text.replace(handler_snip, handler_new, 1)

old_audio = """        if self._audio_tx is not None:
            self._audio_tx.direction = (
                "sendrecv" if self._audio_bridge.playback_enabled else "sendonly"
            )
            mic_track = self._audio_bridge.start_capture()
            if mic_track is not None:
                self._audio_tx.sender.replaceTrack(mic_track)
                await limit_audio_sender_bitrate(self._audio_tx.sender)
                log.info(
                    "WebRTC: I2S-микрофон привязан к audio transceiver (direction=%s)",
                    self._audio_tx.direction,
                )
            else:
                log.warning("WebRTC: микрофон недоступен — audio send отключён")"""

new_audio = """        if self._audio_tx is not None:
            self._audio_tx.direction = "sendonly"
            self._audio_tx.sender.replaceTrack(self._audio_bridge.get_silent_track())
            log.info(
                "WebRTC: audio transceiver готов — микрофон по кнопке pi_listen (ALSA %s)",
                self._audio_bridge.alsa_device,
            )"""

if new_audio not in text:
    if old_audio not in text:
        raise SystemExit("answer audio block missing")
    text = text.replace(old_audio, new_audio, 1)

# Убрать pi-audio DataChannel
import re
text = re.sub(
    r"\n        if dc_audio_enabled\(\):.*?log\.info\(\n"
    r'                "WebRTC: pi-audio DC[^"]*"\n'
    r"            \)\n",
    "\n",
    text,
    count=1,
    flags=re.S,
)

host.write_text(text, encoding="utf-8")
print("patched webrtc_host.py")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "WEBRTC_AUDIO": "1",
    "WEBRTC_AUDIO_PLAYBACK": "0",
    "AUDIO_RELAY_DC": "0",
    "WEBRTC_AUDIO_SAMPLE_RATE": "16000",
    "WEBRTC_AUDIO_MAX_BITRATE": "16000",
    "WEBRTC_AUDIO_CHANNELS": "1",
    "CAMSTREAM_VIDEO_BITRATE": "450000",
    "CAMSTREAM_VIDEO_FPS": "20",
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

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/webrtc-client.html" \
  "$HOST:/home/pavel/operator-web/webrtc-client.html"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "grep -E 'WEBRTC_AUDIO|AUDIO_RELAY' /home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE '${SIGNAL_URL}' -H 'Authorization: Bearer ${ICE_TOKEN}' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service; sleep 4; systemctl is-active camstream.service"

echo "done — кнопка 🔊 удерживать=слушать"
