#!/usr/bin/env bash
# Звук Pi → браузер по отдельному Data Channel (не WebRTC audio / не TURN RTP).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_dc.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/audio_relay_dc.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = host.read_text(encoding="utf-8")

if "from rpi_tools.audio_relay_dc import" not in text:
    anchor = "from rpi_tools.webrtc_vps_signaling import VpsSignaling, make_signaling\n"
    if anchor not in text:
        raise SystemExit("import anchor missing")
    text = text.replace(
        anchor,
        anchor + "from rpi_tools.audio_relay_dc import AudioDcRelay, dc_audio_enabled\n",
        1,
    )

if "self._audio_dc_relay" not in text:
    text = text.replace(
        "        self._audio_tx = None\n",
        "        self._audio_tx = None\n        self._audio_dc_relay = None\n        self._audio_dc = None\n",
        1,
    )

insert_marker = "        answer = await self._pc.createAnswer()\n"
insert_block = (
    "        if dc_audio_enabled():\n"
    "            self._audio_dc_relay = AudioDcRelay()\n"
    "            self._audio_dc = self._pc.createDataChannel(\n"
    '                "pi-audio", ordered=False, maxRetransmits=0\n'
    "            )\n"
    "\n"
    "            @self._audio_dc.on(\"open\")\n"
    "            def _on_pi_audio_dc_open() -> None:\n"
    "                if self._audio_dc_relay and self._audio_dc:\n"
    "                    self._audio_dc_relay.start(self._audio_dc)\n"
    "                    log.info(\"WebRTC: pi-audio DataChannel open — PCM relay\")\n"
    "\n"
    "            log.info(\"WebRTC: pi-audio DataChannel создан (отдельно от m=audio)\")\n"
    "\n"
)

if "pi-audio DataChannel создан" not in text:
    if insert_marker not in text:
        raise SystemExit("createAnswer marker missing")
    text = text.replace(insert_marker, insert_block + insert_marker, 1)

cleanup_old = "        try:\n            await self._audio_bridge.stop()\n        except Exception:\n            log.debug(\"WebRTC: audio bridge stop\", exc_info=True)"
cleanup_new = (
    "        try:\n"
    "            if self._audio_dc_relay:\n"
    "                self._audio_dc_relay.stop()\n"
    "                self._audio_dc_relay = None\n"
    "                self._audio_dc = None\n"
    "            await self._audio_bridge.stop()\n"
    "        except Exception:\n"
    "            log.debug(\"WebRTC: audio bridge stop\", exc_info=True)"
)
if cleanup_new not in text and cleanup_old in text:
    text = text.replace(cleanup_old, cleanup_new, 1)
elif "self._audio_dc_relay.stop()" in text:
    pass
else:
    raise SystemExit("cleanup anchor missing")

host.write_text(text, encoding="utf-8")
print("patched webrtc_host.py")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
out = []
skip = False
for ln in lines:
    if ln.startswith("WEBRTC_AUDIO"):
        if not skip:
            out.append("WEBRTC_AUDIO=0")
            out.append("WEBRTC_AUDIO_PLAYBACK=0")
            skip = True
        continue
    if ln.startswith("WEBRTC_AUDIO_"):
        continue
    if ln.startswith("AUDIO_RELAY_URL") or ln.startswith("AUDIO_RELAY_ENABLED"):
        continue
    out.append(ln)
if not skip:
    out.append("WEBRTC_AUDIO=0")
    out.append("WEBRTC_AUDIO_PLAYBACK=0")
extras = {"AUDIO_RELAY_DC": "1"}
seen = {x.split("=", 1)[0] for x in out if "=" in x and not x.startswith("#")}
for k, v in extras.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl disable --now pi-audio-relay.service 2>/dev/null || true; \
   sudo systemctl restart camstream.service; sleep 3; \
   systemctl is-active camstream.service; \
   grep -E 'WEBRTC_AUDIO|AUDIO_RELAY' /home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env"

echo "Pi: audio via pi-audio DataChannel (WEBRTC_AUDIO=0)"
