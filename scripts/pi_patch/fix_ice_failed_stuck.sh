#!/usr/bin/env bash
# Pi: ice=failed не должен держать сессию вечно из-за старых RTP-пакетов (media_up).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = p.read_text()

old = """            if cs in ("failed", "disconnected") or ice in ("failed", "disconnected"):
                if media_up:
                    self._disconnect_since = None
                else:
                    if self._disconnect_since is None:
                        self._disconnect_since = now
                        log.warning(
                            "WebRTC: нестабильность %s/%s — grace %.0fs",
                            cs,
                            ice,
                            _ICE_DISCONNECT_GRACE_SEC,
                        )
                    elif now - self._disconnect_since >= _ICE_DISCONNECT_GRACE_SEC:
                        self._request_session_end(
                            f"unstable >{_ICE_DISCONNECT_GRACE_SEC:.0f}s ({cs}/{ice} pkts={pkts})"
                        )
                        break"""

new = """            if cs in ("failed", "disconnected") or ice in ("failed", "disconnected"):
                # ice=failed = браузер не получит видео; старые pkts не отменяют таймер
                tolerate = media_up and ice not in ("failed",) and cs not in ("failed",)
                if tolerate:
                    self._disconnect_since = None
                else:
                    if self._disconnect_since is None:
                        self._disconnect_since = now
                        log.warning(
                            "WebRTC: нестабильность %s/%s — grace %.0fs (media_up=%s)",
                            cs,
                            ice,
                            _ICE_DISCONNECT_GRACE_SEC,
                            media_up,
                        )
                    elif now - self._disconnect_since >= _ICE_DISCONNECT_GRACE_SEC:
                        self._request_session_end(
                            f"unstable >{_ICE_DISCONNECT_GRACE_SEC:.0f}s ({cs}/{ice} pkts={pkts})"
                        )
                        break"""

if old not in text:
    if "tolerate = media_up and ice not in" in text:
        print("already patched", p)
    else:
        raise SystemExit("target block not found")
else:
    p.write_text(text.replace(old, new))
    print("patched", p)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service"
echo "camstream restarted"
