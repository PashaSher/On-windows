#!/usr/bin/env python3
"""
Минимальный HTTP-сервер для выдачи WebRTC iceServers (STUN + TURN) браузеру.

Зачем: TURN нужен для связи через «жёсткий» NAT; учётные данные TURN нельзя
вшивать в статический HTML — их отдаёт ваш бэкенд (или вы подставляете URL
этого сервиса после деплоя).

Запуск:
  set TURN_URLS=turn:turn.example.com:3478,turns:turn.example.com:5349
  set TURN_USERNAME=user
  set TURN_PASSWORD=secret
  python ice_config_server.py --host 0.0.0.0 --port 8788

Опционально защитить выдачу токеном (заголовок или ?token=):
  set ICE_CONFIG_TOKEN=random-long-string

GET /api/ice  →  {"iceServers":[...]}  (merge с публичными STUN внутри ответа)

CORS: Access-Control-Allow-Origin: * (для разработки; в проде сузьте origin).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from typing import Iterator

from webrtc_signal_store import STORE, normalize_room_id
from audio_relay_store import AUDIO_RELAY

DEFAULT_STUN = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
]


def _turn_entries() -> list[dict]:
    raw = os.environ.get("TURN_URLS", "").strip()
    if not raw:
        return []
    user = os.environ.get("TURN_USERNAME", "").strip()
    pwd = os.environ.get("TURN_PASSWORD", "").strip()
    out: list[dict] = []
    for part in raw.split(","):
        u = part.strip()
        if not u:
            continue
        entry: dict = {"urls": u}
        if user or pwd:
            entry["username"] = user
            entry["credential"] = pwd
        out.append(entry)
    return out


def _operator_bootstrap_payload() -> dict:
    """Публичный bootstrap для одной ссылки /cam (токен ICE + Firebase + room)."""
    path = Path(os.environ.get("OPERATOR_BOOTSTRAP_FILE", "/etc/default/operator-bootstrap.json"))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    token = os.environ.get("ICE_CONFIG_TOKEN", "").strip()
    room = os.environ.get("WEBRTC_ROOM", "pi-camera").strip() or "pi-camera"
    return {
        "room": room,
        "iceConfigUrl": "/api/ice",
        "iceConfigToken": token,
        "signalApiBase": "/api/signal",
        "audioRelayBase": "/api/audio-relay",
        "signaling": "vps",
    }


def _operator_web_root() -> Path | None:
    raw = os.environ.get("OPERATOR_WEB_ROOT", "").strip()
    if not raw:
        return None
    root = Path(raw).resolve()
    return root if root.is_dir() else None


def _resolve_operator_file(url_path: str) -> Path | None:
    root = _operator_web_root()
    if root is None:
        return None
    rel = unquote(url_path).lstrip("/") or "webrtc-client.html"
    candidate = (root / rel).resolve()
    if not str(candidate).startswith(str(root)):
        return None
    if candidate.is_file():
        return candidate
    return None


def _operator_deploy_root() -> Path:
    raw = os.environ.get("OPERATOR_DEPLOY_ROOT", "/var/lib/ice-config-operator").strip()
    root = Path(raw).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_operator_deploy_file(rel_path: str) -> Path | None:
    root = _operator_deploy_root()
    rel = unquote(rel_path).lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    candidate = (root / rel).resolve()
    if not str(candidate).startswith(str(root)):
        return None
    return candidate


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Clear")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict | list | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _json_response(self, code: int, obj: dict | list) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self._send(code, body, "application/json")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Clear")
        self.end_headers()

    def _parse_audio_relay(self, path: str) -> tuple[str, list[str]] | None:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "audio-relay" or parts[2] != "rooms":
            return None
        return parts[3], parts[4:]

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

    def _read_body_bytes(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if te == "chunked":
            return b"".join(self._iter_request_body_chunks())
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _parse_operator_static(self, path: str) -> str | None:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "operator-static":
            return None
        return "/".join(parts[2:])

    def _handle_operator_static_put(self, rel_path: str) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        target = _resolve_operator_deploy_file(rel_path)
        if target is None:
            self._json_response(400, {"error": "invalid path"})
            return True
        body = self._read_body_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._json_response(200, {"ok": True, "path": rel_path, "bytes": len(body)})
        return True

    def _handle_audio_listen(self, room: str) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        q = AUDIO_RELAY.register_listener(room)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for chunk in AUDIO_RELAY.iter_listener(room, q):
                self._write_chunked(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            AUDIO_RELAY.unregister_listener(room, q)
            try:
                self._write_chunked(b"")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        return True

    def _handle_audio_publish(self, room: str) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        ct = (self.headers.get("Content-Type") or "").lower()
        if "json" in ct:
            self._json_response(400, {"error": "expected application/octet-stream"})
            return True
        AUDIO_RELAY.mark_publisher(room, True)
        total = 0
        try:
            for chunk in self._iter_request_body_chunks():
                total += len(chunk)
                AUDIO_RELAY.publish(room, chunk)
        finally:
            AUDIO_RELAY.mark_publisher(room, False)
        self._json_response(200, {"ok": True, "bytes": total})
        return True

    def _handle_audio_status(self, room: str) -> bool:
        self._json_response(
            200,
            {
                "room": room,
                "publisherActive": AUDIO_RELAY.publisher_active(room),
            },
        )
        return True

    def _dispatch_audio_relay(self, method: str) -> bool:
        parsed = self._parse_audio_relay(urlparse(self.path).path)
        if parsed is None:
            return False
        room, tail = parsed
        room = normalize_room_id(room)
        if method == "GET" and tail == ["listen"]:
            return self._handle_audio_listen(room)
        if method == "GET" and tail == ["status"]:
            return self._handle_audio_status(room)
        if method == "POST" and tail == ["publish"]:
            return self._handle_audio_publish(room)
        if method == "OPTIONS":
            return False
        self._json_response(404, {"error": "not found"})
        return True

    def _parse_signal(self, path: str) -> tuple[str, list[str]] | None:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "signal" or parts[2] != "rooms":
            return None
        return parts[3], parts[4:]

    def _handle_signal_get(self, room: str, tail: list[str]) -> bool:
        qs = parse_qs(urlparse(self.path).query)
        if not tail:
            self._json_response(200, STORE.snapshot(room))
            return True
        if tail == ["events"]:
            since = int(qs.get("since", ["0"])[0] or "0")
            timeout = float(qs.get("timeout", ["25"])[0] or "25")
            self._json_response(200, STORE.wait_events(room, since, timeout))
            return True
        if tail == ["offer"]:
            snap = STORE.snapshot(room)
            self._json_response(200, snap["offer"])
            return True
        if tail == ["answer"]:
            snap = STORE.snapshot(room)
            self._json_response(200, snap["answer"])
            return True
        if tail == ["host"]:
            snap = STORE.snapshot(room)
            self._json_response(200, snap["host"])
            return True
        return False

    def _handle_signal_put(self, room: str, tail: list[str]) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        data = self._read_json()
        if tail == ["offer"]:
            STORE.set_offer(room, data if isinstance(data, dict) else None)
            self._json_response(200, {"ok": True})
            return True
        if tail == ["answer"]:
            STORE.set_answer(room, data if isinstance(data, dict) else None)
            self._json_response(200, {"ok": True})
            return True
        if tail == ["host"]:
            if isinstance(data, dict):
                STORE.set_host(room, data)
            self._json_response(200, {"ok": True})
            return True
        return False

    def _handle_signal_post(self, room: str, tail: list[str]) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        data = self._read_json()
        if not isinstance(data, dict):
            self._json_response(400, {"error": "json object required"})
            return True
        if tail == ["caller-candidates"]:
            cid = STORE.add_caller_candidate(room, data)
            self._json_response(200, {"id": cid})
            return True
        if tail == ["callee-candidates"]:
            cid = STORE.add_callee_candidate(room, data)
            self._json_response(200, {"id": cid})
            return True
        return False

    def _handle_signal_delete(self, room: str, tail: list[str]) -> bool:
        if not _auth_ok(self):
            self._json_response(401, {"error": "unauthorized"})
            return True
        if not tail:
            clear_mode = self.headers.get("X-Clear", "").lower()
            if clear_mode == "caller":
                STORE.clear_caller_side(room)
            elif clear_mode in ("callee", "answer"):
                STORE.clear_callee_side(room)
            else:
                # Legacy Pi clear_room() при пробуждении: не удалять offer оператора.
                snap = STORE.snapshot(room)
                if snap.get("offer"):
                    STORE.clear_callee_side(room)
                else:
                    STORE.clear_room(room)
            self._json_response(200, {"ok": True})
            return True
        return False

    def _dispatch_signal(self, method: str) -> bool:
        parsed = self._parse_signal(urlparse(self.path).path)
        if parsed is None:
            return False
        room, tail = parsed
        room = normalize_room_id(room)
        if method == "GET":
            return self._handle_signal_get(room, tail)
        if method == "PUT":
            return self._handle_signal_put(room, tail)
        if method == "POST":
            return self._handle_signal_post(room, tail)
        if method == "DELETE":
            return self._handle_signal_delete(room, tail)
        return False

    def _serve_operator_static(self, url_path: str) -> bool:
        deploy = _resolve_operator_deploy_file(unquote(url_path).lstrip("/") or "webrtc-client.html")
        if deploy is not None and deploy.is_file():
            body = deploy.read_bytes()
            ctype = mimetypes.guess_type(str(deploy))[0] or "application/octet-stream"
            self._send(200, body, ctype)
            return True
        file_path = _resolve_operator_file(url_path)
        if file_path is None:
            return False
        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self._send(200, body, ctype)
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if self._dispatch_audio_relay("GET"):
            return
        if self._dispatch_signal("GET"):
            return
        if path == "/api/operator-bootstrap":
            payload = _operator_bootstrap_payload()
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if path == "/api/ice":
            if not _auth_ok(self):
                self._send(401, b'{"error":"unauthorized"}', "application/json")
                return
            ice_servers = list(DEFAULT_STUN) + _turn_entries()
            payload = json.dumps({"iceServers": ice_servers}, separators=(",", ":")).encode("utf-8")
            self._send(200, payload, "application/json")
            return
        if self._serve_operator_static(path):
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        rel = self._parse_operator_static(path)
        if rel is not None:
            self._handle_operator_static_put(rel)
            return
        if self._dispatch_signal("PUT"):
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        if self._dispatch_audio_relay("POST"):
            return
        if self._dispatch_signal("POST"):
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_DELETE(self) -> None:  # noqa: N802
        if self._dispatch_signal("DELETE"):
            return
        self._send(404, b'{"error":"not found"}', "application/json")


def main() -> None:
    ap = argparse.ArgumentParser(description="WebRTC ICE (STUN+TURN) config HTTP server")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 for LAN/VPS)")
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"ICE config server http://{args.host}:{args.port}/api/ice",
        file=sys.stderr,
        flush=True,
    )
    if os.environ.get("ICE_CONFIG_TOKEN", "").strip():
        print("Auth: ICE_CONFIG_TOKEN required (Bearer or ?token=)", file=sys.stderr, flush=True)
    if not os.environ.get("TURN_URLS", "").strip():
        print("Warn: TURN_URLS empty — ответ только STUN (для удалённых клиентов часто мало)", file=sys.stderr, flush=True)
    web_root = _operator_web_root()
    if web_root:
        print(f"Operator static: {web_root} (e.g. /webrtc-client.html)", file=sys.stderr, flush=True)
    print("Signaling: GET/PUT/POST /api/signal/rooms/<id>/...", file=sys.stderr, flush=True)
    print("Audio relay: GET .../listen, POST .../publish", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nexit", file=sys.stderr)


if __name__ == "__main__":
    main()
