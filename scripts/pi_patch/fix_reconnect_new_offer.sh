#!/usr/bin/env bash
# Pi: при обрыве браузера — завершить старую сессию, если на VPS новый offer.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

http_p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_vps_signaling.py")
host_p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")

ht = http_p.read_text()
if "def fetch_room(self)" not in ht:
    anchor = "    def wait_events(self, timeout: float = 8.0) -> dict[str, Any]:"
    snippet = '''    def fetch_room(self) -> dict[str, Any] | None:
        try:
            return self._request("GET", (), auth=True)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

'''
    ht = ht.replace(anchor, snippet + anchor)
    http_p.write_text(ht)
    print("added fetch_room", http_p)

ht = http_p.read_text()
if "async def peek_new_browser_offer" not in ht:
    anchor = "    async def wait_for_offer("
    snippet = '''    async def peek_new_browser_offer(self, current_ufrag: str | None) -> bool:
        def _go() -> bool:
            snap = self._http.fetch_room() or {}
            offer = self._coerce_offer(snap.get("offer"))
            if not offer:
                return False
            ufrag = self._extract_ufrag(offer.get("sdp", ""))
            if not ufrag or ufrag == (current_ufrag or ""):
                return False
            ok, _ = self._is_plausible_browser_offer(offer.get("sdp", ""))
            return ok

        return bool(await self._run_sync(_go))

'''
    ht = ht.replace(anchor, snippet + anchor)
    http_p.write_text(ht)
    print("added peek_new_browser_offer", http_p)

wt = host_p.read_text()
old = """        last_stability_log = time.monotonic()
        while self._running and self._pc and not self._session_end.is_set():"""

new = """        last_stability_log = time.monotonic()
        last_offer_check = 0.0
        while self._running and self._pc and not self._session_end.is_set():"""

if old in wt and "last_offer_check" not in wt:
    wt = wt.replace(old, new)

insert = """            if (
                self._signaling
                and self._prev_ufrag
                and now - last_offer_check >= 1.5
            ):
                last_offer_check = now
                peek = getattr(self._signaling, "peek_new_browser_offer", None)
                if callable(peek):
                    try:
                        if await peek(self._prev_ufrag):
                            log.info(
                                "WebRTC: новый offer на VPS (ufrag≠%s) — завершаем сессию для reconnect",
                                self._prev_ufrag,
                            )
                            self._request_session_end("browser reconnect offer")
                            break
                    except Exception:
                        log.debug("WebRTC: peek_new_browser_offer", exc_info=True)
"""

anchor = "            if cs == \"closed\" or ice == \"closed\":"
if insert.strip() not in wt and anchor in wt:
    wt = wt.replace(anchor, insert + "\n" + anchor)
    host_p.write_text(wt)
    print("patched _await_session_end", host_p)
elif "peek_new_browser_offer" in wt:
    print("host already patched")
else:
    raise SystemExit("host patch anchor missing")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 2 && systemctl is-active camstream.service"
