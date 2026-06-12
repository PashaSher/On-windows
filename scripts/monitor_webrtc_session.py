#!/usr/bin/env python3
"""Мониторинг WebRTC-сессии на VPS: host status, offer/answer, powerSave цикл."""
from __future__ import annotations

import json
import sys
import time
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def fetch(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    boot = fetch(f"{VPS}/api/operator-bootstrap", "")
    token = str(boot.get("iceConfigToken") or "").strip()
    if not token:
        print("FAIL: no ICE token in bootstrap")
        return 1
    base = f"{VPS}/api/signal/rooms/{ROOM}"
    print(f"=== monitor {DURATION}s · {base} ===\n")

    since = 0
    t0 = time.time()
    states: list[str] = []
    last = {}

    while time.time() - t0 < DURATION:
        ev = fetch(f"{base}/events?since={since}&timeout=5", token)
        since = int(ev.get("seq", since))
        h = ev.get("host") or {}
        key = (
            f"status={h.get('status')} needOffer={h.get('needOffer')} "
            f"powerSave={h.get('powerSave')} session={h.get('hostSessionId')}"
        )
        if key != last.get("key"):
            dt = time.time() - t0
            print(f"+{dt:5.1f}s  {key}")
            if ev.get("offer"):
                print(f"         offer present")
            if ev.get("answer"):
                print(f"         answer present")
            states.append(h.get("status") or "?")
            last["key"] = key
        time.sleep(0.2)

    waking = states.count("waking")
    idle = states.count("idle")
    waiting = states.count("waiting")
    negotiating = states.count("negotiating")
    connected = states.count("connected")

    print("\n=== diagnosis ===")
    if waiting == 0 and negotiating == 0 and connected == 0:
        print("CRITICAL: Pi never in waiting/negotiating/connected — webrtc-vps NOT serving offers")
        print("  → on Pi: sudo systemctl restart webrtc-vps && journalctl -u webrtc-vps -n 50")
    if waking > 3 and idle > 3:
        print(f"CRITICAL: powerSave loop (waking={waking} idle={idle}) — Pi wakes but does not answer")
        print("  → camstream/powerSave fights WebRTC; disable sleep during stream or fix webrtc-vps")
    snap = fetch(base, token)
    if snap.get("offer") and not snap.get("answer"):
        print("WARN: offer without answer — stale or Pi not responding")
    if snap.get("answer") and not snap.get("offer"):
        print("WARN: answer without offer — browser gets Failed (deploy clear-stale-answer patch)")

    html = urllib.request.urlopen(f"{VPS}/webrtc-client.html", timeout=10).read().decode("utf-8", "replace")
    if "clear-stale-answer" in html:
        print("OK: browser client patched (clear-stale-answer)")
    else:
        print("WARN: VPS still serves OLD webrtc-client.html — deploy deploy/vps/webrtc-client_live_patched.html")

    return 2 if waiting == 0 and negotiating == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
