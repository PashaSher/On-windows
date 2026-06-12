#!/usr/bin/env python3
"""Тест FPS и задержки DC: Playwright, без аудио."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
PAGE = f"{VPS}/webrtc-client.html?room={ROOM}&tcpTurn=1"
HOLD_SEC = 35


def fetch_json(url: str, *, method: str = "GET", token: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def clear_room(token: str) -> None:
    base = f"{VPS}/api/signal/rooms/{ROOM}"
    for hdr in ("caller", "callee"):
        req = urllib.request.Request(
            base,
            method="DELETE",
            headers={"Authorization": f"Bearer {token}", "X-Clear": hdr},
        )
        urllib.request.urlopen(req, timeout=15)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q", "--break-system-packages"])
        from playwright.sync_api import sync_playwright

    boot = fetch_json(f"{VPS}/api/operator-bootstrap")
    token = str(boot.get("iceConfigToken") or "").strip()
    clear_room(token)

    fps_samples: list[float] = []
    dc_rtts: list[float] = []
    logs: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        page.on("console", lambda m: logs.append(m.text))
        page.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        chk = page.locator("#chkPiAudio")
        if chk.is_visible():
            chk.uncheck()

        btn = page.locator("#btnConnect")
        if btn.is_enabled():
            btn.click()
            print("Clicked Connect (audio off)")

        deadline = time.time() + 120
        connected = False
        while time.time() < deadline:
            if any("WebRTC connected" in l for l in logs):
                connected = True
                break
            try:
                st = page.locator("#status").inner_text(timeout=500)
                if "Connected" in st:
                    connected = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not connected:
            print("FAIL: no connection within 120s")
            for l in logs[-15:]:
                print(" ", l[:160])
            browser.close()
            return 1

        print("Connected — measuring FPS + DC RTT…")
        t0 = time.time()
        while time.time() - t0 < HOLD_SEC:
            for l in logs:
                m = re.search(r"fps=([\d.]+)", l)
                if m:
                    fps_samples.append(float(m.group(1)))
            rtt = page.evaluate(
                """async () => {
                const dc = window.__romeoDc;
                if (!dc || dc.readyState !== 'open') return null;
                const t0 = performance.now();
                return new Promise((resolve) => {
                    const onMsg = () => {
                        dc.removeEventListener('message', onMsg);
                        resolve(performance.now() - t0);
                    };
                    dc.addEventListener('message', onMsg);
                    dc.send(JSON.stringify({action:'camera_status'}));
                    setTimeout(() => { dc.removeEventListener('message', onMsg); resolve(null); }, 2000);
                });
            }"""
            )
            if rtt and rtt > 0:
                dc_rtts.append(float(rtt))
            time.sleep(4)

        browser.close()

    fps_ok = [f for f in fps_samples if f > 0]
    med_fps = sorted(fps_ok)[len(fps_ok) // 2] if fps_ok else 0
    med_rtt = sorted(dc_rtts)[len(dc_rtts) // 2] if dc_rtts else None
    audio_off = any("Audio: выкл" in l for l in logs)
    print(f"Audio off in offer: {audio_off}")
    print(f"FPS samples: {len(fps_ok)}, median={med_fps:.1f}, min={min(fps_ok) if fps_ok else 0:.1f}, max={max(fps_ok) if fps_ok else 0:.1f}")
    print(f"DC RTT samples: {len(dc_rtts)}, median={med_rtt:.0f}ms" if med_rtt else "DC RTT: no samples")

    if med_fps >= 8 and (med_rtt is None or med_rtt < 800):
        print("RESULT: PASS")
        return 0
    print("RESULT: FAIL (low FPS or high DC latency)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
