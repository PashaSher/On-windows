#!/usr/bin/env python3
"""Operator UI + local PCM audio relay; остальные /api/* → VPS."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse

import urllib.error
import urllib.request

from audio_relay_store import AUDIO_RELAY, AUDIO_TALK

_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept(key: str) -> str:
    digest = hashlib.sha1(key.encode("ascii") + _WS_GUID).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_send_frame(sock: socket.socket, opcode: int, data: bytes) -> None:
    length = len(data)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    sock.sendall(header + data)


def _ws_send_binary(sock: socket.socket, data: bytes) -> None:
    _ws_send_frame(sock, 0x2, data)


def _ws_read_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    hdr = sock.recv(2)
    if len(hdr) < 2:
        return None
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        ext = sock.recv(2)
        if len(ext) < 2:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        if len(ext) < 8:
            return None
        length = struct.unpack("!Q", ext)[0]
    mask = b""
    if masked:
        mask = sock.recv(4)
        if len(mask) < 4:
            return None
    payload = b""
    while len(payload) < length:
        part = sock.recv(length - len(payload))
        if not part:
            return None
        payload += part
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _ws_client_loop(sock: socket.socket, stop: threading.Event) -> None:
    sock.settimeout(1.0)
    while not stop.is_set():
        try:
            frame = _ws_read_frame(sock)
        except socket.timeout:
            continue
        except OSError:
            break
        if frame is None:
            break
        opcode, payload = frame
        if opcode == 0x8:
            break
        if opcode == 0x9:
            try:
                _ws_send_frame(sock, 0xA, payload)
            except OSError:
                break
    stop.set()

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _auth_ok(handler: http.server.BaseHTTPRequestHandler) -> bool:
    token = os.environ.get("ICE_CONFIG_TOKEN", "").strip()
    if not token:
        return True
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == token:
        return True
    qs = parse_qs(urlparse(handler.path).query)
    if qs.get("token", [None])[0] == token:
        return True
    return False


def _normalize_room(room: str) -> str:
    room = unquote(room).strip()
    return room or "pi-camera"


class OperatorProxyHandler(http.server.SimpleHTTPRequestHandler):
    vps_origin: str = "http://116.203.148.254"
    web_root: str = "."

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Clear")

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _iter_request_body_chunks(self) -> Iterator[bytes]:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if te == "chunked":
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size_hex = line.strip().split(b";", 1)[0]
                if not size_hex:
                    break
                size = int(size_hex, 16)
                if size == 0:
                    self.rfile.readline()
                    break
                data = self.rfile.read(size)
                if len(data) < size:
                    break
                self.rfile.readline()
                if data:
                    yield data
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 4096))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    def _write_chunked(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(f"{len(data):x}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _parse_audio_relay(self, path: str) -> tuple[str, list[str]] | None:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "audio-relay" or parts[2] != "rooms":
            return None
        return parts[3], parts[4:]

    def _handle_audio_listen(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        q = AUDIO_RELAY.register_listener(room)
        self.send_response(200)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for chunk in AUDIO_RELAY.iter_listener(room, q):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            AUDIO_RELAY.unregister_listener(room, q)

    def _handle_audio_listen_ws(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        ws_key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
        if not ws_key:
            self.send_error(400, "WebSocket upgrade required")
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept(ws_key))
        self._cors()
        self.end_headers()
        q = AUDIO_RELAY.register_listener(room)
        sock = self.connection
        stop = threading.Event()
        reader = threading.Thread(target=_ws_client_loop, args=(sock, stop), daemon=True)
        reader.start()
        try:
            for chunk in AUDIO_RELAY.iter_listener(room, q):
                if stop.is_set():
                    break
                try:
                    _ws_send_binary(sock, chunk)
                except OSError:
                    break
        finally:
            stop.set()
            AUDIO_RELAY.unregister_listener(room, q)
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _handle_audio_publish(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        AUDIO_RELAY.mark_publisher(room, True)
        total = 0
        try:
            for chunk in self._iter_request_body_chunks():
                total += len(chunk)
                AUDIO_RELAY.publish(room, chunk)
        finally:
            AUDIO_RELAY.mark_publisher(room, False)
        self._send_json(200, {"ok": True, "bytes": total})

    def _handle_audio_status(self, room: str) -> None:
        self._send_json(
            200,
            {
                "room": room,
                "publisherActive": AUDIO_RELAY.publisher_active(room),
                "talkPublisherActive": AUDIO_TALK.publisher_active(room),
            },
        )

    def _handle_talk_listen(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        q = AUDIO_TALK.register_listener(room)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for chunk in AUDIO_TALK.iter_listener(room, q):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            AUDIO_TALK.unregister_listener(room, q)

    def _handle_talk_publish(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        AUDIO_TALK.mark_publisher(room, True)
        total = 0
        try:
            for chunk in self._iter_request_body_chunks():
                total += len(chunk)
                AUDIO_TALK.publish(room, chunk)
        finally:
            AUDIO_TALK.mark_publisher(room, False)
        self._send_json(200, {"ok": True, "bytes": total})

    def _handle_talk_publish_ws(self, room: str) -> None:
        if not _auth_ok(self):
            self._send_json(401, {"error": "unauthorized"})
            return
        ws_key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
        if not ws_key:
            self.send_error(400, "WebSocket upgrade required")
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept(ws_key))
        self._cors()
        self.end_headers()
        sock = self.connection
        AUDIO_TALK.mark_publisher(room, True)
        sock.settimeout(1.0)
        try:
            while True:
                try:
                    frame = _ws_read_frame(sock)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    try:
                        _ws_send_frame(sock, 0xA, payload)
                    except OSError:
                        break
                    continue
                if opcode == 0x2 and payload:
                    AUDIO_TALK.publish(room, payload)
        finally:
            AUDIO_TALK.mark_publisher(room, False)
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _dispatch_audio_relay(self, method: str) -> bool:
        parsed = self._parse_audio_relay(urlparse(self.path).path)
        if parsed is None:
            return False
        room, tail = parsed
        room = _normalize_room(room)
        if method == "GET" and tail == ["listen"]:
            self._handle_audio_listen(room)
            return True
        if method == "GET" and tail == ["listen-ws"]:
            self._handle_audio_listen_ws(room)
            return True
        if method == "GET" and tail == ["status"]:
            self._handle_audio_status(room)
            return True
        if method == "POST" and tail == ["publish"]:
            self._handle_audio_publish(room)
            return True
        if method == "GET" and tail == ["talk-listen"]:
            self._handle_talk_listen(room)
            return True
        if method == "GET" and tail == ["talk-publish-ws"]:
            self._handle_talk_publish_ws(room)
            return True
        if method == "POST" and tail == ["talk-publish"]:
            self._handle_talk_publish(room)
            return True
        self._send_json(404, {"error": "not found"})
        return True

    def _proxy_vps(self, stream: bool = False) -> None:
        url = self.vps_origin + self.path
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else None
        headers = {}
        for key in ("Authorization", "Content-Type", "X-Clear", "Accept"):
            val = self.headers.get(key)
            if val:
                headers[key] = val
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(req, timeout=3600 if stream else 120)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        except Exception as exc:
            self._send_json(502, {"error": f"proxy failed: {exc}"})
            return
        try:
            self.send_response(resp.status)
            for key in (
                "Content-Type",
                "Cache-Control",
                "Transfer-Encoding",
                "Access-Control-Allow-Origin",
            ):
                val = resp.headers.get(key)
                if val:
                    self.send_header(key, val)
            self._cors()
            self.end_headers()
            if stream:
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                self.wfile.write(resp.read())
        finally:
            resp.close()

    def _serve_static(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/cam", "/cam/"):
            self.path = "/cam.html"
        http.server.SimpleHTTPRequestHandler.do_GET(self)

    def do_OPTIONS(self) -> None:
        if self.path.startswith("/api/"):
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        super().do_OPTIONS()

    def _handle_operator_bootstrap(self) -> None:
        token = os.environ.get("ICE_CONFIG_TOKEN", "").strip()
        room = os.environ.get("WEBRTC_ROOM", "pi-camera").strip() or "pi-camera"
        self._send_json(
            200,
            {
                "room": room,
                "iceConfigUrl": "/api/ice",
                "iceConfigToken": token,
                "signalApiBase": "/api/signal",
                "audioRelayBase": "/api/audio-relay",
                "signaling": "vps",
            },
        )

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/operator-bootstrap":
            self._handle_operator_bootstrap()
            return
        if self._dispatch_audio_relay("GET"):
            return
        if self.path.startswith("/api/"):
            stream = "/api/audio-relay/" in self.path and self.path.endswith("/listen")
            self._proxy_vps(stream=stream)
            return
        self._serve_static()

    def do_POST(self) -> None:
        if self._dispatch_audio_relay("POST"):
            return
        if self.path.startswith("/api/"):
            self._proxy_vps()
            return
        self.send_error(405)

    def do_PUT(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_vps()
            return
        self.send_error(405)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_vps()
            return
        self.send_error(405)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--vps", default=os.environ.get("OPERATOR_VPS_ORIGIN", "http://116.203.148.254"))
    args = ap.parse_args()
    OperatorProxyHandler.vps_origin = args.vps.rstrip("/")
    root = os.environ.get("OPERATOR_WEB_ROOT", "")
    if not root or not os.path.isdir(root):
        print("OPERATOR_WEB_ROOT must point to operator static files", file=sys.stderr)
        sys.exit(1)

    def factory(*a, **kw):
        return OperatorProxyHandler(*a, directory=root, **kw)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), factory)
    print(
        f"operator proxy http://{args.host}:{args.port}/cam  "
        f"(api -> {args.vps}, audio-relay local)",
        file=sys.stderr,
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nexit", file=sys.stderr)


if __name__ == "__main__":
    main()
