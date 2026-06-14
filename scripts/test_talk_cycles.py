#!/usr/bin/env python3
"""Simulate several browser→Pi talk sessions via talk-publish-ws."""
from __future__ import annotations

import base64
import socket
import struct
import sys
import time
import urllib.request

VPS = "116.203.148.254"
PORT = 8788
TOKEN = "698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"
ROOM = "pi-camera"
CYCLES = 5
FRAMES = 80
FRAME = b"\x00\x7f" * 320  # 640 B


def ws_send_bin(sock: socket.socket, data: bytes) -> None:
    ln = len(data)
    if ln < 126:
        sock.sendall(bytes([0x82, ln]) + data)
    else:
        sock.sendall(bytes([0x82, 126]) + struct.pack("!H", ln) + data)


def talk_once(cycle: int) -> int:
    s = socket.create_connection((VPS, PORT), timeout=10)
    key = base64.b64encode(f"talktest{cycle:02d}!!".encode()).decode()
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
    resp = s.recv(512)
    if b"101" not in resp:
        raise RuntimeError(f"cycle {cycle}: WS upgrade failed: {resp[:120]!r}")
    sent = 0
    for _ in range(FRAMES):
        ws_send_bin(s, FRAME)
        sent += len(FRAME)
        time.sleep(0.02)
    s.close()
    return sent


def status() -> dict:
    url = f"http://{VPS}:{PORT}/api/audio-relay/rooms/{ROOM}/status"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        import json

        return json.loads(resp.read().decode())


def main() -> int:
    print("status before:", status())
    ok = 0
    for i in range(1, CYCLES + 1):
        nbytes = talk_once(i)
        print(f"cycle {i}/{CYCLES}: sent {nbytes} B")
        time.sleep(1.2)
        st = status()
        print(f"  status: talk={st.get('talkPublisherActive')} publish={st.get('publisherActive')}")
        ok += 1
    print(f"OK: {ok}/{CYCLES} talk cycles completed")
    return 0 if ok == CYCLES else 1


if __name__ == "__main__":
    sys.exit(main())
