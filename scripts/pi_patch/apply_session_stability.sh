#!/usr/bin/env bash
# Pi: не рвать сессию на кратковременный ice=failed/disconnected, если RTP идёт.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
FILE="/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py"

ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path
p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = p.read_text()
changed = False

if "_ICE_DISCONNECT_GRACE_SEC = 4.0" in text:
    text = text.replace("_ICE_DISCONNECT_GRACE_SEC = 4.0", "_ICE_DISCONNECT_GRACE_SEC = 20.0")
    changed = True
elif "_ICE_DISCONNECT_GRACE_SEC = 20.0" not in text:
    raise SystemExit("grace constant not found")

old_conn = '''        @self._pc.on("connectionstatechange")
        async def on_connection_state_change():
            if not self._pc:
                return
            state = self._pc.connectionState
            ice = self._pc.iceConnectionState
            log.info("WebRTC: connection state -> %s (ice=%s)", state, ice)
            if state == "connected":
                await self._signaling.set_status("connected")
            elif state in ("failed", "closed", "disconnected"):
                self._request_session_end(f"connection={state} (ice={ice})")

        @self._pc.on("iceconnectionstatechange")
        async def on_ice_state():
            if not self._pc:
                return
            ice = self._pc.iceConnectionState
            log.info("WebRTC: ICE state -> %s", ice)
            if ice in ("failed", "closed"):
                self._request_session_end(f"ice={ice}")'''

new_conn = '''        @self._pc.on("connectionstatechange")
        async def on_connection_state_change():
            if not self._pc:
                return
            state = self._pc.connectionState
            ice = self._pc.iceConnectionState
            pkts = int(getattr(self._video_track, "_packets_total", 0) or 0)
            log.info(
                "WebRTC: connection state -> %s (ice=%s packets=%d)",
                state,
                ice,
                pkts,
            )
            if state == "connected":
                await self._signaling.set_status("connected")
            elif state == "closed":
                self._request_session_end(f"connection={state} (ice={ice})")

        @self._pc.on("iceconnectionstatechange")
        async def on_ice_state():
            if not self._pc:
                return
            ice = self._pc.iceConnectionState
            pkts = int(getattr(self._video_track, "_packets_total", 0) or 0)
            log.info("WebRTC: ICE state -> %s (packets=%d)", ice, pkts)
            if ice == "closed":
                self._request_session_end(f"ice={ice}")'''

if old_conn in text:
    text = text.replace(old_conn, new_conn)
    changed = True
elif "WebRTC: ICE state -> %s (packets=%d)" in text:
    pass
else:
    raise SystemExit("connection/ice handlers block not found")

old_await = '''        while self._running and self._pc and not self._session_end.is_set():
            pc = self._pc
            cs = pc.connectionState
            ice = pc.iceConnectionState
            if cs in ("failed", "closed") or ice in ("failed", "closed"):
                self._request_session_end(f"poll {cs}/{ice}")
                break
            if cs == "disconnected" or ice == "disconnected":
                now = time.monotonic()
                if self._disconnect_since is None:
                    self._disconnect_since = now
                elif now - self._disconnect_since >= _ICE_DISCONNECT_GRACE_SEC:
                    self._request_session_end(
                        f"disconnected >{_ICE_DISCONNECT_GRACE_SEC:.0f}s ({cs}/{ice})"
                    )
                    break
            else:
                self._disconnect_since = None
            try:
                await asyncio.wait_for(self._session_end.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass'''

new_await = '''        last_stability_log = time.monotonic()
        while self._running and self._pc and not self._session_end.is_set():
            pc = self._pc
            cs = pc.connectionState
            ice = pc.iceConnectionState
            pkts = int(getattr(self._video_track, "_packets_total", 0) or 0)
            media_up = _peer_media_ready(pc, self._video_track)
            now = time.monotonic()
            if now - last_stability_log >= 30.0:
                last_stability_log = now
                log.info(
                    "WebRTC: stability tick connection=%s ice=%s packets=%d media_up=%s",
                    cs,
                    ice,
                    pkts,
                    media_up,
                )
            if cs == "closed" or ice == "closed":
                self._request_session_end(f"poll {cs}/{ice}")
                break
            if cs in ("failed", "disconnected") or ice in ("failed", "disconnected"):
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
                        break
            else:
                self._disconnect_since = None
            try:
                await asyncio.wait_for(self._session_end.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass'''

if old_await in text:
    text = text.replace(old_await, new_await)
    changed = True
elif "WebRTC: stability tick" in text:
    pass
else:
    raise SystemExit("_await_session_end loop not found")

if changed:
    p.write_text(text)
    print("patched", p)
else:
    print("already patched", p)
PY

ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service"
echo "camstream restarted"
