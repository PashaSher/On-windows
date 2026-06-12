#!/usr/bin/env python3
"""Мониторинг WebRTC-сессии 3+ мин: ICE, FPS, audio, обрывы."""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import urllib.request

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcconfiguration import RTCIceServer, RTCConfiguration
from aiortc.sdp import candidate_from_sdp

VPS = "http://116.203.148.254"
ROOM = "pi-camera"
HOLD_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 180
TOKEN = ""


def fetch_json(url: str, *, method: str = "GET", body: dict | None = None, token: str = "", clear: str = "") -> dict:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if clear:
        headers["X-Clear"] = clear
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=35) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def load_token() -> str:
    boot = fetch_json(f"{VPS}/api/operator-bootstrap")
    return str(boot.get("iceConfigToken") or "").strip()


def ice_config(token: str) -> RTCConfiguration:
    ice = fetch_json(f"{VPS}/api/ice", token=token)
    servers = []
    for ent in ice.get("iceServers") or []:
        urls = ent.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not any("turn" in str(u).lower() for u in urls):
            continue
        servers.append(
            RTCIceServer(urls=urls, username=ent.get("username"), credential=ent.get("credential"))
        )
    return RTCConfiguration(iceServers=servers)


class Metrics:
    def __init__(self) -> None:
        self.frames = 0
        self.audio_frames = 0
        self._last_v = 0
        self._last_a = 0
        self._last_ts = 0.0
        self.fps_samples: list[float] = []
        self.ice_states: list[str] = []
        self.drops = 0

    def on_video(self) -> None:
        self.frames += 1

    def on_audio(self) -> None:
        self.audio_frames += 1

    def sample_fps(self) -> float | None:
        now = time.time()
        if self._last_ts <= 0:
            self._last_ts = now
            self._last_v = self.frames
            return None
        dt = now - self._last_ts
        if dt < 1.0:
            return None
        fps = (self.frames - self._last_v) / dt
        self._last_ts = now
        self._last_v = self.frames
        if fps > 0:
            self.fps_samples.append(fps)
        return fps


async def apply_remote(pc: RTCPeerConnection, raw: dict, seen: set[str]) -> None:
    line = str(raw.get("candidate") or "")
    if not line or line[:80] in seen:
        return
    try:
        c = candidate_from_sdp(line)
        c.sdpMid = raw.get("sdpMid")
        c.sdpMLineIndex = raw.get("sdpMLineIndex")
        await pc.addIceCandidate(c)
        seen.add(line[:80])
    except Exception as ex:
        print(f"  WARN cand: {ex}")


async def main() -> int:
    global TOKEN
    TOKEN = load_token()
    base = f"{VPS}/api/signal/rooms/{ROOM}"
    for side in ("caller", "callee"):
        fetch_json(base, method="DELETE", token=TOKEN, clear=side)

    print(f"=== session monitor {HOLD_SEC}s (audio+video) ===\n")
    cfg = ice_config(TOKEN)
    pc = RTCPeerConnection(configuration=cfg)
    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")
    m = Metrics()
    t0 = time.time()
    connected_at: float | None = None
    seen: set[str] = set()

    @pc.on("iceconnectionstatechange")
    async def on_ice() -> None:
        st = pc.iceConnectionState
        m.ice_states.append(st)
        print(f"  +{time.time()-t0:6.1f}s  ICE → {st}")

    @pc.on("connectionstatechange")
    async def on_cs() -> None:
        print(f"  +{time.time()-t0:6.1f}s  connection → {pc.connectionState}")

    @pc.on("track")
    async def on_track(track) -> None:
        print(f"  +{time.time()-t0:6.1f}s  track {track.kind}")
        if track.kind == "video":
            asyncio.create_task(_consume(track, m.on_video))
        elif track.kind == "audio":
            asyncio.create_task(_consume(track, m.on_audio))

    @pc.on("icecandidate")
    async def on_cand(ev) -> None:
        if ev.candidate:
            fetch_json(f"{base}/caller-candidates", method="POST", body=ev.candidate.toJSON(), token=TOKEN)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await asyncio.sleep(2.5)
    fetch_json(f"{base}/offer", method="PUT", body={"type": offer.type, "sdp": pc.localDescription.sdp}, token=TOKEN)
    print(f"OK offer +{time.time()-t0:.1f}s")

    answer = None
    since = 0
    for i in range(90):
        snap = fetch_json(base, token=TOKEN)
        if snap.get("answer"):
            answer = snap["answer"]
            break
        ev = fetch_json(f"{base}/events?since={since}&timeout=2", token=TOKEN)
        since = int(ev.get("seq") or since)
        if ev.get("answer"):
            answer = ev["answer"]
            break
        if i % 10 == 0:
            h = (ev.get("host") or snap.get("host") or {})
            print(f"  wait {time.time()-t0:.1f}s host={h.get('status')} ps={h.get('powerSave')}")
        await asyncio.sleep(0.5)
    if not answer:
        print("FAIL no answer")
        await pc.close()
        return 1

    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
    print(f"OK answer +{time.time()-t0:.1f}s has_audio={'m=audio' in answer['sdp']}")

    ice_ok = False
    for _ in range(60):
        ev = fetch_json(f"{base}/events?since={since}&timeout=1", token=TOKEN)
        since = int(ev.get("seq") or since)
        for raw in (ev.get("calleeCandidates") or {}).values():
            await apply_remote(pc, raw, seen)
        if pc.iceConnectionState in ("connected", "completed"):
            ice_ok = True
            connected_at = time.time()
            break
        if pc.iceConnectionState in ("failed", "closed"):
            break
        await asyncio.sleep(0.5)

    if not ice_ok:
        print(f"FAIL ICE {pc.iceConnectionState}")
        await pc.close()
        return 1

    print(f"OK ICE +{time.time()-t0:.1f}s — hold {HOLD_SEC}s\n")

    last_log = time.time()
    hold_start = time.time()
    last_ice = pc.iceConnectionState
    last_conn = pc.connectionState

    while time.time() - hold_start < HOLD_SEC:
        if pc.connectionState in ("failed", "closed") or pc.iceConnectionState in ("failed", "closed"):
            m.drops += 1
            print(f"  +{time.time()-t0:6.1f}s  DROP conn={pc.connectionState} ice={pc.iceConnectionState}")
            break
        if pc.iceConnectionState != last_ice or pc.connectionState != last_conn:
            print(f"  +{time.time()-t0:6.1f}s  state conn={pc.connectionState} ice={pc.iceConnectionState}")
            last_ice = pc.iceConnectionState
            last_conn = pc.connectionState
        ev = fetch_json(f"{base}/events?since={since}&timeout=1", token=TOKEN)
        since = int(ev.get("seq") or since)
        for raw in (ev.get("calleeCandidates") or {}).values():
            await apply_remote(pc, raw, seen)
        now = time.time()
        if now - last_log >= 15:
            fps = m.sample_fps()
            h = ev.get("host") or {}
            fps_s = f"{fps:.1f}" if fps else "?"
            print(
                f"  +{now-t0:6.1f}s  [tick] fps={fps_s} v_frames={m.frames} a_frames={m.audio_frames} "
                f"ice={pc.iceConnectionState} host={h.get('status')} ps={h.get('powerSave')}"
            )
            last_log = now
        await asyncio.sleep(1.0)

    await pc.close()
    dur = time.time() - t0
    hold = time.time() - hold_start if connected_at else 0

    print("\n=== Summary ===")
    print(f"  Duration:     {dur:.1f}s")
    print(f"  Hold:         {hold:.1f}s / {HOLD_SEC}s")
    print(f"  Drops:        {m.drops}")
    print(f"  Video frames: {m.frames}")
    print(f"  Audio frames: {m.audio_frames}")
    if m.fps_samples:
        print(f"  FPS median:   {statistics.median(m.fps_samples):.1f}")
        print(f"  FPS min/max:  {min(m.fps_samples):.1f} / {max(m.fps_samples):.1f}")
    print(f"  ICE trail:    {' → '.join(m.ice_states[-8:])}")

    ok = m.drops == 0 and hold >= HOLD_SEC * 0.9 and m.frames > 50
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


async def _consume(track, cb) -> None:
    while True:
        try:
            await asyncio.wait_for(track.recv(), timeout=5.0)
            cb()
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
