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
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/ice":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        if not _auth_ok(self):
            self._send(401, b'{"error":"unauthorized"}', "application/json")
            return
        ice_servers = list(DEFAULT_STUN) + _turn_entries()
        payload = json.dumps({"iceServers": ice_servers}, separators=(",", ":")).encode("utf-8")
        self._send(200, payload, "application/json")


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
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nexit", file=sys.stderr)


if __name__ == "__main__":
    main()
