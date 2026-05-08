#!/usr/bin/env python3
"""
Приёмник видео для Windows: TCP (MJPEG с префиксом длины) + UDP discovery,
совместим со stream_camera.py на Raspberry Pi.

Режим «ПК ждёт Pi» (Pi: python stream_camera.py send --host auto):
  python receive_stream.py listen --port 5000

Режим «подключиться к Pi» (Pi: send --listen --port 5000):
  python receive_stream.py connect --host 192.168.1.10 --port 5000

Romeo (TCP управление, как на Pi romeo_control_server), по умолчанию порт 5001:
  python receive_stream.py connect --host 10.42.0.1 --romeo-control-port 5001
  Отключить: --romeo-control-port 0

UDP discovery по умолчанию на порту 37020; отключить: listen --no-discovery
Секрет: --discover-token (должен совпадать с Pi).

Качество / FPS (в основном настройка на Pi, не на ПК):
  На Pi: меньше разрешение и выше --jpeg-quality (например 85–92) дают меньше «мыла» при движении,
  но больше трафика; --fps и загрузка CPU тоже влияют.
  На ПК: --display-max-width 960 — уменьшить картинку перед imshow (быстрее UI);
  --show-fps — показывать принятый FPS в заголовке окна.

Башня IJKL/стрелки по умолчанию плавно: --romeo-turret-mode smooth (или velocity — то же самое):
  при нажатии — {"action":"turret_smooth","dir":"left"|...} [,"v":N], при отпускании —
  {"action":"turret_stop"} (на Romeo TS). Опция --romeo-turret-smooth-v: если >0, в JSON
  добавляется v; иначе скорость по умолчанию на Pi/прошивке. Режим step — дискретные
  turret + --romeo-turret-repeat-ms.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import socket
import struct
import sys
import threading
import time

log = logging.getLogger("camrecv")

_VIDEO_EOF = object()

# OpenCV waitKeyEx: стрелки (типичные коды Windows)
_ARROW_TURRET = {
    65362: "up",
    65364: "down",
    65361: "left",
    65363: "right",
}

# Гусеницы: латиница + те же физические клавиши при русской раскладке (ЙЦУКЕН…)
_DF = {"action": "drive", "dir": "forward"}
_DB = {"action": "drive", "dir": "back"}
_DL = {"action": "drive", "dir": "left"}
_DR = {"action": "drive", "dir": "right"}
ROME_DRIVE_BY_CH: dict[int, dict] = {
    ord("w"): _DF,
    ord("W"): _DF,
    ord("s"): _DB,
    ord("S"): _DB,
    ord("a"): _DL,
    ord("A"): _DL,
    ord("d"): _DR,
    ord("D"): _DR,
    ord("ц"): _DF,
    ord("Ц"): _DF,
    ord("ы"): _DB,
    ord("Ы"): _DB,
    ord("ф"): _DL,
    ord("Ф"): _DL,
    ord("в"): _DR,
    ord("В"): _DR,
}

# Башня: IJKL + русские буквы с тех же клавиш (ш о л д / Ш О Л Д)
ROME_TURRET_BY_CH: dict[int, str] = {
    ord("i"): "up",
    ord("I"): "up",
    ord("k"): "down",
    ord("K"): "down",
    ord("j"): "right",
    ord("J"): "right",
    ord("l"): "left",
    ord("L"): "left",
    ord("ш"): "up",
    ord("Ш"): "up",
    ord("о"): "right",
    ord("О"): "right",
    ord("л"): "down",
    ord("Л"): "down",
    ord("д"): "left",
    ord("Д"): "left",
}

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
    control_port: int | None,
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
        if control_port is not None:
            rsp["control"] = control_port
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
    control_port: int | None = None,
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
        args=(udp, tcp_port, token, control_port),
        daemon=True,
    )
    th.start()
    log.info("UDP discovery: слушаем 0.0.0.0:%s", discover_port)
    return udp


class RomeoControlClient:
    """
    TCP к Pi (romeo_control_server): одна команда — одна строка UTF-8 с \\n,
    ответ — одна строка JSON (как в docs/pc-remote-control).
    """

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 5.0,
        *,
        debug: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._debug = debug
        self._sock: socket.socket | None = None
        self._buf = b""
        self._lock = threading.Lock()

    def try_connect(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(max(1.0, self._connect_timeout))
        try:
            s.connect((self._host, self._port))
        except OSError as e:
            log.warning(
                "Romeo control: подключение к %s:%s не удалось (%s). "
                "Клавиши в окне видео не будут отправляться на Pi.",
                self._host,
                self._port,
                e,
            )
            s.close()
            return False
        s.settimeout(3.0)
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = s
            self._buf = b""
        log.info("Romeo control: TCP подключён %s:%s", self._host, self._port)
        return True

    def close(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._buf = b""

    def send_line(self, line: str) -> dict | None:
        payload = (line.rstrip("\n") + "\n").encode("utf-8")
        with self._lock:
            sock = self._sock
            if sock is None:
                if self._debug:
                    log.info("romeo TX пропущен (сокет закрыт): %r", line[:200])
                return None
            try:
                if self._debug:
                    log.info("romeo TX (%d B): %s", len(payload), line[:500])
                sock.sendall(payload)
                while b"\n" not in self._buf:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("romeo: соединение закрыто")
                    self._buf += chunk
                first, _, rest = self._buf.partition(b"\n")
                self._buf = rest
            except (OSError, ConnectionError) as e:
                log.warning("Romeo control: ошибка отправки/чтения: %s", e)
                try:
                    sock.close()
                except OSError:
                    pass
                self._sock = None
                self._buf = b""
                return None
        try:
            out = json.loads(first.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("Romeo control: невалидный JSON ответ: %s raw=%r", e, first[:300])
            return None
        if self._debug:
            log.info("romeo RX: %s", out)
        elif not out.get("ok", True):
            log.warning("Romeo control: ответ ok=false: %s", out)
        return out

    def send_json_cmd(self, obj: dict) -> dict | None:
        return self.send_line(json.dumps(obj, separators=(",", ":")))


def _try_raise_preview_window(window: str) -> None:
    """Чуть поднять окно превью (часто помогает получить фокус для waitKey)."""
    import cv2

    try:
        cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
        cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 0)
    except cv2.error:
        pass


def _key_u16(key: int) -> int:
    """Код символа для раскладки (кириллица не обрезается до одного байта)."""
    if key in (-1, 255):
        return 0xFFFE  # «нет клавиши», не пересекается с Unicode
    return int(key) & 0xFFFF


def _turret_dir_from_u_key(u: int, key: int) -> str | None:
    if u in ROME_TURRET_BY_CH:
        return ROME_TURRET_BY_CH[u]
    return _ARROW_TURRET.get(key)


def _romeo_turret_mode_is_smooth(mode: str) -> bool:
    """Плавная башня: turret_smooth + turret_stop (velocity — устаревший синоним в CLI)."""
    return mode in ("smooth", "velocity")


def _romeo_keyboard(
    romeo: RomeoControlClient,
    key: int,
    prev_key: int,
    *,
    romeo_debug: bool,
    turret_repeat_ms: float,
    drive_repeat_ms: float,
    turret_mode: str,
    turret_smooth_v: float,
    state: dict,
) -> None:
    """Клавиши + опциональный повтор turret/drive по таймеру при удержании."""
    if romeo_debug and key != prev_key and key not in (-1, 255):
        log.info("keyboard: waitKeyEx=%s u16=0x%04x", key, _key_u16(key))

    now = time.monotonic()
    u = _key_u16(key)
    prev_u = _key_u16(prev_key)
    in_drive = u in ROME_DRIVE_BY_CH
    prev_in_drive = prev_u in ROME_DRIVE_BY_CH

    if in_drive:
        cmd = ROME_DRIVE_BY_CH[u]
        ddir = str(cmd.get("dir", ""))
        edge = key != prev_key or not prev_in_drive or prev_u != u or state.get("drive_dir") != ddir
        rep_ok = (
            drive_repeat_ms > 0
            and state.get("drive_dir") == ddir
            and now - float(state.get("drive_last", 0.0)) >= drive_repeat_ms / 1000.0
        )
        if edge:
            r = romeo.send_json_cmd(cmd)
            log.info("romeo cmd drive %s -> %s", cmd, r)
            state["drive_dir"] = ddir
            state["drive_last"] = now
        elif rep_ok:
            r = romeo.send_json_cmd(cmd)
            log.debug("romeo repeat drive %s -> %s", cmd, r)
            state["drive_last"] = now
    elif prev_in_drive:
        r = romeo.send_json_cmd({"action": "drive", "dir": "stop"})
        log.info("romeo cmd drive stop -> %s", r)
        state["drive_dir"] = None
        state["drive_last"] = 0.0
    else:
        state["drive_dir"] = None

    td = _turret_dir_from_u_key(u, key)
    prev_td = _turret_dir_from_u_key(prev_u, prev_key) if prev_key not in (-1, 255) else None

    if _romeo_turret_mode_is_smooth(turret_mode):
        d = td
        prev_smooth = state.get("last_turret_smooth")
        if d != prev_smooth:
            if d is None and prev_smooth is not None:
                r = romeo.send_json_cmd({"action": "turret_stop"})
                log.info("romeo cmd turret_stop -> %s", r)
            elif d is not None:
                cmd_sm: dict[str, object] = {"action": "turret_smooth", "dir": d}
                if turret_smooth_v > 0.0:
                    cmd_sm["v"] = turret_smooth_v
                r = romeo.send_json_cmd(cmd_sm)
                log.info("romeo cmd %s -> %s", cmd_sm, r)
            state["last_turret_smooth"] = d
        state["turret_dir"] = None
        state["turret_last"] = 0.0
    elif td is not None:
        edge_t = key != prev_key or td != state.get("turret_dir") or td != prev_td
        rep_t = (
            turret_repeat_ms > 0
            and state.get("turret_dir") == td
            and now - float(state.get("turret_last", 0.0)) >= turret_repeat_ms / 1000.0
        )
        if edge_t:
            r = romeo.send_json_cmd({"action": "turret", "dir": td})
            log.info("romeo cmd turret %s -> %s", td, r)
            state["turret_dir"] = td
            state["turret_last"] = now
        elif rep_t:
            r = romeo.send_json_cmd({"action": "turret", "dir": td})
            log.debug("romeo repeat turret %s -> %s", td, r)
            state["turret_last"] = now
    else:
        state["turret_dir"] = None

    if u in (ord(" "), ord("x"), ord("X")) and key != prev_key:
        r = romeo.send_json_cmd({"action": "drive", "dir": "stop"})
        log.info("romeo cmd stop (space/x) -> %s", r)
    if u in (ord("h"), ord("H"), ord("р"), ord("Р")) and key != prev_key:
        r = romeo.send_json_cmd({"action": "home"})
        log.info("romeo cmd home -> %s", r)
    if u in (ord("m"), ord("M"), ord("ь"), ord("Ь")) and key != prev_key:
        r = romeo.send_line("MS")
        log.info("romeo cmd MS -> %s", r)


def _display_resize_for_show(frame, max_w: int | None):
    """Уменьшение по ширине перед показом: меньше пикселей → выше реальный FPS окна."""
    import cv2

    if max_w is None or max_w <= 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_w:
        return frame
    nh = max(1, int(round(h * (max_w / float(w)))))
    return cv2.resize(frame, (max_w, nh), interpolation=cv2.INTER_AREA)


def stream_to_window(
    sock: socket.socket,
    window: str,
    romeo: RomeoControlClient | None = None,
    *,
    romeo_debug: bool = False,
    romeo_missing_warn: str | None = None,
    romeo_turret_mode: str = "smooth",
    romeo_turret_smooth_v: float = 0.0,
    romeo_turret_repeat_ms: float = 70.0,
    romeo_drive_repeat_ms: float = 0.0,
    display_max_width: int | None = None,
    show_fps_title: bool = False,
) -> None:
    import cv2
    import numpy as np

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    warned_missing = False
    if romeo_missing_warn:
        log.warning("%s", romeo_missing_warn)
        warned_missing = True
    if romeo is not None:
        log.debug("Romeo: WASD; IJKL/стрелки башня (smooth: turret_smooth→turret_stop); Q — выход.")
    elif not warned_missing:
        log.info("Romeo отключён (--romeo-control-port 0). Только видео.")

    jpeg_q: queue.Queue[bytes | object] = queue.Queue(maxsize=1)

    def recv_worker() -> None:
        try:
            while True:
                jpeg = recv_jpeg_frame(sock)
                try:
                    jpeg_q.put_nowait(jpeg)
                except queue.Full:
                    try:
                        jpeg_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        jpeg_q.put_nowait(jpeg)
                    except queue.Full:
                        pass
        except (EOFError, ValueError, OSError) as e:
            log.info("поток завершён: %s", e)
        finally:
            try:
                jpeg_q.put_nowait(_VIDEO_EOF)
            except queue.Full:
                try:
                    jpeg_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    jpeg_q.put_nowait(_VIDEO_EOF)
                except queue.Full:
                    pass

    th = threading.Thread(target=recv_worker, daemon=True)
    th.start()

    n = 0
    prev_key = -1
    last_np = None
    log_fps_n = 0
    log_fps_t0 = time.monotonic()
    title_fps_n = 0
    title_fps_t0 = time.monotonic()
    repeat_state: dict = {
        "turret_dir": None,
        "turret_last": 0.0,
        "drive_dir": None,
        "drive_last": 0.0,
        "last_turret_smooth": None,
    }

    try:
        while True:
            try:
                item = jpeg_q.get(timeout=0.05)
            except queue.Empty:
                item = None
            if item is _VIDEO_EOF:
                break
            if isinstance(item, (bytes, bytearray)):
                arr = np.frombuffer(item, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    log.warning("кадр: imdecode не распознал JPEG")
                    continue
                last_np = frame
                n += 1
                if n == 1:
                    log.info("первый кадр получен (~%d байт JPEG)", len(item))
                log_fps_n += 1
                title_fps_n += 1
                now = time.monotonic()
                if now - log_fps_t0 >= 2.0 and log_fps_n > 0:
                    log.info("видео: ~%.1f кадр/с принято (декод)", log_fps_n / (now - log_fps_t0))
                    log_fps_t0 = now
                    log_fps_n = 0
                if show_fps_title and now - title_fps_t0 >= 0.75 and title_fps_n > 0:
                    t = f"{window}  {title_fps_n / (now - title_fps_t0):.0f} fps"
                    try:
                        cv2.setWindowTitle(window, t)
                    except cv2.error:
                        pass
                    title_fps_t0 = now
                    title_fps_n = 0

            if last_np is None:
                key = cv2.waitKeyEx(10)
                u = _key_u16(key)
                if u in (ord("q"), ord("Q"), ord("й"), ord("Й"), 27) or (key & 0xFF) == 27:
                    log.info("выход по клавише")
                    if romeo is not None:
                        romeo.send_json_cmd({"action": "turret_stop"})
                        romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                    break
                if romeo is not None:
                    _romeo_keyboard(
                        romeo,
                        key,
                        prev_key,
                        romeo_debug=romeo_debug,
                        turret_repeat_ms=romeo_turret_repeat_ms,
                        drive_repeat_ms=romeo_drive_repeat_ms,
                        turret_mode=romeo_turret_mode,
                        turret_smooth_v=romeo_turret_smooth_v,
                        state=repeat_state,
                    )
                prev_key = key
                continue

            disp = _display_resize_for_show(last_np, display_max_width)
            cv2.imshow(window, disp)
            if n == 1:
                _try_raise_preview_window(window)
            key = cv2.waitKeyEx(1)
            u = _key_u16(key)
            if u in (ord("q"), ord("Q"), ord("й"), ord("Й"), 27) or (key & 0xFF) == 27:
                log.info("выход по клавише")
                if romeo is not None:
                    romeo.send_json_cmd({"action": "turret_stop"})
                    romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                break

            if romeo is not None:
                _romeo_keyboard(
                    romeo,
                    key,
                    prev_key,
                    romeo_debug=romeo_debug,
                    turret_repeat_ms=romeo_turret_repeat_ms,
                    drive_repeat_ms=romeo_drive_repeat_ms,
                    turret_mode=romeo_turret_mode,
                    turret_smooth_v=romeo_turret_smooth_v,
                    state=repeat_state,
                )
            elif romeo_debug and key != prev_key and key not in (-1, 255):
                log.info("keyboard (Romeo выкл): waitKeyEx=%s u16=0x%04x", key, u)

            prev_key = key
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        th.join(timeout=4.0)
    cv2.destroyAllWindows()


def run_listen(
    tcp_port: int,
    discover_port: int | None,
    discover_token: str | None,
    window: str,
    romeo_control_port: int,
    romeo_connect_timeout: float,
    romeo_debug: bool,
    romeo_turret_mode: str,
    romeo_turret_smooth_v: float,
    romeo_turret_repeat_ms: float,
    romeo_drive_repeat_ms: float,
    display_max_width: int | None,
    show_fps_title: bool,
) -> None:
    udp_sock: socket.socket | None = None
    disc_control: int | None = None
    if discover_port is not None and romeo_control_port > 0:
        disc_control = romeo_control_port
    if discover_port is not None:
        try:
            udp_sock = start_discovery_responder(
                discover_port, tcp_port, discover_token, disc_control
            )
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
            romeo: RomeoControlClient | None = None
            if romeo_control_port > 0:
                rc = RomeoControlClient(
                    addr[0],
                    romeo_control_port,
                    romeo_connect_timeout,
                    debug=romeo_debug,
                )
                if rc.try_connect():
                    romeo = rc
            miss = None
            if romeo_control_port > 0 and romeo is None:
                miss = (
                    f"Romeo: не удалось открыть второй TCP к {addr[0]}:{romeo_control_port} "
                    "(управление с клавиатуры не работает; видео — да)."
                )
            try:
                stream_to_window(
                    conn,
                    window,
                    romeo,
                    romeo_debug=romeo_debug,
                    romeo_missing_warn=miss,
                    romeo_turret_mode=romeo_turret_mode,
                    romeo_turret_smooth_v=romeo_turret_smooth_v,
                    romeo_turret_repeat_ms=romeo_turret_repeat_ms,
                    romeo_drive_repeat_ms=romeo_drive_repeat_ms,
                    display_max_width=display_max_width,
                    show_fps_title=show_fps_title,
                )
            finally:
                if romeo is not None:
                    romeo.close()
                conn.close()
                log.info("сессия закрыта, снова ожидание на порту %s ...", tcp_port)
    finally:
        srv.close()
        if udp_sock:
            udp_sock.close()


def run_connect(
    host: str,
    port: int,
    window: str,
    connect_timeout: float,
    romeo_control_port: int,
    romeo_host: str | None,
    romeo_connect_timeout: float,
    romeo_debug: bool,
    romeo_turret_mode: str,
    romeo_turret_smooth_v: float,
    romeo_turret_repeat_ms: float,
    romeo_drive_repeat_ms: float,
    display_max_width: int | None,
    show_fps_title: bool,
) -> None:
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

    romeo: RomeoControlClient | None = None
    rhost = romeo_host or host
    if romeo_control_port > 0:
        rc = RomeoControlClient(
            rhost,
            romeo_control_port,
            romeo_connect_timeout,
            debug=romeo_debug,
        )
        if rc.try_connect():
            romeo = rc
    miss = None
    if romeo_control_port > 0 and romeo is None:
        miss = (
            f"Romeo: не удалось открыть второй TCP к {rhost}:{romeo_control_port} "
            "(управление с клавиатуры не работает; видео — да)."
        )

    try:
        stream_to_window(
            sock,
            window,
            romeo,
            romeo_debug=romeo_debug,
            romeo_missing_warn=miss,
            romeo_turret_mode=romeo_turret_mode,
            romeo_turret_smooth_v=romeo_turret_smooth_v,
            romeo_turret_repeat_ms=romeo_turret_repeat_ms,
            romeo_drive_repeat_ms=romeo_drive_repeat_ms,
            display_max_width=display_max_width,
            show_fps_title=show_fps_title,
        )
    finally:
        if romeo is not None:
            romeo.close()
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
    p_listen.add_argument(
        "--romeo-control-port",
        type=int,
        default=5001,
        help="Romeo control TCP (Pi romeo_control_server); 0 disables",
    )
    p_listen.add_argument(
        "--romeo-connect-timeout",
        type=float,
        default=5.0,
        help="TCP connect timeout for Romeo control socket",
    )
    p_listen.add_argument(
        "--romeo-debug",
        action="store_true",
        help="Verbose Romeo TX/RX and raw keyboard codes (INFO)",
    )
    p_listen.add_argument(
        "--romeo-turret-mode",
        choices=("smooth", "velocity", "step"),
        default="smooth",
        help="Башня: smooth|velocity=turret_smooth+stop (PL/PR/TU/TD); step=turret+repeat",
    )
    p_listen.add_argument(
        "--romeo-turret-smooth-v",
        type=float,
        default=0.0,
        help="Для smooth/velocity: если >0 — добавить v в turret_smooth (иначе скорость по умолчанию на Pi)",
    )
    p_listen.add_argument(
        "--romeo-turret-repeat-ms",
        type=float,
        default=70.0,
        help="Только step: повтор turret JSON пока клавиша зажата (0 = только edge)",
    )
    p_listen.add_argument(
        "--romeo-drive-repeat-ms",
        type=float,
        default=0.0,
        help="Repeat drive JSON while key held (0 = off; use if tracks need pulses)",
    )
    p_listen.add_argument(
        "--display-max-width",
        type=int,
        default=0,
        help="Max width in pixels before imshow (0 = native; e.g. 960 for faster UI)",
    )
    p_listen.add_argument(
        "--show-fps",
        action="store_true",
        help="Show decoded FPS in window title",
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
    p_conn.add_argument(
        "--romeo-control-port",
        type=int,
        default=5001,
        help="Romeo control TCP; 0 disables (match Pi --romeo-control-port)",
    )
    p_conn.add_argument(
        "--romeo-host",
        default=None,
        help="Romeo host if different from video --host",
    )
    p_conn.add_argument(
        "--romeo-connect-timeout",
        type=float,
        default=5.0,
        help="TCP connect timeout for Romeo control socket",
    )
    p_conn.add_argument(
        "--romeo-debug",
        action="store_true",
        help="Verbose Romeo TX/RX and raw keyboard codes (INFO)",
    )
    p_conn.add_argument(
        "--romeo-turret-mode",
        choices=("smooth", "velocity", "step"),
        default="smooth",
        help="Башня: smooth|velocity=turret_smooth+stop; step=turret+repeat",
    )
    p_conn.add_argument(
        "--romeo-turret-smooth-v",
        type=float,
        default=0.0,
        help="Для smooth/velocity: v в turret_smooth если >0",
    )
    p_conn.add_argument(
        "--romeo-turret-repeat-ms",
        type=float,
        default=70.0,
        help="Только step: повтор turret JSON пока клавиша зажата (0 = только edge)",
    )
    p_conn.add_argument(
        "--romeo-drive-repeat-ms",
        type=float,
        default=0.0,
        help="Repeat drive JSON while key held (0 = off)",
    )
    p_conn.add_argument(
        "--display-max-width",
        type=int,
        default=0,
        help="Max width in pixels before imshow (0 = native)",
    )
    p_conn.add_argument(
        "--show-fps",
        action="store_true",
        help="Show decoded FPS in window title",
    )
    p_conn.add_argument("--window", default="Pi camera")

    args = parser.parse_args()
    level = logging.DEBUG if args.verbose else getattr(logging, args.log_level)
    setup_logging(level)

    if args.cmd == "listen":
        disc = None if args.no_discovery or args.discover_port == 0 else args.discover_port
        dmw = args.display_max_width if args.display_max_width > 0 else None
        run_listen(
            args.port,
            disc,
            args.discover_token,
            args.window,
            args.romeo_control_port,
            args.romeo_connect_timeout,
            args.romeo_debug,
            args.romeo_turret_mode,
            args.romeo_turret_smooth_v,
            args.romeo_turret_repeat_ms,
            args.romeo_drive_repeat_ms,
            dmw,
            args.show_fps,
        )
    elif args.cmd == "connect":
        dmw = args.display_max_width if args.display_max_width > 0 else None
        run_connect(
            args.host,
            args.port,
            args.window,
            args.connect_timeout,
            args.romeo_control_port,
            args.romeo_host,
            args.romeo_connect_timeout,
            args.romeo_debug,
            args.romeo_turret_mode,
            args.romeo_turret_smooth_v,
            args.romeo_turret_repeat_ms,
            args.romeo_drive_repeat_ms,
            dmw,
            args.show_fps,
        )


if __name__ == "__main__":
    main()
