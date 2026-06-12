#!/usr/bin/env python3
"""Полная WebRTC-сессия VPS→Pi: ICE, видео, стабильность, задержки."""
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
HOLD_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 90
TOKEN = ""


def fetch_json(url: str, *, method: str = "GET", body: dict | None = None, token: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
            RTCIceServer(
                urls=urls,
                username=ent.get("username"),
                credential=ent.get("credential"),
            )
        )
    return RTCConfiguration(iceServers=servers)


def clear_session(base: str, token: str) -> None:
    for hdr in ("caller", "callee"):
        req = urllib.request.Request(
            base,
            method="DELETE",
            headers={"Authorization": f"Bearer {token}", "X-Clear": hdr},
        )
        urllib.request.urlopen(req, timeout=15)


async def apply_remote_candidate(pc: RTCPeerConnection, raw: dict) -> bool:
    line = str(raw.get("candidate") or "").strip()
    if not line:
        return False
    try:
        c = candidate_from_sdp(line)
        c.sdpMid = raw.get("sdpMid")
        c.sdpMLineIndex = raw.get("sdpMLineIndex")
        await pc.addIceCandidate(c)
        return True
    except Exception as ex:
        print(f"  WARN addIceCandidate: {ex}")
        return False


class FrameStats:
    def __init__(self) -> None:
        self.count = 0
        self.first_ts: float | None = None
        self.intervals: list[float] = []
        self._last: float | None = None
        self.bytes = 0

    def on_frame(self, nbytes: int) -> None:
        now = time.time()
        if self.first_ts is None:
            self.first_ts = now
        if self._last is not None:
            self.intervals.append(now - self._last)
        self._last = now
        self.count += 1
        self.bytes += nbytes


async def consume_track(track, stats: FrameStats, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
        nbytes = 0
        if hasattr(frame, "to_ndarray"):
            try:
                arr = frame.to_ndarray(format="yuv420p")
                nbytes = arr.nbytes
            except Exception:
                nbytes = 1
        stats.on_frame(nbytes)


async def poll_host(base: str, token: str, since: int) -> tuple[int, dict]:
    ev = fetch_json(f"{base}/events?since={since}&timeout=2", token=token)
    seq = int(ev.get("seq") or since)
    return seq, ev.get("host") or {}


async def main() -> int:
    global TOKEN
    TOKEN = load_token()
    if not TOKEN:
        print("FAIL no ICE token")
        return 1

    base = f"{VPS}/api/signal/rooms/{ROOM}"
    print(f"=== WebRTC session stability test ({HOLD_SEC}s hold) ===\n")
    clear_session(base, TOKEN)
    print("OK  room cleared")

    cfg = ice_config(TOKEN)
    pc = RTCPeerConnection(configuration=cfg)
    pc.addTransceiver("video", direction="recvonly")

    t0 = time.time()
    ice_connected_at: float | None = None
    ice_states: list[str] = []
    conn_states: list[str] = []
    frame_stats = FrameStats()
    stop_consume = asyncio.Event()
    consume_task: asyncio.Task | None = None
    applied_cands: set[str] = set()

    @pc.on("iceconnectionstatechange")
    async def on_ice() -> None:
        nonlocal ice_connected_at
        st = pc.iceConnectionState
        ice_states.append(st)
        dt = time.time() - t0
        print(f"  +{dt:5.2f}s  ICE → {st}")
        if st in ("connected", "completed") and ice_connected_at is None:
            ice_connected_at = time.time()

    @pc.on("connectionstatechange")
    async def on_conn() -> None:
        st = pc.connectionState
        conn_states.append(st)
        dt = time.time() - t0
        print(f"  +{dt:5.2f}s  connection → {st}")

    @pc.on("track")
    async def on_track(track) -> None:
        nonlocal consume_task
        if track.kind != "video":
            return
        dt = time.time() - t0
        print(f"  +{dt:5.2f}s  remote video track received")
        consume_task = asyncio.create_task(consume_track(track, frame_stats, stop_consume))

    @pc.on("icecandidate")
    async def on_cand(event) -> None:
        if event.candidate:
            fetch_json(
                f"{base}/caller-candidates",
                method="POST",
                body=event.candidate.toJSON(),
                token=TOKEN,
            )

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await asyncio.sleep(2.5)

    fetch_json(
        f"{base}/offer",
        method="PUT",
        body={"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
        token=TOKEN,
    )
    t_offer = time.time()
    print(f"OK  offer sent (+{t_offer - t0:.2f}s)")

    answer = None
    since = 0
    deadline = time.time() + 30
    while time.time() < deadline and not answer:
        ev = fetch_json(f"{base}/events?since={since}&timeout=5", token=TOKEN)
        since = int(ev.get("seq") or since)
        if ev.get("answer"):
            answer = ev["answer"]
            break
        h = ev.get("host") or {}
        if h.get("status") not in (None, "idle", "waiting"):
            print(f"    host: status={h.get('status')} powerSave={h.get('powerSave')}")

    if not answer:
        print("FAIL no answer from Pi within 30s")
        await pc.close()
        return 1

    t_answer = time.time()
    print(f"OK  answer received (+{t_answer - t0:.2f}s, signaling={t_answer - t_offer:.2f}s)")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    # trickle + wait ICE
    ice_deadline = time.time() + 45
    while time.time() < ice_deadline:
        ev = fetch_json(f"{base}/events?since={since}&timeout=2", token=TOKEN)
        since = int(ev.get("seq") or since)
        for raw in (ev.get("calleeCandidates") or {}).values():
            key = str(raw.get("candidate", ""))[:80]
            if key in applied_cands:
                continue
            if await apply_remote_candidate(pc, raw):
                applied_cands.add(key)
        if pc.iceConnectionState in ("connected", "completed"):
            break
        if pc.iceConnectionState in ("failed", "closed"):
            print(f"FAIL ICE {pc.iceConnectionState}")
            stop_consume.set()
            await pc.close()
            return 1
        await asyncio.sleep(0.5)

    if pc.iceConnectionState not in ("connected", "completed"):
        print(f"FAIL ICE timeout (state={pc.iceConnectionState})")
        stop_consume.set()
        await pc.close()
        return 1

    t_ice = time.time()
    print(f"OK  ICE connected (+{t_ice - t0:.2f}s, ice_after_answer={t_ice - t_answer:.2f}s)")

    # wait first frame
    frame_deadline = time.time() + 20
    while frame_stats.first_ts is None and time.time() < frame_deadline:
        if pc.connectionState in ("failed", "closed"):
            print(f"FAIL connection dropped before first frame ({pc.connectionState})")
            stop_consume.set()
            await pc.close()
            return 1
        await asyncio.sleep(0.2)

    if frame_stats.first_ts is None:
        print("WARN no video frames in 20s (ICE ok but no RTP)")
    else:
        t_frame = frame_stats.first_ts
        print(f"OK  first video frame (+{t_frame - t0:.2f}s, after_ice={t_frame - t_ice:.2f}s)")

    # hold session
    print(f"\n--- holding {HOLD_SEC}s ---")
    host_changes: list[str] = []
    last_host_key = ""
    hold_start = time.time()
    drops = 0

    while time.time() - hold_start < HOLD_SEC:
        if pc.connectionState in ("failed", "closed"):
            drops += 1
            dt = time.time() - t0
            print(f"  +{dt:5.2f}s  DROP connection={pc.connectionState}")
            break
        if pc.iceConnectionState in ("failed", "closed"):
            drops += 1
            dt = time.time() - t0
            print(f"  +{dt:5.2f}s  DROP ice={pc.iceConnectionState}")
            break

        since, h = await poll_host(base, TOKEN, since)
        key = f"{h.get('status')}|{h.get('powerSave')}|{h.get('hostSessionId')}"
        if key != last_host_key:
            dt = time.time() - t0
            print(
                f"  +{dt:5.2f}s  host status={h.get('status')} "
                f"powerSave={h.get('powerSave')} session={h.get('hostSessionId')}"
            )
            host_changes.append(key)
            last_host_key = key

        elapsed = int(time.time() - hold_start)
        if elapsed > 0 and elapsed % 15 == 0 and frame_stats.count > 0:
            fps = frame_stats.count / (time.time() - (frame_stats.first_ts or t0))
            print(f"  +{time.time() - t0:5.2f}s  frames={frame_stats.count} ~{fps:.1f} fps")
        await asyncio.sleep(1.0)

    stop_consume.set()
    if consume_task:
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    await pc.close()
    t_end = time.time()

    # summary
    print("\n=== Summary ===")
    print(f"  ICE connect:      {t_ice - t0:.2f}s")
    if frame_stats.first_ts:
        print(f"  First frame:      {frame_stats.first_ts - t0:.2f}s")
        print(f"  Frames received:  {frame_stats.count}")
        if frame_stats.intervals:
            ms = [x * 1000 for x in frame_stats.intervals]
            print(f"  Frame interval:   median={statistics.median(ms):.0f}ms "
                  f"p95={sorted(ms)[int(len(ms)*0.95)]:.0f}ms "
                  f"max={max(ms):.0f}ms")
            exp_fps = 20
            late = sum(1 for x in ms if x > (1000 / exp_fps) * 1.5)
            print(f"  Late frames (>1.5×20fps): {late}/{len(ms)}")
    print(f"  Session duration: {t_end - t0:.1f}s")
    print(f"  Drops:            {drops}")
    print(f"  Host transitions: {len(host_changes)}")
    print(f"  ICE states:       {' → '.join(ice_states)}")
    print(f"  Connection:       {' → '.join(conn_states)}")

    ok = drops == 0 and pc.iceConnectionState in ("connected", "completed", "closed")
    if frame_stats.count > 0 and frame_stats.intervals:
        med_ms = statistics.median(frame_stats.intervals) * 1000
        ok = ok and med_ms < 120  # ~20fps = 50ms; allow up to 120ms median
    if frame_stats.count == 0:
        ok = False

    if ok:
        print("\nRESULT: PASS — session stable")
        return 0
    if drops == 0 and frame_stats.count > 0:
        print("\nRESULT: WARN — connected but latency/jitter high")
        return 0
    print("\nRESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
