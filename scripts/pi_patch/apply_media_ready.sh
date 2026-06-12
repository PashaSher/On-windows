#!/usr/bin/env bash
# Патч Pi: _peer_media_ready учитывает исходящие RTP-пакеты (aiortc ice=checking при TURN).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
FILE="${PI_WEBRTC_HOST:-/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py}"

ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path
p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = p.read_text()
marker = "min_packets: int = 30"
if marker in text:
    print("already patched")
    raise SystemExit(0)
old_fn = '''def _peer_media_ready(pc: RTCPeerConnection) -> bool:
    """Медиа уже идёт, хотя connectionState в aiortc может оставаться «connecting»."""
    if pc.connectionState == "connected":
        return True
    return pc.iceConnectionState in ("connected", "completed")'''
new_fn = '''def _peer_media_ready(
    pc: RTCPeerConnection,
    video_track: object | None = None,
    *,
    min_packets: int = 30,
) -> bool:
    """Медиа уже идёт; aiortc на Pi часто остаётся ice=checking при рабочем RTP через TURN."""
    if pc.connectionState == "connected":
        return True
    if pc.iceConnectionState in ("connected", "completed"):
        return True
    if video_track is not None:
        n = int(getattr(video_track, "_packets_total", 0) or 0)
        if n >= min_packets:
            return True
    return False'''
if old_fn not in text:
    raise SystemExit("webrtc_host.py: expected function not found")
text = text.replace(old_fn, new_fn)
text = text.replace("_peer_media_ready(self._pc)", "_peer_media_ready(self._pc, self._video_track)")
text = text.replace("_peer_media_ready(pc)", "_peer_media_ready(pc, self._video_track)")
old_log = '''                log.info(
                    "WebRTC: media path up (connection=%s ice=%s)",
                    pc.connectionState,
                    pc.iceConnectionState,
                )'''
new_log = '''                pkts = int(getattr(self._video_track, "_packets_total", 0) or 0)
                log.info(
                    "WebRTC: media path up (connection=%s ice=%s packets=%d)",
                    pc.connectionState,
                    pc.iceConnectionState,
                    pkts,
                )'''
text = text.replace(old_log, new_log)
old_to = '''                log.warning(
                    "WebRTC: connect timeout (60s, connection=%s ice=%s) — ending session",
                    pc.connectionState,
                    pc.iceConnectionState,
                )'''
new_to = '''                pkts = int(getattr(self._video_track, "_packets_total", 0) or 0)
                log.warning(
                    "WebRTC: connect timeout (60s, connection=%s ice=%s packets=%d) — ending session",
                    pc.connectionState,
                    pc.iceConnectionState,
                    pkts,
                )'''
text = text.replace(old_to, new_to)
p.write_text(text)
print("patched", p)
PY

ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service"
echo "camstream restarted"
