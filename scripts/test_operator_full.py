#!/usr/bin/env python3
"""Тест: видео + управление + Opus-звук (не лагает WebRTC)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

VPS = os.environ.get("VPS_BASE", "http://116.203.148.254")
ROOM = "pi-camera"
PAGE = os.environ.get(
    "OPERATOR_PAGE",
    f"https://pasta-antarctica-hazardous-seekers.trycloudflare.com/webrtc-client.html?room={ROOM}&autostart=1&share=1&tcpTurn=1",
)
HOLD_SEC = 30


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
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        from playwright.sync_api import sync_playwright

    token = fetch_json(f"{VPS}/api/operator-bootstrap").get("iceConfigToken", "")
    if not token:
        print("FAIL: no token")
        return 1
    clear_room(str(token))
    print(f"Page: {PAGE}")

    logs: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
        )
        page = browser.new_page(ignore_https_errors=True)
        page.on("console", lambda m: logs.append(m.text))
        page.goto(PAGE, wait_until="domcontentloaded", timeout=90000)
        page.click("#btnConnect")
        for _ in range(60):
            if "Connected" in page.locator("#status").inner_text(timeout=1000):
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(HOLD_SEC * 1000)

        stats = page.evaluate(
            """async () => {
            const v = document.getElementById('remoteVideo');
            const a = document.getElementById('remoteAudio');
            let frames = 0;
            if (v?.getVideoPlaybackQuality) frames = v.getVideoPlaybackQuality().totalVideoFrames;
            else if (v?.webkitDecodedFrameCount) frames = v.webkitDecodedFrameCount;
            let pc = null;
            for (const k of Object.keys(window)) {
                const o = window[k];
                if (o && o.getStats && o.iceConnectionState) { pc = o; break; }
            }
            let audioRtp = 0, videoRtp = 0;
            if (pc) {
                const rep = await pc.getStats();
                rep.forEach(s => {
                    if (s.type === 'inbound-rtp' && s.kind === 'audio') audioRtp = s.bytesReceived || 0;
                    if (s.type === 'inbound-rtp' && s.kind === 'video') videoRtp = s.bytesReceived || 0;
                });
            }
            return {
                status: document.getElementById('status')?.textContent || '',
                videoFrames: frames,
                webrtcAudioBytes: audioRtp,
                webrtcVideoBytes: videoRtp,
                audioWsParts: window.__piAudioWsParts || 0,
            };
        }"""
        )
        browser.close()

    ogg_ok = any("PCM stream playing" in l for l in logs)
    connected = "Connected" in stats.get("status", "")
    video_ok = stats.get("videoFrames", 0) > 20
    no_webrtc_audio = stats.get("webrtcAudioBytes", 0) == 0
    ws_parts = int(stats.get("audioWsParts") or 0)
    pcm_ok = ws_parts >= 8

    print("stats:", json.dumps(stats, ensure_ascii=False))
    print(f"video_ok={video_ok} pcm_ok={pcm_ok} ws_parts={ws_parts} no_webrtc_audio={no_webrtc_audio} connected={connected}")
    for l in logs:
        if "audio" in l.lower() or "connected" in l.lower() or "ffmpeg" in l.lower():
            print(" ", l)

    if connected and video_ok and no_webrtc_audio and pcm_ok and ogg_ok:
        print("RESULT: PASS — video+control WebRTC, sound via C PCM WS tunnel")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
