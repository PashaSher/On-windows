#!/usr/bin/env python3
"""Тест подключения как браузер: Playwright + WebRTC operator page."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
PAGE = f"{VPS}/webrtc-client.html?room={ROOM}&tcpTurn=1"
HOLD_SEC = 45


def fetch_json(url: str, *, method: str = "GET", token: str = "") -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def load_token() -> str:
    boot = fetch_json(f"{VPS}/api/operator-bootstrap")
    return str(boot.get("iceConfigToken") or "").strip()


def clear_room(token: str) -> None:
    base = f"{VPS}/api/signal/rooms/{ROOM}"
    for hdr in ("caller", "callee"):
        req = urllib.request.Request(
            base,
            method="DELETE",
            headers={"Authorization": f"Bearer {token}", "X-Clear": hdr},
        )
        urllib.request.urlopen(req, timeout=15)


def room_snapshot(token: str) -> dict:
    return fetch_json(f"{VPS}/api/signal/rooms/{ROOM}", token=token)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        from playwright.sync_api import sync_playwright

    token = load_token()
    if not token:
        print("FAIL: no ice token")
        return 1

    clear_room(token)
    print(f"Page: {PAGE}")

    logs: list[str] = []
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def on_console(msg):
            text = msg.text
            if "[webrtc]" in text or "Operator build" in text or "ICE" in text:
                logs.append(text)
                print(f"  [console] {text[:200]}")

        page.on("console", on_console)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)

        build_ok = any("tcp-turn-3" in l for l in logs)
        print(f"Build tcp-turn-3: {'OK' if build_ok else 'MISSING (cache?)'}")

        btn = page.locator("#btnConnect")
        if btn.is_visible() and btn.is_enabled():
            btn.click()
            print("Clicked Connect")
        else:
            print("Autostart or already connecting — ждём без клика")

        deadline = time.time() + 120
        connected = False
        ice_failed = False
        frames = 0
        while time.time() < deadline:
            snap = room_snapshot(token)
            if snap.get("answer"):
                print("OK  Pi answer received")
            status_el = page.locator("#statusText")
            try:
                status = status_el.inner_text(timeout=500)
            except Exception:
                status = ""
            if "Connected" in status or any("WebRTC connected" in l for l in logs):
                connected = True
                print(f"OK  Browser status: {status or 'connected (log)'}")
                break
            if "Failed" in status or "error" in status.lower():
                ice_failed = True
            for l in logs[-8:]:
                if "ICE failed" in l:
                    ice_failed = True
            time.sleep(2)

        if connected:
            print(f"Holding {HOLD_SEC}s...")
            t0 = time.time()
            while time.time() - t0 < HOLD_SEC:
                try:
                    v = page.evaluate(
                        """() => {
                        const v = document.getElementById('remoteVideo');
                        if (!v || !v.srcObject) return {ready:0, w:0, h:0};
                        const t = v.srcObject.getVideoTracks()[0];
                        return {ready: v.readyState, w: v.videoWidth, h: v.videoHeight, muted: t?.muted};
                    }"""
                    )
                    if v.get("w", 0) > 0:
                        frames += 1
                except Exception:
                    pass
                time.sleep(3)
            print(f"Video frames with size>0: {frames}/{HOLD_SEC // 3} checks")

        snap = room_snapshot(token)
        callers = snap.get("callerCandidates") or {}
        udp_rel = tcp_rel = 0
        for raw in callers.values():
            line = str(raw.get("candidate") or "")
            if "typ relay" not in line:
                continue
            if " tcp " in line:
                tcp_rel += 1
            elif " udp " in line:
                udp_rel += 1
        print(f"Caller ICE: tcp_relay={tcp_rel} udp_relay={udp_rel}")

        browser.close()

    if errors:
        print("Page errors:", errors[:3])

    if connected and frames > 2:
        print(f"RESULT: PASS — browser connected (tcp={tcp_rel} udp={udp_rel})")
        return 0
    if connected:
        print(f"RESULT: PARTIAL — connected, low video (tcp={tcp_rel} udp={udp_rel})")
        return 0
    if ice_failed:
        print("RESULT: FAIL — ICE failed in browser")
        return 1
    print("RESULT: FAIL — no connection within 90s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
