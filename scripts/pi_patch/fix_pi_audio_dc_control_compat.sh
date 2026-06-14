#!/usr/bin/env bash
# Pi: старый VPS HTML шлёт drive JSON на pi-audio DC — пробрасываем в command handler.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
TOKEN="698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

path = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = path.read_text(encoding="utf-8")

needle = '''            log.info("WebRTC: pi-audio DataChannel создан (отдельно от m=audio)")

        answer = await self._pc.createAnswer()'''

insert = '''            _wire_data_channel(self._audio_dc)
            log.info(
                "WebRTC: pi-audio DC — PCM relay + приём JSON (совместимость со старым VPS HTML)"
            )

        answer = await self._pc.createAnswer()'''

if "_wire_data_channel(self._audio_dc)" in text:
    print("already patched")
elif needle not in text:
    raise SystemExit("anchor not found")
else:
    text = text.replace(needle, insert, 1)
    path.write_text(text, encoding="utf-8")
    print("patched webrtc_host.py")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer $TOKEN' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer $TOKEN' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service && sleep 4 && systemctl is-active camstream.service"

echo "done — переподключитесь (Ctrl+F5) на http://116.203.148.254/cam"
