#!/usr/bin/env python3
"""3-min audio relay monitor: publisher, listen bytes, gaps, Pi publish TCP."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VPS = "116.203.148.254"
TOKEN = "698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"
ROOM = "pi-camera"
DURATION_SEC = 180
POLL_SEC = 1.0
LISTEN_SLICE_SEC = 2.0


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)


def fetch_status(port: int = 8788) -> dict:
    url = f"http://{VPS}:{port}/api/audio-relay/rooms/{ROOM}/status"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def listen_bytes(duration: float, port: int = 8788) -> tuple[int, str | None]:
    url = f"http://{VPS}:{port}/api/audio-relay/rooms/{ROOM}/listen"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    total = 0
    err = None
    try:
        with urllib.request.urlopen(req, timeout=duration + 5) as resp:
            end = time.monotonic() + duration
            while time.monotonic() < end:
                try:
                    chunk = resp.read(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    err = "stream_end"
                    break
                total += len(chunk)
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
    except OSError as e:
        err = str(e)
    return total, err


def main() -> int:
    log(f"=== AUDIO MONITOR {DURATION_SEC}s room={ROOM} vps={VPS} ===")
    events: list[dict] = []
    listen_results: list[dict] = []
    stop = threading.Event()

    def listen_worker(slot: int) -> None:
        while not stop.is_set():
            t0 = time.monotonic()
            n, err = listen_bytes(LISTEN_SLICE_SEC)
            dt = time.monotonic() - t0
            rec = {
                "slot": slot,
                "bytes": n,
                "err": err,
                "dt": round(dt, 2),
                "rate_kbps": round(n * 8 / max(dt, 0.001) / 1000, 1),
            }
            listen_results.append(rec)
            if n == 0 or err:
                log(f"LISTEN#{slot} ZERO/err bytes={n} err={err} dt={dt:.1f}s")
            elif n < 16000:
                log(f"LISTEN#{slot} LOW bytes={n} rate={rec['rate_kbps']}kbps")
            time.sleep(0.05)

    workers = [threading.Thread(target=listen_worker, args=(i,), daemon=True) for i in range(2)]
    for w in workers:
        w.start()

    prev_pub: bool | None = None
    prev_bytes: int | None = None
    gap_streak = 0
    t_start = time.monotonic()

    try:
        while time.monotonic() - t_start < DURATION_SEC:
            elapsed = int(time.monotonic() - t_start)
            try:
                st = fetch_status(8788)
                pub = bool(st.get("publisherActive"))
            except OSError as e:
                log(f"STATUS err: {e}")
                time.sleep(POLL_SEC)
                continue

            recent = listen_results[-4:] if listen_results else []
            bytes_2s = sum(r["bytes"] for r in recent)
            zeros = sum(1 for r in recent if r["bytes"] == 0 or r.get("err"))

            if prev_pub is not None and pub != prev_pub:
                log(f"EVENT publisherActive {prev_pub} -> {pub} at t={elapsed}s")
                events.append({"t": elapsed, "type": "publisher_flip", "to": pub})

            if bytes_2s == 0 and pub:
                gap_streak += 1
                if gap_streak == 1 or gap_streak % 5 == 0:
                    log(f"GAP t={elapsed}s publisherActive=true but listen=0 bytes (streak={gap_streak})")
                if gap_streak == 3:
                    events.append({"t": elapsed, "type": "listen_gap", "streak": gap_streak})
            else:
                if gap_streak >= 3 and bytes_2s > 0:
                    log(f"RECOVER t={elapsed}s listen restored bytes_2s={bytes_2s}")
                    events.append({"t": elapsed, "type": "listen_recover", "bytes_2s": bytes_2s})
                gap_streak = 0

            if prev_bytes is not None and pub and bytes_2s > 0:
                drop = prev_bytes > 32000 and bytes_2s < 8000
                if drop:
                    log(f"DROP t={elapsed}s bytes_2s {prev_bytes}->{bytes_2s}")
                    events.append({"t": elapsed, "type": "rate_drop", "from": prev_bytes, "to": bytes_2s})

            if elapsed % 15 == 0:
                log(f"TICK t={elapsed}s pub={pub} bytes_2s={bytes_2s} zeros_recent={zeros}/{len(recent)}")

            prev_pub = pub
            prev_bytes = bytes_2s
            time.sleep(POLL_SEC)
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=3)

    log("=== SUMMARY ===")
    log(f"events: {len(events)}")
    for ev in events:
        log(f"  t={ev.get('t')}s {ev.get('type')} { {k:v for k,v in ev.items() if k not in ('t','type')} }")

    if listen_results:
        total = sum(r["bytes"] for r in listen_results)
        zero_slices = sum(1 for r in listen_results if r["bytes"] == 0)
        log(f"listen_slices={len(listen_results)} total_bytes={total} zero_slices={zero_slices}")

    out = {
        "duration": DURATION_SEC,
        "events": events,
        "listen_slices": len(listen_results),
        "zero_slices": sum(1 for r in listen_results if r["bytes"] == 0),
    }
    log(f"JSON: {json.dumps(out, ensure_ascii=False)}")
    return 0 if not events else 1


if __name__ == "__main__":
    raise SystemExit(main())
