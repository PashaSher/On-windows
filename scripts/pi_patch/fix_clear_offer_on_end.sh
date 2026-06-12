#!/usr/bin/env bash
# Pi: при завершении сессии сбрасывать offer браузера — иначе stale SDP блокирует новый Connect.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_vps_signaling.py")
text = p.read_text()

old = """        def _go() -> None:
            self._http.clear_callee_side(timeout_sec=3.0)
            patch: dict[str, Any] = {"""

new = """        def _go() -> None:
            self._http.clear_callee_side(timeout_sec=3.0)
            self._http.clear_caller_side(timeout_sec=3.0)
            patch: dict[str, Any] = {"""

if old not in text:
    if "clear_caller_side(timeout_sec=3.0)" in text:
        print("already patched", p)
    else:
        raise SystemExit("end_session block not found")
else:
    p.write_text(text.replace(old, new))
    print("patched", p)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service"
echo "camstream restarted"
