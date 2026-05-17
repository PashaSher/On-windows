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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from webrtc_signal_store import STORE, normalize_room_id

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
            if self.headers.get("X-Clear", "").lower() == "caller":
                STORE.clear_caller_side(room)
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
        file_path = _resolve_operator_file(url_path)
        if file_path is None:
            return False
        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self._send(200, body, ctype)
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
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
        if self._dispatch_signal("PUT"):
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
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
    httpd = HTTPServer((args.host, args.port), Handler)
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
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nexit", file=sys.stderr)


if __name__ == "__main__":
    main()
