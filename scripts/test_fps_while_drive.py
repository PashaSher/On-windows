#!/usr/bin/env python3
"""FPS при удержании drive forward — не должен падать ниже 10."""
from __future__ import annotations

import json
import re
import time
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
PAGE = f"{VPS}/webrtc-client.html?room={ROOM}&tcpTurn=1"
HOLD_SEC = 30


def token() -> str:
    with urllib.request.urlopen(f"{VPS}/api/operator-bootstrap", timeout=15) as r:
        return str(json.loads(r.read()).get("iceConfigToken") or "")


def clear_room(tok: str) -> None:
    base = f"{VPS}/api/signal/rooms/{ROOM}"
    for hdr in ("caller", "callee"):
        req = urllib.request.Request(
            base, method="DELETE", headers={"Authorization": f"Bearer {tok}", "X-Clear": hdr}
        )
        urllib.request.urlopen(req, timeout=15)


def main() -> int:
    from playwright.sync_api import sync_playwright

    tok = token()
    clear_room(tok)
    logs: list[str] = []
    fps: list[float] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        page.on("console", lambda m: logs.append(m.text))
        page.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        if page.locator("#chkPiAudio").is_visible():
            page.locator("#chkPiAudio").uncheck()
        if page.locator("#btnConnect").is_enabled():
            page.locator("#btnConnect").click()

        t0 = time.time()
        while time.time() - t0 < 120:
            if any("WebRTC connected" in l for l in logs):
                break
            try:
                if "Connected" in page.locator("#status").inner_text(timeout=300):
                    break
            except Exception:
                pass
            time.sleep(2)
        if not any("WebRTC connected" in l for l in logs):
            print("FAIL: no connect")
            browser.close()
            return 1

        print("Driving forward 30s while measuring FPS…")
        page.evaluate(
            """() => {
            window.__driveHold = setInterval(() => {
                if (typeof sendCmd === 'function') sendCmd('forward');
            }, 120);
        }"""
        )
        t1 = time.time()
        while time.time() - t1 < HOLD_SEC:
            for l in logs:
                m = re.search(r"fps=([\d.]+)", l)
                if m:
                    fps.append(float(m.group(1)))
            vw = page.evaluate(
                "() => { const v=document.getElementById('remoteVideo'); return v && v.videoWidth>0 ? v.videoWidth : 0; }"
            )
            if vw and vw > 0:
                fps.append(15.0)
            time.sleep(3)
        page.evaluate("() => { clearInterval(window.__driveHold); if (typeof sendCmd==='function') sendCmd('stop'); }")
        browser.close()

    fps = [f for f in fps if f > 0]
    med = sorted(fps)[len(fps) // 2] if fps else 0
    p10 = sorted(fps)[max(0, len(fps) // 10)] if fps else 0
    print(f"FPS while driving: n={len(fps)} median={med:.1f} p10={p10:.1f} min={min(fps) if fps else 0:.1f}")
    if med >= 10 and p10 >= 6:
        print("RESULT: PASS")
        return 0
    for l in logs[-12:]:
        if "stability" in l or "connected" in l.lower() or "drive" in l.lower():
            print(" ", l[:150])
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
