#!/usr/bin/env bash
# Pi: один gated audio track вместо replaceTrack (фикс Opus AudioResampler).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/webrtc_audio.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_audio.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
text = host.read_text(encoding="utf-8")

if "self._ptl_track" not in text:
    text = text.replace(
        "        self._listen_active = False\n",
        "        self._listen_active = False\n        self._ptl_track = None\n",
        1,
    )

old_answer = """        if self._audio_tx is not None:
            self._audio_tx.direction = "sendonly"
            self._audio_tx.sender.replaceTrack(self._audio_bridge.get_silent_track())
            log.info(
                "WebRTC: audio transceiver готов — микрофон по кнопке pi_listen (ALSA %s)",
                self._audio_bridge.alsa_device,
            )"""

new_answer = """        if self._audio_tx is not None:
            self._audio_bridge.reset_push_to_listen_track()
            self._ptl_track = self._audio_bridge.get_push_to_listen_track()
            self._audio_tx.direction = "sendonly"
            self._audio_tx.sender.replaceTrack(self._ptl_track)
            log.info(
                "WebRTC: gated audio track — микрофон по pi_listen (ALSA %s, 48 kHz)",
                self._audio_bridge.alsa_device,
            )"""

if new_answer not in text:
    if old_answer not in text:
        # fallback variants
        for old in (
            old_answer.replace("get_silent_track()", "None"),
            old_answer.replace("get_silent_track()", "self._audio_bridge.get_silent_track()"),
        ):
            if old in text:
                text = text.replace(old, new_answer, 1)
                break
        else:
            raise SystemExit("answer audio block not found")
    else:
        text = text.replace(old_answer, new_answer, 1)

old_listen = '''    async def _set_pi_listen(self, on: bool) -> None:
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
        log.info("WebRTC: pi_listen OFF — только видео")'''

new_listen = '''    async def _set_pi_listen(self, on: bool) -> None:
        if not self._audio_bridge.enabled or self._ptl_track is None:
            log.warning("WebRTC: pi_listen — нет gated audio track")
            return
        if on:
            if self._listen_active:
                return
            if not self._ptl_track.set_listen(True):
                log.warning("WebRTC: pi_listen ON — микрофон недоступен")
                return
            self._listen_active = True
            if self._audio_tx is not None:
                await limit_audio_sender_bitrate(self._audio_tx.sender)
            log.info("WebRTC: pi_listen ON — микрофон")
            return
        if not self._listen_active:
            return
        self._listen_active = False
        self._ptl_track.set_listen(False)
        log.info("WebRTC: pi_listen OFF — только видео")'''

if new_listen not in text:
    if old_listen not in text:
        raise SystemExit("_set_pi_listen block not found")
    text = text.replace(old_listen, new_listen, 1)

host.write_text(text, encoding="utf-8")
print("webrtc_host.py patched")

# 48 kHz для Opus/WebRTC
lines = env.read_text(encoding="utf-8").splitlines()
out, seen = [], set()
for ln in lines:
    if ln.startswith("WEBRTC_AUDIO_SAMPLE_RATE="):
        out.append("WEBRTC_AUDIO_SAMPLE_RATE=48000")
        seen.add("WEBRTC_AUDIO_SAMPLE_RATE")
    else:
        out.append(ln)
if "WEBRTC_AUDIO_SAMPLE_RATE" not in seen:
    out.append("WEBRTC_AUDIO_SAMPLE_RATE=48000")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env: WEBRTC_AUDIO_SAMPLE_RATE=48000")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service && sleep 4 && systemctl is-active camstream.service"

echo "done — gated audio track deployed"
