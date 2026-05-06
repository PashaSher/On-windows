#!/usr/bin/env python3
"""
Приёмник видео для Windows: TCP (MJPEG с префиксом длины) + UDP discovery,
совместим со stream_camera.py на Raspberry Pi.

Режим «ПК ждёт Pi» (Pi: python stream_camera.py send --host auto):
  python receive_stream.py listen --port 5000

Режим «подключиться к Pi» (Pi: python stream_camera.py send --listen --port 5000):
  python receive_stream.py connect --host 192.168.1.10 --port 5000

UDP discovery по умолчанию на порту 37020; отключить: listen --no-discovery
Секрет: --discover-token (должен совпадать с Pi).
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
import sys
import threading

log = logging.getLogger("camrecv")

DISCOVERY_PORT_DEFAULT = 37020
DISCOVERY_VERSION = 1
DISCOVERY_REQ = "discover"
DISCOVERY_RSP = "hello"


def setup_logging(level: int) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stderr)
    h.setLevel(level)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    root.addHandler(h)


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("соединение закрыто до получения всех байт")
        buf += chunk
    return buf


def recv_jpeg_frame(sock: socket.socket) -> bytes:
    hdr = read_exact(sock, 4)
    (length,) = struct.unpack(">I", hdr)
    if length <= 0 or length > 50 * 1024 * 1024:
        raise ValueError(f"некорректная длина кадра: {length}")
    return read_exact(sock, length)


def _discovery_responder_loop(
    udp_sock: socket.socket,
    tcp_port: int,
    token: str | None,
) -> None:
    while True:
        try:
            data, addr = udp_sock.recvfrom(4096)
        except OSError:
            break
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if msg.get("v") != DISCOVERY_VERSION or msg.get("cmd") != DISCOVERY_REQ:
            continue
        req_tok = msg.get("token") or ""
        if token and req_tok != token:
            log.debug("discovery: токен не совпал, ответ не отправляем")
            continue
        rsp: dict = {
            "v": DISCOVERY_VERSION,
            "cmd": DISCOVERY_RSP,
            "tcp": tcp_port,
            "name": socket.gethostname(),
            "http": None,
        }
        try:
            udp_sock.sendto(
                json.dumps(rsp, separators=(",", ":")).encode("utf-8"),
                addr,
            )
            log.info("discovery: hello → %s tcp=%s", addr[0], tcp_port)
        except OSError:
            pass


def start_discovery_responder(
    discover_port: int,
    tcp_port: int,
    token: str | None,
) -> socket.socket:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        udp.bind(("0.0.0.0", discover_port))
    except OSError as e:
        log.error("UDP discovery: не удалось занять порт %s: %s", discover_port, e)
        raise
    th = threading.Thread(
        target=_discovery_responder_loop,
        args=(udp, tcp_port, token),
        daemon=True,
    )
    th.start()
    log.info("UDP discovery: слушаем 0.0.0.0:%s", discover_port)
    return udp


def stream_to_window(sock: socket.socket, window: str) -> None:
    import cv2
    import numpy as np

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    n = 0
    while True:
        try:
            jpeg = recv_jpeg_frame(sock)
        except (EOFError, ValueError, OSError) as e:
            log.info("поток завершён: %s", e)
            break
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            log.warning("кадр %d: imdecode не распознал JPEG", n + 1)
            continue
        n += 1
        if n == 1:
            log.info("первый кадр получен (~%d байт JPEG)", len(jpeg))
        cv2.imshow(window, frame)
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
            log.info("выход по клавише")
            break
    cv2.destroyAllWindows()


def run_listen(
    tcp_port: int,
    discover_port: int | None,
    discover_token: str | None,
    window: str,
) -> None:
    udp_sock: socket.socket | None = None
    if discover_port is not None:
        try:
            udp_sock = start_discovery_responder(discover_port, tcp_port, discover_token)
        except OSError:
            sys.exit(1)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", tcp_port))
    except OSError as e:
        log.error("TCP: не удалось bind 0.0.0.0:%s: %s", tcp_port, e)
        if udp_sock:
            udp_sock.close()
        sys.exit(1)
    srv.listen(5)
    log.info("TCP: ждём подключение Pi на 0.0.0.0:%s (Q или Esc — выход из окна)", tcp_port)

    try:
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("TCP: подключение с %s:%s", addr[0], addr[1])
            try:
                stream_to_window(conn, window)
            finally:
                conn.close()
                log.info("сессия закрыта, снова ожидание на порту %s ...", tcp_port)
    finally:
        srv.close()
        if udp_sock:
            udp_sock.close()


def run_connect(host: str, port: int, window: str, connect_timeout: float) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(max(1.0, connect_timeout))
    log.info("TCP: подключение к %s:%s (таймаут %.0f с) ...", host, port, connect_timeout)
    try:
        sock.connect((host, port))
    except OSError as e:
        log.error("TCP: не удалось подключиться: %s", e)
        if host.startswith("10.42."):
            log.error(
                "Адрес 10.42.x — сеть точки Pi. Нужно подключить ПК к Wi‑Fi SSID малинки "
                "(Ethernet к роутеру не подставляет эту подсеть). Проверьте: "
                "netsh wlan show interfaces и ipconfig (шлюз Wi‑Fi должен быть IP Pi)."
            )
        sys.exit(1)
    sock.settimeout(None)
    log.info("TCP: соединение установлено")
    try:
        stream_to_window(sock, window)
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Windows receiver for Pi camera stream (TCP MJPEG + UDP discovery)."
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_listen = sub.add_parser(
        "listen",
        help="Listen on TCP (+ UDP discovery for Pi: send --host auto)",
    )
    p_listen.add_argument("--port", type=int, default=5000, help="TCP listen port")
    p_listen.add_argument(
        "--discover-port",
        type=int,
        default=DISCOVERY_PORT_DEFAULT,
        help="UDP discovery port (0 disables discovery only)",
    )
    p_listen.add_argument(
        "--no-discovery",
        action="store_true",
        help="Do not bind UDP (TCP only; set Pi host manually)",
    )
    p_listen.add_argument(
        "--discover-token",
        default=None,
        help="Discovery token (must match Pi --discover-token)",
    )
    p_listen.add_argument(
        "--window",
        default="Pi camera",
        help="OpenCV window title",
    )

    p_conn = sub.add_parser(
        "connect",
        help="Connect to Pi (Pi mode: send --listen)",
    )
    p_conn.add_argument("--host", required=True, help="Pi IP or hostname")
    p_conn.add_argument("--port", type=int, default=5000)
    p_conn.add_argument(
        "--connect-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for TCP connect (avoid infinite hang)",
    )
    p_conn.add_argument("--window", default="Pi camera")

    args = parser.parse_args()
    level = logging.DEBUG if args.verbose else getattr(logging, args.log_level)
    setup_logging(level)

    if args.cmd == "listen":
        disc = None if args.no_discovery or args.discover_port == 0 else args.discover_port
        run_listen(args.port, disc, args.discover_token, args.window)
    elif args.cmd == "connect":
        run_connect(args.host, args.port, args.window, args.connect_timeout)


if __name__ == "__main__":
    main()
