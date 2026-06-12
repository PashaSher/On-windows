#!/usr/bin/env bash
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_vps_signaling.py")
text = p.read_text()

snippet = '''
    def clear_caller_side(self, *, timeout_sec: float = 3.0) -> bool:
        """Сбросить offer и ICE браузера (X-Clear: caller)."""
        url = self._url()
        hdrs = self._headers(auth=True)
        hdrs["X-Clear"] = "caller"
        req = urllib.request.Request(url, headers=hdrs, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning("VPS: DELETE caller %s — %s (продолжаем)", self.room, e)
            return False
'''

if "def clear_caller_side" in text:
    print("already has clear_caller_side", p)
else:
    anchor = "    def clear_callee_side(self, *, timeout_sec: float = 3.0) -> bool:"
    if anchor not in text:
        raise SystemExit("anchor not found")
    text = text.replace(anchor, snippet + "\n" + anchor)
    p.write_text(text)
    print("added clear_caller_side", p)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 2 && systemctl is-active camstream.service"
echo "camstream restarted"
