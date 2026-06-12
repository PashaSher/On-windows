#!/usr/bin/env python3
"""Диагностика WebRTC через VPS: bootstrap, ICE, signaling, симуляция offer."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
TIMEOUT = 35


def fetch_json(url: str, *, method: str = "GET", body: dict | None = None, token: str = "") -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            return resp.status, json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def main() -> int:
    print(f"=== WebRTC VPS connect test ({VPS}) ===\n")

    code, boot = fetch_json(f"{VPS}/api/operator-bootstrap")
    if code != 200 or not isinstance(boot, dict):
        print(f"FAIL bootstrap HTTP {code}: {boot}")
        return 1
    token = str(boot.get("iceConfigToken") or "").strip()
    room = str(boot.get("room") or ROOM).strip() or ROOM
    signal_base = str(boot.get("signalApiBase") or "/api/signal").strip()
    if signal_base.startswith("/"):
        signal_base = VPS + signal_base
    print(f"OK  bootstrap room={room} token={'set' if token else 'MISSING'}")

    code, ice = fetch_json(f"{VPS}/api/ice")
    print(f"{'WARN' if code == 401 else 'FAIL'} ICE without token: HTTP {code}")
    code, ice = fetch_json(f"{VPS}/api/ice", token=token)
    if code != 200:
        print(f"FAIL ICE with token HTTP {code}: {ice}")
        return 1
    servers = ice.get("iceServers") if isinstance(ice, dict) else []
    turn = sum(1 for s in servers if "turn" in str(s.get("urls", "")).lower())
    print(f"OK  ICE entries={len(servers)} turn={turn}")

    snap_url = f"{signal_base}/rooms/{room}"
    code, snap = fetch_json(snap_url)
    if code != 200 or not isinstance(snap, dict):
        print(f"FAIL signal snapshot HTTP {code}: {snap}")
        return 1
    host = snap.get("host") or {}
    print(
        f"    host status={host.get('status')} needOffer={host.get('needOffer')} "
        f"launchId={host.get('hostLaunchId')} session={host.get('hostSessionId')}"
    )

    issues: list[str] = []
    if host.get("status") == "idle" and not host.get("needOffer"):
        issues.append("Pi WebRTC не активен (host idle, needOffer=false) — на Pi: systemctl restart webrtc-vps")
    if not token:
        issues.append("ICE token пуст в bootstrap — оператор не подключится к TURN")

    # Симуляция Connect: очистка caller + offer
    headers_clear = {"Authorization": f"Bearer {token}", "X-Clear": "caller", "Content-Type": "application/json"}
    req = urllib.request.Request(snap_url, method="DELETE", headers=headers_clear)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        pass
    print("OK  cleared caller side")

    fake_offer = {
        "type": "offer",
        "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=ice-ufrag:testufrag\r\na=ice-pwd:testpwd\r\n",
    }
    code, _ = fetch_json(f"{snap_url}/offer", method="PUT", body=fake_offer, token=token)
    if code != 200:
        print(f"FAIL put offer HTTP {code}")
        return 1
    print("OK  wrote test offer")

    print(f"    waiting up to 20s for Pi answer…")
    deadline = time.time() + 20
    answer = None
    since = 0
    while time.time() < deadline:
        ev_url = f"{snap_url}/events?since={since}&timeout=5"
        code, ev = fetch_json(ev_url)
        if code != 200 or not isinstance(ev, dict):
            time.sleep(1)
            continue
        since = int(ev.get("seq") or since)
        if ev.get("answer"):
            answer = ev["answer"]
            break
        h = ev.get("host") or {}
        if h.get("status") not in (None, "idle"):
            print(f"    host update: status={h.get('status')} needOffer={h.get('needOffer')}")
        time.sleep(0.5)

    if answer:
        print("OK  Pi answered (webrtc-vps.service работает)")
    else:
        issues.append("Pi не прислал answer за 20 с — webrtc-vps не запущен или неверный ICE token на Pi")
        print("FAIL no answer from Pi within 20s")

    # cleanup test offer
    req = urllib.request.Request(snap_url, method="DELETE", headers=headers_clear)
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError:
        pass

    print("\n=== Summary ===")
    if issues:
        for i, msg in enumerate(issues, 1):
            print(f"{i}. {msg}")
        return 2
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
