#!/usr/bin/env python3
"""Alternating test: browser→Pi talk × N, then Pi→browser listen check × N."""
from __future__ import annotations

import base64
import json
import socket
import struct
import sys
import time
import urllib.request

VPS = "116.203.148.254"
PORT = 8788
TOKEN = "698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"
ROOM = "pi-camera"
ROUNDS = 5
TALK_FRAMES = 60
FRAME = b"\x00\x7f" * 320


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def status() -> dict:
    url = f"http://{VPS}:{PORT}/api/audio-relay/rooms/{ROOM}/status"
    req = urllib.request.Request(url, headers=auth_headers())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def ws_send_bin(sock: socket.socket, data: bytes) -> None:
    ln = len(data)
    if ln < 126:
        sock.sendall(bytes([0x82, ln]) + data)
    else:
        sock.sendall(bytes([0x82, 126]) + struct.pack("!H", ln) + data)


def talk_once(round_no: int) -> int:
    s = socket.create_connection((VPS, PORT), timeout=10)
    key = base64.b64encode(f"alt-talk-{round_no:02d}!".encode()).decode()
    req = (
        f"GET /api/audio-relay/rooms/{ROOM}/talk-publish-ws HTTP/1.1\r\n"
        f"Host: {VPS}:{PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {TOKEN}\r\n\r\n"
    )
    s.sendall(req.encode())
    if b"101" not in s.recv(512):
        raise RuntimeError(f"round {round_no}: WS upgrade failed")
    sent = 0
    for _ in range(TALK_FRAMES):
        ws_send_bin(s, FRAME)
        sent += len(FRAME)
        time.sleep(0.02)
    s.close()
    return sent


def listen_bytes(seconds: float = 1.5) -> int:
    url = f"http://{VPS}:{PORT}/api/audio-relay/rooms/{ROOM}/listen?token={TOKEN}"
    req = urllib.request.Request(url, headers=auth_headers())
    total = 0
    with urllib.request.urlopen(req, timeout=seconds + 2) as resp:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = resp.read(4096)
            if not chunk:
                break
            total += len(chunk)
    return total


def pi_talk_log_sessions(since: str = "3 min ago") -> list[str]:
    import os
    import subprocess

    host = os.environ.get("PI_HOST", "pavel@100.73.9.95")
    pw = os.environ.get("PI_SSH_PASS", "2214")
    cmd = (
        f"sshpass -p {pw} ssh -o StrictHostKeyChecking=no {host} "
        f"\"journalctl -u pi-audio-talk --since '{since}' --no-pager "
        f"| grep -E 'first chunk|playback ended.*session'\""
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def main() -> int:
    print("=== Alternating duplex test ===")
    print("initial:", status())
    failures = []

    for i in range(1, ROUNDS + 1):
        print(f"\n--- Round {i}/{ROUNDS} ---")
        try:
            sent = talk_once(i)
            print(f"  talk sent: {sent} B")
            time.sleep(1.6)
            st = status()
            print(f"  status after talk: talk={st['talkPublisherActive']} publish={st['publisherActive']}")
            if not st["publisherActive"]:
                failures.append(f"round {i}: Pi publish inactive after talk")
        except Exception as exc:
            failures.append(f"round {i} talk: {exc}")
            print(f"  talk FAIL: {exc}")
            continue

        try:
            heard = listen_bytes(2.0)
            print(f"  Pi→browser listen: {heard} B / 2.0s")
            if heard < 16000:
                failures.append(f"round {i}: Pi→browser too quiet ({heard} B)")
        except Exception as exc:
            failures.append(f"round {i} listen: {exc}")
            print(f"  listen FAIL: {exc}")

        time.sleep(0.5)

    print("\n=== Pi journal (talk sessions) ===")
    try:
        lines = pi_talk_log_sessions("5 min ago")
        sessions = sum(1 for ln in lines if "first chunk" in ln)
        print(f"  talk sessions on Pi (first chunk lines): {sessions}")
        for ln in lines[-12:]:
            print(" ", ln.split("python[")[-1] if "python[" in ln else ln)
        if sessions < ROUNDS:
            failures.append(f"Pi only got {sessions}/{ROUNDS} talk sessions")
    except Exception as exc:
        print(f"  Pi log check skipped: {exc}")

    print("\n=== Result ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"OK: {ROUNDS}/{ROUNDS} rounds — talk + Pi→browser both working")
    return 0


if __name__ == "__main__":
    sys.exit(main())
