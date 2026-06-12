#!/usr/bin/env python3
"""Real WebRTC ICE test: VPS acts as browser caller → Pi answer via TURN."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCIceServer, RTCConfiguration

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
TOKEN = ""


def fetch_json(url: str, *, method: str = "GET", body: dict | None = None, token: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def load_token() -> str:
    boot = fetch_json(f"{VPS}/api/operator-bootstrap")
    return str(boot.get("iceConfigToken") or "").strip()


def ice_servers(token: str) -> RTCConfiguration:
    ice = fetch_json(f"{VPS}/api/ice", token=token)
    servers = []
    for ent in ice.get("iceServers") or []:
        urls = ent.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        servers.append(
            RTCIceServer(
                urls=urls,
                username=ent.get("username"),
                credential=ent.get("credential"),
            )
        )
    return RTCConfiguration(iceServers=servers)


async def main() -> int:
    global TOKEN
    TOKEN = load_token()
    if not TOKEN:
        print("FAIL no ICE token")
        return 1

    base = f"{VPS}/api/signal/rooms/{ROOM}"
    req = urllib.request.Request(
        base,
        method="DELETE",
        headers={"Authorization": f"Bearer {TOKEN}", "X-Clear": "caller"},
    )
    urllib.request.urlopen(req, timeout=15)

    cfg = ice_servers(TOKEN)
    pc = RTCPeerConnection(configuration=cfg)
    pc.addTransceiver("video", direction="recvonly")

    ice_log: list[str] = []

    @pc.on("iceconnectionstatechange")
    async def on_ice() -> None:
        ice_log.append(pc.iceConnectionState)
        print(f"  ICE state: {pc.iceConnectionState}")

    @pc.on("connectionstatechange")
    async def on_conn() -> None:
        print(f"  connection state: {pc.connectionState}")

    @pc.on("icecandidate")
    async def on_cand(event) -> None:
        if event.candidate:
            fetch_json(
                f"{base}/callerCandidates",
                method="POST",
                body=event.candidate.toJSON(),
                token=TOKEN,
            )

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await asyncio.sleep(3.0)

    fetch_json(
        f"{base}/offer",
        method="PUT",
        body={"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
        token=TOKEN,
    )
    print("OK  offer sent, waiting for Pi answer…")

    answer = None
    since = 0
    deadline = time.time() + 25
    while time.time() < deadline and not answer:
        ev = fetch_json(f"{base}/events?since={since}&timeout=5")
        since = int(ev.get("seq") or since)
        if ev.get("answer"):
            answer = ev["answer"]
            break
        await asyncio.sleep(0.3)

    if not answer:
        print("FAIL no answer from Pi")
        await pc.close()
        return 1

    print("OK  got answer, applying…")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    for _ in range(30):
        ev = fetch_json(f"{base}/events?since={since}&timeout=1")
        since = int(ev.get("seq") or since)
        for raw in (ev.get("calleeCandidates") or {}).values():
            from aiortc import RTCIceCandidate

            try:
                await pc.addIceCandidate(RTCIceCandidate(**raw))
            except Exception as ex:
                print(f"  addIceCandidate: {ex}")
        if pc.iceConnectionState in ("connected", "completed"):
            print("SUCCESS ICE connected!")
            await pc.close()
            return 0
        if pc.iceConnectionState in ("failed", "closed"):
            print(f"FAIL ICE {pc.iceConnectionState}")
            await pc.close()
            return 1
        await asyncio.sleep(1.0)

    print(f"FAIL ICE timeout (state={pc.iceConnectionState}, log={ice_log})")
    await pc.close()
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
