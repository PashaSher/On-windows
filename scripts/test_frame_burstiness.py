#!/usr/bin/env python3
"""Измеряет рваность видео: decoded FPS vs painted FPS, packet loss."""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
PAGE = f"{VPS}/webrtc-client.html?room={ROOM}&tcpTurn=1"
HOLD_SEC = 40


def fetch_json(url: str, *, method: str = "GET", token: str = "", clear: str = "") -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if clear:
        headers["X-Clear"] = clear
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

    logs: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        page.on("console", lambda m: logs.append(m.text))
        page.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        page.locator("#btnConnect").click()

        deadline = time.time() + 120
        while time.time() < deadline:
            if any("WebRTC connected" in l or "video.play() OK" in l for l in logs):
                break
            time.sleep(2)
        else:
            print("FAIL: no connection")
            browser.close()
            return 1

        print("Connected — measuring burstiness…")
        time.sleep(3)
        metrics = page.evaluate(
            f"""async () => {{
            const pc = window.__romeoPc;
            const video = document.getElementById('remoteVideo');
            if (!pc || !video) return {{error: 'no pc/video'}};

            const paintDeltas = [];
            let lastPaint = 0;
            let frames = 0;
            const paintPromise = new Promise((resolve) => {{
                const cb = () => {{
                    const now = performance.now();
                    if (lastPaint > 0) paintDeltas.push(now - lastPaint);
                    lastPaint = now;
                    frames += 1;
                    if (frames < 80) video.requestVideoFrameCallback(cb);
                    else resolve();
                }};
                if (typeof video.requestVideoFrameCallback === 'function') cb();
                else resolve();
            }});
            await Promise.race([paintPromise, new Promise(r => setTimeout(r, {HOLD_SEC * 1000}))]);

            const stats = await pc.getStats();
            let inbound = null;
            stats.forEach(r => {{ if (r.type === 'inbound-rtp' && r.kind === 'video') inbound = r; }});

            const sorted = [...paintDeltas].sort((a,b) => a-b);
            const med = sorted[Math.floor(sorted.length/2)] || 0;
            const p90 = sorted[Math.floor(sorted.length*0.9)] || 0;
            const max = sorted[sorted.length-1] || 0;
            const burst = paintDeltas.filter(d => d > 200).length;

            return {{
                paintSamples: paintDeltas.length,
                paintMedianMs: med,
                paintP90Ms: p90,
                paintMaxMs: max,
                paintBurstsOver200ms: burst,
                framesDecoded: inbound?.framesDecoded,
                framesPerSecond: inbound?.framesPerSecond,
                packetsReceived: inbound?.packetsReceived,
                packetsLost: inbound?.packetsLost,
                jitterMs: inbound?.jitter != null ? inbound.jitter * 1000 : null,
                bytesReceived: inbound?.bytesReceived,
            }};
        }}"""
        )
        browser.close()

    print(json.dumps(metrics, indent=2))
    if metrics.get("error"):
        return 1

    med = metrics.get("paintMedianMs") or 0
    burst = metrics.get("paintBurstsOver200ms") or 0
    lost = metrics.get("packetsLost") or 0
    fps = metrics.get("framesPerSecond") or 0

    slideshow = burst > 5 or med > 120 or (fps > 10 and med > 80)
    print(f"Verdict: {'SLIDESHOW' if slideshow else 'SMOOTH'} (paint med={med:.0f}ms bursts>200ms={burst} lost={lost} decode_fps={fps})")
    return 1 if slideshow else 0


if __name__ == "__main__":
    raise SystemExit(main())
