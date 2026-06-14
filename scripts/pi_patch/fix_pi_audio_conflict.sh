#!/usr/bin/env bash
# Pi publish напрямую на ice-config-server :8788 — nginx :80 не успевает читать chunked POST (Send-Q → обрыв каждые ~33с).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
VPS="${VPS:-116.203.148.254}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_player.py" \
  "$REPO/scripts/pi_patch/audio_relay_tunnel.c" \
  "$REPO/scripts/pi_patch/run_audio_relay_tunnel.sh" \
  "$HOST:/home/pavel/operator-proxy/"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "cc -O2 -o /home/pavel/operator-proxy/audio_relay_tunnel /home/pavel/operator-proxy/audio_relay_tunnel.c -lasound"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<PY
from pathlib import Path
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "AUDIO_RELAY_PUBLISH_URL": "http://${VPS}:8788/api/audio-relay/rooms/pi-camera/publish",
    "AUDIO_RELAY_URL": "http://${VPS}/api/audio-relay",
    "AUDIO_TALK_LISTEN_URL": "http://${VPS}/api/audio-relay/rooms/pi-camera/talk-listen",
}
out = []
seen = set()
for ln in lines:
    if "=" in ln and not ln.startswith("#"):
        key = ln.split("=", 1)[0]
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(ln)
    else:
        out.append(ln)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\\n".join(out) + "\\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart pi-audio-relay pi-audio-talk; sleep 2; systemctl is-active pi-audio-relay pi-audio-talk; journalctl -u pi-audio-relay -n 2 --no-pager"

echo "Pi audio fix applied"
