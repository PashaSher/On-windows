#!/usr/bin/env bash
# Pi: replaceTrack в aiortc синхронный — await ломает createAnswer (TypeError).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path
p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = p.read_text(encoding="utf-8")
old = text
text = text.replace("await self._audio_tx.sender.replaceTrack(None)", "self._audio_tx.sender.replaceTrack(None)")
text = text.replace("await self._audio_tx.sender.replaceTrack(track)", "self._audio_tx.sender.replaceTrack(track)")
if text == old:
    print("already fixed")
else:
    p.write_text(text, encoding="utf-8")
    print("patched replaceTrack awaits")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service"
echo "done"
