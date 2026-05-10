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

UDP discovery по умолчанию на порту 37020; в listen — отключить: --no-discovery.
В connect без --host: ПК сам шлёт broadcast и берёт IP/порты из ответа hello (поля
tcp и control). Секрет: --discover-token (должен совпадать с Pi).
  python receive_stream.py connect            # авто-поиск
  python receive_stream.py connect --host 10.42.0.1 --port 5000   # явно

Видео конвейер — три независимых стадии (на трёх потоках):
  1) video-rx:  только TCP recv → raw_q (maxsize=1, drop-old).
  2) video-dec: raw_q → cv2.imdecode + resize → frame_q (maxsize=1, drop-old).
  3) main:      frame_q → cv2.imshow + опрос ввода + UI.
Декод и сетевой recv разнесены, чтобы всплеск декодирования (или нагрузка
от управления) не замедлял дренаж TCP — иначе у Pi заполняется буфер
отправки и видео визуально подлагивает.

Контроль (Romeo) — request/response: на каждую команду ждём JSON-ответ Pi
в пределах --romeo-rx-timeout (по умолчанию 0.6 с). Если ответа нет —
повторяем ту же команду до --romeo-max-attempts раз (по умолчанию 3),
при необходимости пересоздавая TCP. Все команды Romeo идемпотентны, повтор
безопасен. Главный поток только кладёт команду в очередь; фоновый воркер
romeo-tx пишет/читает/повторяет, чтобы UI не блокировался сетью. Дубли
одинакового payload сжимаются, пока предыдущий экземпляр в очереди или
уже отправляется с ожиданием ответа — лишние копии на провод не попадают.

Логи отдельных команд идут на DEBUG (видно при -v), чтобы под нагрузкой не
нагружать stdout. Сводка скорости команд `romeo TX: …/с` остаётся на INFO.

Качество / FPS (в основном настройка на Pi, не на ПК):
  На Pi: меньше разрешение и выше --jpeg-quality (например 85–92) дают меньше «мыла» при движении,
  но больше трафика; --fps и загрузка CPU тоже влияют.
  На ПК: --display-max-width 960 — уменьшить картинку перед imshow (быстрее UI);
  --show-fps — показывать принятый FPS в заголовке окна.

Управление гусеницами — режим hold (по умолчанию, key-down / key-up):
  Зажал W → ровно один пакет drive forward в момент нажатия.
  Пока держишь — на провод не уходит ничего.
  Отпустил W → ровно один пакет drive stop.
  Аналогично S/A/D. Это «не стрим, но удержание для движения».
  Стопы дополнительно дебаунсятся --romeo-drive-stop-grace-ms (по умолч. 80 мс),
  чтобы кратковременная потеря фокуса окна или провал GetAsyncKeyState не
  трактовалась как ложное «отпустил».
Альтернатива: --romeo-drive-mode toggle — тап W вкл/выкл движение, тап S
переключает направление. Удержание в этом режиме игнорируется.

Башня (IJKL/стрелки) всегда работает в hold-режиме (так удобнее прицеливаться):
  smooth: при нажатии — turret_smooth dir [,v]; при отпускании — turret_stop.
  step:   при нажатии — turret dir.
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


_IS_WIN = sys.platform == "win32"
_user32 = None
_WinMSG = None
if _IS_WIN:
    try:
        import ctypes
        from ctypes import wintypes

        class _WinPOINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class _WinMSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", _WinPOINT),
            ]

        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        _user32.GetAsyncKeyState.restype = ctypes.c_short
        _user32.GetForegroundWindow.restype = ctypes.c_void_p
        _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        _user32.FindWindowW.restype = ctypes.c_void_p
        _user32.PeekMessageW.argtypes = [
            ctypes.POINTER(_WinMSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        _user32.PeekMessageW.restype = wintypes.BOOL
        _user32.TranslateMessage.argtypes = [ctypes.POINTER(_WinMSG)]
        _user32.TranslateMessage.restype = wintypes.BOOL
        _user32.DispatchMessageW.argtypes = [ctypes.POINTER(_WinMSG)]
        _user32.DispatchMessageW.restype = ctypes.c_longlong
    except (OSError, AttributeError):
        _user32 = None
        _WinMSG = None


_VK_W, _VK_A, _VK_S, _VK_D = 0x57, 0x41, 0x53, 0x44
_VK_I, _VK_J, _VK_K, _VK_L = 0x49, 0x4A, 0x4B, 0x4C
_VK_LEFT, _VK_UP, _VK_RIGHT, _VK_DOWN = 0x25, 0x26, 0x27, 0x28
_VK_SPACE, _VK_ESC, _VK_Q, _VK_H, _VK_M, _VK_X = 0x20, 0x1B, 0x51, 0x48, 0x4D, 0x58

# Клавиши Romeo на Win32: autorepeat в очереди окна OpenCV не нужен (состояние
# снимаем через GetAsyncKeyState). Иначе waitKeyEx разгребает сотни WM_KEYDOWN
# за кадр и видео «подвисает» при удержании WSAD.
_WIN_ROMEO_DRAIN_VKS: frozenset[int] = frozenset(
    {
        _VK_W,
        _VK_A,
        _VK_S,
        _VK_D,
        _VK_I,
        _VK_J,
        _VK_K,
        _VK_L,
        _VK_LEFT,
        _VK_UP,
        _VK_RIGHT,
        _VK_DOWN,
        _VK_SPACE,
        _VK_ESC,
        _VK_Q,
        _VK_H,
        _VK_M,
        _VK_X,
    }
)


def _pump_win32_gui_dropping_romeo_key_repeats(max_messages: int = 512) -> None:
    """Убрать из очереди GUI-потока автоповтор WM_KEYDOWN по клавишам Romeo.

    Управление читается через GetAsyncKeyState; дубли WM_KEYDOWN при удержании
    клавиши всё равно попадают в фокусное окно OpenCV и cv2.waitKeyEx вынужден
    их разгребать — отсюда лаг превью. Повторы (lParam bit 30 == 1) выкидываем,
    остальные сообщения обрабатываем как обычно.
    """
    if _user32 is None or _WinMSG is None:
        return
    import ctypes

    PM_REMOVE = 0x0001
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    msg = _WinMSG()
    for _ in range(max(1, int(max_messages))):
        if not _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            break
        if msg.message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            vk = int(msg.wParam) & 0xFF
            prev_down = (int(msg.lParam) >> 30) & 1
            if vk in _WIN_ROMEO_DRAIN_VKS and prev_down == 1:
                continue
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))


def _key_down_win(vk: int) -> bool:
    if _user32 is None:
        return False
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _read_input_state_win(window_hwnd: int | None) -> dict | None:
    """Снимок реального состояния клавиш (без autorepeat) на Windows.

    Возвращает None, если окно превью не в фокусе — тогда команды не идут
    (страховка: пользователь может работать в другом окне).
    """
    if _user32 is None:
        return None
    fg = _user32.GetForegroundWindow()
    if window_hwnd is None or not fg or fg != window_hwnd:
        return {"focus": False}

    drive_keys = {
        "forward": _key_down_win(_VK_W),
        "back": _key_down_win(_VK_S),
        "left": _key_down_win(_VK_A),
        "right": _key_down_win(_VK_D),
    }
    drive_dir: str | None = None
    for d in ("forward", "back", "left", "right"):
        if drive_keys[d]:
            drive_dir = d
            break

    turret_dir: str | None = None
    if _key_down_win(_VK_I) or _key_down_win(_VK_UP):
        turret_dir = "up"
    elif _key_down_win(_VK_K) or _key_down_win(_VK_DOWN):
        turret_dir = "down"
    elif _key_down_win(_VK_J) or _key_down_win(_VK_LEFT):
        turret_dir = "right"
    elif _key_down_win(_VK_L) or _key_down_win(_VK_RIGHT):
        turret_dir = "left"

    return {
        "focus": True,
        "drive_dir": drive_dir,
        "drive_keys": drive_keys,
        "turret_dir": turret_dir,
        "stop": _key_down_win(_VK_SPACE) or _key_down_win(_VK_X),
        "home": _key_down_win(_VK_H),
        "save": _key_down_win(_VK_M),
        "quit": _key_down_win(_VK_Q) or _key_down_win(_VK_ESC),
    }


_DRIVE_KEY_ORDER = ("forward", "back", "left", "right")


def _romeo_keyboard_state(
    romeo: "RomeoControlClient",
    pressed: dict | None,
    *,
    turret_mode: str,
    turret_smooth_v: float,
    drive_mode: str,
    drive_release_debounce_ms: float,
    turret_release_debounce_ms: float,
    state: dict,
) -> None:
    """Обработка реального состояния клавиш (Win32 GetAsyncKeyState).

    drive_mode:
      - "hold":   пока клавиша зажата — едет; отпустил — стоп. Стоп с дебаунсом
                  drive_release_debounce_ms (страхует от потерь фокуса/опроса).
      - "toggle": тап клавиши направления toggle'ит это направление. Тап того же
                  направления, в котором уже едем, → drive stop. Тап другого
                  направления → переключение на новое (без промежуточного стопа).
                  Удержание игнорируется. На «провод» уходит ровно один пакет
                  на каждый тап.

    Башня (turret) всегда работает в hold-режиме — это удобнее для прицеливания.
    """
    now = time.monotonic()
    if pressed is None or not pressed.get("focus", False):
        new_drive = None
        new_turret = None
        new_stop = new_home = new_save = False
        drive_keys: dict[str, bool] = {d: False for d in _DRIVE_KEY_ORDER}
    else:
        new_drive = pressed.get("drive_dir")
        new_turret = pressed.get("turret_dir")
        new_stop = bool(pressed.get("stop"))
        new_home = bool(pressed.get("home"))
        new_save = bool(pressed.get("save"))
        raw = pressed.get("drive_keys") or {}
        drive_keys = {d: bool(raw.get(d, False)) for d in _DRIVE_KEY_ORDER}

    drive_grace_sec = max(0.0, float(drive_release_debounce_ms)) / 1000.0
    turret_grace_sec = max(0.0, float(turret_release_debounce_ms)) / 1000.0

    if drive_mode == "toggle":
        prev_keys: dict[str, bool] = state.setdefault(
            "drive_keys_prev", {d: False for d in _DRIVE_KEY_ORDER}
        )
        cur_drive = state.get("drive_dir")
        for d in _DRIVE_KEY_ORDER:
            if drive_keys[d] and not prev_keys.get(d, False):
                if cur_drive == d:
                    romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                    log.info("romeo cmd drive stop (toggle off, %s)", d)
                    cur_drive = None
                else:
                    romeo.send_json_cmd({"action": "drive", "dir": d})
                    log.info("romeo cmd drive %s (toggle on)", d)
                    cur_drive = d
        state["drive_dir"] = cur_drive
        state["drive_keys_prev"] = dict(drive_keys)
        state["drive_release_from"] = None
    else:
        cur_drive = state.get("drive_dir")
        if new_drive is not None:
            state["drive_release_from"] = None
            if cur_drive != new_drive:
                romeo.send_json_cmd({"action": "drive", "dir": new_drive})
                log.info("romeo cmd drive %s (key-down)", new_drive)
                state["drive_dir"] = new_drive
        elif cur_drive is not None:
            if drive_grace_sec <= 0.0:
                release_now = True
            else:
                g0 = state.get("drive_release_from")
                if g0 is None:
                    state["drive_release_from"] = now
                    release_now = False
                else:
                    release_now = now - float(g0) >= drive_grace_sec
            if release_now:
                romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                log.info("romeo cmd drive stop (key-up)")
                state["drive_dir"] = None
                state["drive_release_from"] = None
        else:
            state["drive_release_from"] = None
        state["drive_keys_prev"] = dict(drive_keys)

    if _romeo_turret_mode_is_smooth(turret_mode):
        cur_turret = state.get("last_turret_smooth")
        if new_turret is not None:
            state["turret_release_from"] = None
            if cur_turret != new_turret:
                cmd_sm: dict[str, object] = {"action": "turret_smooth", "dir": new_turret}
                if turret_smooth_v > 0.0:
                    cmd_sm["v"] = turret_smooth_v
                romeo.send_json_cmd(cmd_sm)
                log.debug("romeo cmd %s (queued, edge press)", cmd_sm)
                state["last_turret_smooth"] = new_turret
        elif cur_turret is not None:
            if turret_grace_sec <= 0.0:
                release_now = True
            else:
                g0 = state.get("turret_release_from")
                if g0 is None:
                    state["turret_release_from"] = now
                    release_now = False
                else:
                    release_now = now - float(g0) >= turret_grace_sec
            if release_now:
                romeo.send_json_cmd({"action": "turret_stop"})
                log.info("romeo cmd turret_stop (queued, edge release)")
                state["last_turret_smooth"] = None
                state["turret_release_from"] = None
        else:
            state["turret_release_from"] = None
        state["turret_dir"] = None
    else:
        cur_turret = state.get("turret_dir")
        if new_turret is not None:
            state["turret_release_from"] = None
            if cur_turret != new_turret:
                romeo.send_json_cmd({"action": "turret", "dir": new_turret})
                log.debug("romeo cmd turret %s (queued, edge press)", new_turret)
                state["turret_dir"] = new_turret
        elif cur_turret is not None:
            if turret_grace_sec <= 0.0:
                release_now = True
            else:
                g0 = state.get("turret_release_from")
                if g0 is None:
                    state["turret_release_from"] = now
                    release_now = False
                else:
                    release_now = now - float(g0) >= turret_grace_sec
            if release_now:
                state["turret_dir"] = None
                state["turret_release_from"] = None
        else:
            state["turret_release_from"] = None
        state["last_turret_smooth"] = None

    if new_stop and not state.get("oneshot_stop", False):
        romeo.send_json_cmd({"action": "drive", "dir": "stop"})
        log.info("romeo cmd stop (space/x) queued")
    state["oneshot_stop"] = new_stop
    if new_home and not state.get("oneshot_home", False):
        romeo.send_json_cmd({"action": "home"})
        log.info("romeo cmd home queued")
    state["oneshot_home"] = new_home
    if new_save and not state.get("oneshot_save", False):
        romeo.send_line("MS")
        log.info("romeo cmd MS queued")
    state["oneshot_save"] = new_save


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


def discover_pi(
    discover_port: int,
    token: str | None,
    timeout: float = 2.0,
    broadcast: str = "255.255.255.255",
) -> dict | None:
    """UDP-клиент discovery: шлёт broadcast и возвращает первый валидный hello.

    В ответе ожидаются поля: tcp (порт видео) и опц. control (порт управления).
    Адрес Pi берётся из source IP пакета.
    """
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp.settimeout(max(0.2, timeout))
    req = {"v": DISCOVERY_VERSION, "cmd": DISCOVERY_REQ}
    if token:
        req["token"] = token
    try:
        udp.sendto(
            json.dumps(req, separators=(",", ":")).encode("utf-8"),
            (broadcast, discover_port),
        )
        deadline = time.monotonic() + max(0.2, timeout)
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0.0:
                return None
            udp.settimeout(remain)
            try:
                data, addr = udp.recvfrom(4096)
            except socket.timeout:
                return None
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if msg.get("v") != DISCOVERY_VERSION or msg.get("cmd") != DISCOVERY_RSP:
                continue
            tcp_p = msg.get("tcp")
            if not isinstance(tcp_p, int):
                continue
            return {
                "host": addr[0],
                "tcp": int(tcp_p),
                "control": int(msg["control"]) if isinstance(msg.get("control"), int) else None,
                "name": msg.get("name"),
            }
    finally:
        udp.close()


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

    Не «стрим» — request/response: на каждую команду ждём подтверждение от Pi
    в пределах rx_timeout. Если ответа не пришло — тот же payload повторяется
    до max_attempts раз с пересозданием TCP при сетевых ошибках. Все команды
    Romeo идемпотентны (drive forward/back/stop/turret_smooth/turret_stop/...),
    поэтому повтор безопасен. Если все попытки исчерпаны — команда дропается
    с log.error, пользователь видит сообщение и может тапнуть ещё раз.

    Главный поток только enqueue'ит команды, фоновый воркер пишет/читает
    сокет и при необходимости перепосылает. Это нужно, чтобы цикл рисования
    кадров не блокировался сетью.

    На очередь действует coalescing идентичных payload'ов (например, дубль
    turret_smooth left при autorepeat): пока тот же байтовый payload «в работе»
    (в очереди или отправляется с ожиданием ответа), повторные enqueue
    игнорируются — на провод не уходит лишняя копия.
    """

    _MAX_QUEUE = 64

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 5.0,
        *,
        debug: bool = False,
        rx_timeout: float = 0.6,
        max_attempts: int = 3,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._debug = debug
        self._rx_timeout = max(0.05, float(rx_timeout))
        self._max_attempts = max(1, int(max_attempts))
        self._sock: socket.socket | None = None
        self._buf = b""
        self._sock_lock = threading.Lock()
        self._q: queue.Queue[bytes | object] = queue.Queue(maxsize=self._MAX_QUEUE)
        self._q_seen: set[bytes] = set()
        self._q_seen_lock = threading.Lock()
        self._stop_sentinel = object()
        self._writer: threading.Thread | None = None
        self._closing = False
        self._tx_count_lock = threading.Lock()
        self._tx_count = 0
        self._tx_window_t0 = time.monotonic()

    def _ensure_writer(self) -> None:
        if self._writer is None or not self._writer.is_alive():
            self._writer = threading.Thread(
                target=self._writer_loop, name="romeo-tx", daemon=True
            )
            self._writer.start()

    def try_connect(self, *, quiet: bool = False) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(max(1.0, self._connect_timeout))
        try:
            s.connect((self._host, self._port))
        except OSError as e:
            (log.debug if quiet else log.warning)(
                "Romeo control: подключение к %s:%s не удалось (%s).",
                self._host,
                self._port,
                e,
            )
            s.close()
            self._ensure_writer()
            return False
        s.settimeout(self._rx_timeout)
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = s
            self._buf = b""
        (log.debug if quiet else log.info)(
            "Romeo control: TCP подключён %s:%s (rx_timeout=%.2fс)",
            self._host,
            self._port,
            self._rx_timeout,
        )
        self._ensure_writer()
        return True

    def close(self) -> None:
        self._closing = True
        try:
            self._q.put_nowait(self._stop_sentinel)
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(self._stop_sentinel)
            except (queue.Empty, queue.Full):
                pass
        w = self._writer
        if w is not None:
            w.join(timeout=2.0)
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._buf = b""

    def _drop_socket(self) -> None:
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._buf = b""

    def is_connected(self) -> bool:
        with self._sock_lock:
            return self._sock is not None

    def _enqueue(self, payload: bytes) -> None:
        with self._q_seen_lock:
            if payload in self._q_seen:
                if self._debug:
                    log.debug("romeo TX coalesced (dup in queue): %s", payload[:200])
                return
            self._q_seen.add(payload)
        try:
            self._q.put_nowait(payload)
        except queue.Full:
            try:
                dropped = self._q.get_nowait()
                if isinstance(dropped, (bytes, bytearray)):
                    with self._q_seen_lock:
                        self._q_seen.discard(bytes(dropped))
                self._q.put_nowait(payload)
            except (queue.Empty, queue.Full):
                with self._q_seen_lock:
                    self._q_seen.discard(payload)

    def _writer_loop(self) -> None:
        while True:
            item = self._q.get()
            if item is self._stop_sentinel:
                return
            if not isinstance(item, (bytes, bytearray)):
                continue
            payload = bytes(item)
            try:
                self._send_and_read_one(payload)
                self._tick_tx_counter()
            finally:
                with self._q_seen_lock:
                    self._q_seen.discard(payload)

    def _tick_tx_counter(self) -> None:
        now = time.monotonic()
        with self._tx_count_lock:
            self._tx_count += 1
            elapsed = now - self._tx_window_t0
            if elapsed >= 5.0:
                rate = self._tx_count / elapsed if elapsed > 0 else 0.0
                if self._tx_count > 0:
                    log.info(
                        "romeo TX: %d команд за %.1f с (%.1f/с)",
                        self._tx_count,
                        elapsed,
                        rate,
                    )
                self._tx_count = 0
                self._tx_window_t0 = now

    def _send_and_read_one(self, payload: bytes) -> None:
        """Отправить команду и дождаться JSON-ответа. Повторяет до max_attempts."""
        for attempt in range(1, self._max_attempts + 1):
            if self._closing:
                return

            with self._sock_lock:
                need_reconnect = self._sock is None
            if need_reconnect:
                if not self.try_connect(quiet=True):
                    if attempt < self._max_attempts:
                        time.sleep(min(0.5, 0.1 * attempt))
                    continue

            first: bytes | None = None
            with self._sock_lock:
                sock = self._sock
                if sock is None:
                    continue
                try:
                    sock.settimeout(self._rx_timeout)
                    if self._debug:
                        log.info(
                            "romeo TX try=%d/%d (%d B): %s",
                            attempt, self._max_attempts, len(payload), payload[:500],
                        )
                    sock.sendall(payload)
                    while b"\n" not in self._buf:
                        chunk = sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("соединение закрыто")
                        self._buf += chunk
                    head, _, rest = self._buf.partition(b"\n")
                    self._buf = rest
                    first = head
                except socket.timeout:
                    log.warning(
                        "Romeo control: нет ответа за %.2fс (попытка %d/%d) на %s",
                        self._rx_timeout, attempt, self._max_attempts,
                        payload[:200].decode("utf-8", errors="replace").rstrip(),
                    )
                    try:
                        sock.close()
                    except OSError:
                        pass
                    self._sock = None
                    self._buf = b""
                except (OSError, ConnectionError) as e:
                    log.warning(
                        "Romeo control: сеть упала (попытка %d/%d): %s",
                        attempt, self._max_attempts, e,
                    )
                    try:
                        sock.close()
                    except OSError:
                        pass
                    self._sock = None
                    self._buf = b""

            if first is None:
                if attempt < self._max_attempts:
                    time.sleep(min(0.5, 0.1 * attempt))
                continue

            try:
                out = json.loads(first.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                log.warning("Romeo control: невалидный JSON ответ: %s raw=%r", e, first[:300])
                return
            if self._debug:
                log.info("romeo RX (try=%d): %s", attempt, out)
            elif isinstance(out, dict) and not out.get("ok", True):
                log.warning("Romeo control: ответ ok=false: %s", out)
            if attempt > 1:
                log.info(
                    "Romeo control: команда подтверждена с попытки %d: %s",
                    attempt, payload[:200].decode("utf-8", errors="replace").rstrip(),
                )
            return

        log.error(
            "Romeo control: команда отброшена после %d попыток (нет подтверждения): %s",
            self._max_attempts,
            payload[:200].decode("utf-8", errors="replace").rstrip(),
        )

    def send_line(self, line: str) -> None:
        payload = (line.rstrip("\n") + "\n").encode("utf-8")
        self._enqueue(payload)

    def send_json_cmd(self, obj: dict) -> None:
        self.send_line(json.dumps(obj, separators=(",", ":")))


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
    turret_mode: str,
    turret_smooth_v: float,
    turret_smooth_stop_grace_ms: float,
    drive_stop_grace_ms: float,
    drive_mode: str = "toggle",
    state: dict,
) -> None:
    """Обработка ввода через cv2.waitKeyEx (фолбэк, не‑Windows).

    drive_mode:
      - "hold":   зажал — едет, отпустил — стоп (с grace на release).
      - "toggle": тап клавиши направления toggle'ит её. Тап того же направления
                  → стоп. Тап другого → переключение направления.
    Башня всегда hold (smooth/step как настроено).
    """
    if romeo_debug and key != prev_key and key not in (-1, 255):
        log.info("keyboard: waitKeyEx=%s u16=0x%04x", key, _key_u16(key))

    now = time.monotonic()
    u = _key_u16(key)
    in_drive = u in ROME_DRIVE_BY_CH
    drive_grace_sec = max(0.0, float(drive_stop_grace_ms)) / 1000.0
    turret_grace_sec = max(0.0, float(turret_smooth_stop_grace_ms)) / 1000.0

    if drive_mode == "toggle":
        if in_drive and key != prev_key:
            ddir = str(ROME_DRIVE_BY_CH[u].get("dir", ""))
            cur = state.get("drive_dir")
            if cur == ddir:
                romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                log.info("romeo cmd drive stop (toggle off, %s)", ddir)
                state["drive_dir"] = None
            else:
                romeo.send_json_cmd({"action": "drive", "dir": ddir})
                log.info("romeo cmd drive %s (toggle on)", ddir)
                state["drive_dir"] = ddir
        state["drive_grace_from"] = None
    elif in_drive:
        ddir = str(ROME_DRIVE_BY_CH[u].get("dir", ""))
        state["drive_grace_from"] = None
        if state.get("drive_dir") != ddir:
            romeo.send_json_cmd({"action": "drive", "dir": ddir})
            log.info("romeo cmd drive %s (key-down)", ddir)
            state["drive_dir"] = ddir
    elif state.get("drive_dir") is not None:
        g0 = state.get("drive_grace_from")
        if drive_grace_sec <= 0.0:
            release_now = True
        elif g0 is None:
            state["drive_grace_from"] = now
            release_now = False
        else:
            release_now = now - float(g0) >= drive_grace_sec
        if release_now:
            romeo.send_json_cmd({"action": "drive", "dir": "stop"})
            log.info("romeo cmd drive stop (key-up)")
            state["drive_dir"] = None
            state["drive_grace_from"] = None
    else:
        state["drive_grace_from"] = None

    td = _turret_dir_from_u_key(u, key)

    if _romeo_turret_mode_is_smooth(turret_mode):
        prev_smooth = state.get("last_turret_smooth")
        if td is not None:
            state["turret_smooth_stop_grace_from"] = None
            if prev_smooth != td:
                cmd_sm: dict[str, object] = {"action": "turret_smooth", "dir": td}
                if turret_smooth_v > 0.0:
                    cmd_sm["v"] = turret_smooth_v
                romeo.send_json_cmd(cmd_sm)
                log.debug("romeo cmd %s (queued, edge press)", cmd_sm)
                state["last_turret_smooth"] = td
        elif prev_smooth is not None:
            g0 = state.get("turret_smooth_stop_grace_from")
            if turret_grace_sec <= 0.0:
                release_now = True
            elif g0 is None:
                state["turret_smooth_stop_grace_from"] = now
                release_now = False
            else:
                release_now = now - float(g0) >= turret_grace_sec
            if release_now:
                romeo.send_json_cmd({"action": "turret_stop"})
                log.debug("romeo cmd turret_stop (queued, edge release)")
                state["last_turret_smooth"] = None
                state["turret_smooth_stop_grace_from"] = None
        else:
            state["turret_smooth_stop_grace_from"] = None
        state["turret_dir"] = None
    else:
        if td is not None:
            state["turret_smooth_stop_grace_from"] = None
            if state.get("turret_dir") != td:
                romeo.send_json_cmd({"action": "turret", "dir": td})
                log.debug("romeo cmd turret %s (queued, edge press)", td)
                state["turret_dir"] = td
        elif state.get("turret_dir") is not None:
            g0 = state.get("turret_smooth_stop_grace_from")
            if turret_grace_sec <= 0.0:
                release_now = True
            elif g0 is None:
                state["turret_smooth_stop_grace_from"] = now
                release_now = False
            else:
                release_now = now - float(g0) >= turret_grace_sec
            if release_now:
                state["turret_dir"] = None
                state["turret_smooth_stop_grace_from"] = None
        else:
            state["turret_smooth_stop_grace_from"] = None
        state["last_turret_smooth"] = None

    if u in (ord(" "), ord("x"), ord("X")) and key != prev_key:
        romeo.send_json_cmd({"action": "drive", "dir": "stop"})
        log.info("romeo cmd stop (space/x) queued")
    if u in (ord("h"), ord("H"), ord("р"), ord("Р")) and key != prev_key:
        romeo.send_json_cmd({"action": "home"})
        log.info("romeo cmd home queued")
    if u in (ord("m"), ord("M"), ord("ь"), ord("Ь")) and key != prev_key:
        romeo.send_line("MS")
        log.info("romeo cmd MS queued")


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
    romeo_turret_smooth_stop_grace_ms: float = 80.0,
    romeo_drive_stop_grace_ms: float = 80.0,
    romeo_drive_mode: str = "hold",
    display_max_width: int | None = None,
    show_fps_title: bool = False,
) -> bool:
    """Возвращает True, если пользователь нажал Q/Esc (выход), False — если поток
    видео оборвался по сети/EOF (вызывающая сторона может переподключиться).
    """
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

    raw_q: queue.Queue = queue.Queue(maxsize=1)
    frame_q: queue.Queue = queue.Queue(maxsize=1)

    diag_lock = threading.Lock()
    diag_recv_n = 0
    diag_recv_bytes = 0
    diag_recv_drops = 0     # сколько JPEG было выкинуто из raw_q (decoder не успевал)
    diag_dec_n = 0
    diag_dec_drops = 0      # сколько кадров было выкинуто из frame_q (UI не успевал)
    diag_dec_time_sum = 0.0 # суммарное время imdecode+resize, для среднего
    diag_e2e_sum_ms = 0.0   # сумма end-to-end задержек recv→imshow
    diag_e2e_max_ms = 0.0
    diag_e2e_n = 0
    diag_recv_gap_max_ms = 0.0  # максимальный интервал между двумя соседними recv (джиттер)
    diag_recv_gap_min_ms = 1e9
    diag_recv_size_max = 0
    diag_t_recv_prev: list[float | None] = [None]  # mutable holder для замыкания
    diag_t0 = time.monotonic()

    def _put_drop_to(q: queue.Queue, item) -> int:
        """Вернёт 1, если пришлось выкинуть старый элемент, иначе 0."""
        try:
            q.put_nowait(item)
            return 0
        except queue.Full:
            pass
        dropped = 0
        try:
            q.get_nowait()
            dropped = 1
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass
        return dropped

    def recv_worker() -> None:
        """Только сеть: читаем JPEG-кадры из TCP и кладём «как есть» в raw_q (drop-old).

        Никаких imdecode/resize здесь — иначе всплеск нагрузки на декодер замедляет
        дренаж сокета, у Pi заполняется буфер отправки и видео визуально подвисает.
        """
        nonlocal diag_recv_n, diag_recv_bytes, diag_recv_drops
        nonlocal diag_recv_gap_max_ms, diag_recv_gap_min_ms, diag_recv_size_max
        try:
            while True:
                jpeg = recv_jpeg_frame(sock)
                t_recv = time.monotonic()
                dropped = _put_drop_to(raw_q, (jpeg, t_recv))
                with diag_lock:
                    diag_recv_n += 1
                    diag_recv_bytes += len(jpeg)
                    diag_recv_drops += dropped
                    if len(jpeg) > diag_recv_size_max:
                        diag_recv_size_max = len(jpeg)
                    prev = diag_t_recv_prev[0]
                    if prev is not None:
                        gap_ms = (t_recv - prev) * 1000.0
                        if gap_ms > diag_recv_gap_max_ms:
                            diag_recv_gap_max_ms = gap_ms
                        if gap_ms < diag_recv_gap_min_ms:
                            diag_recv_gap_min_ms = gap_ms
                    diag_t_recv_prev[0] = t_recv
        except (EOFError, ValueError, OSError) as e:
            log.info("поток завершён: %s", e)
        finally:
            _put_drop_to(raw_q, _VIDEO_EOF)

    def decode_worker() -> None:
        """Только декод/ресайз последнего кадра. UI всегда видит самый свежий кадр."""
        nonlocal diag_dec_n, diag_dec_drops, diag_dec_time_sum
        try:
            while True:
                item = raw_q.get()
                if item is _VIDEO_EOF:
                    break
                jpeg, t_recv = item
                t0 = time.monotonic()
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    log.warning("кадр: imdecode не распознал JPEG")
                    continue
                disp = _display_resize_for_show(frame, display_max_width)
                t_dec = time.monotonic()
                dropped = _put_drop_to(frame_q, (disp, len(jpeg), t_recv))
                with diag_lock:
                    diag_dec_n += 1
                    diag_dec_drops += dropped
                    diag_dec_time_sum += (t_dec - t0)
        finally:
            _put_drop_to(frame_q, _VIDEO_EOF)

    th_rx = threading.Thread(target=recv_worker, name="video-rx", daemon=True)
    th_dec = threading.Thread(target=decode_worker, name="video-dec", daemon=True)
    th_rx.start()
    th_dec.start()

    n = 0
    prev_key = -1
    last_np = None
    log_fps_n = 0
    log_fps_t0 = time.monotonic()
    title_fps_n = 0
    title_fps_t0 = time.monotonic()
    repeat_state: dict = {
        "turret_dir": None,
        "drive_dir": None,
        "drive_grace_from": None,
        "drive_release_from": None,
        "last_turret_smooth": None,
        "turret_smooth_stop_grace_from": None,
        "turret_release_from": None,
        "oneshot_stop": False,
        "oneshot_home": False,
        "oneshot_save": False,
    }

    use_win_keys = _IS_WIN and _user32 is not None
    win_hwnd: int | None = None
    if use_win_keys:
        log.info("Ввод: Win32 GetAsyncKeyState (без autorepeat). Окно %r должно быть в фокусе.", window)

    def _quit_keypress() -> bool:
        if use_win_keys:
            pressed = _read_input_state_win(win_hwnd)
            return bool(pressed and pressed.get("focus") and pressed.get("quit"))
        return False

    user_quit = False
    try:
        while True:
            try:
                item = frame_q.get(timeout=0.05)
            except queue.Empty:
                item = None
            if item is _VIDEO_EOF:
                break
            if isinstance(item, tuple):
                frame, jpeg_len, t_recv = item
                last_np = frame
                n += 1
                if n == 1:
                    log.info("первый кадр получен (~%d байт JPEG)", jpeg_len)
                log_fps_n += 1
                title_fps_n += 1
                now = time.monotonic()
                e2e_ms = (now - t_recv) * 1000.0
                with diag_lock:
                    diag_e2e_sum_ms += e2e_ms
                    if e2e_ms > diag_e2e_max_ms:
                        diag_e2e_max_ms = e2e_ms
                    diag_e2e_n += 1
                if now - log_fps_t0 >= 2.0 and log_fps_n > 0:
                    elapsed = now - log_fps_t0
                    with diag_lock:
                        rn = diag_recv_n
                        rb = diag_recv_bytes
                        rdrop = diag_recv_drops
                        dn = diag_dec_n
                        ddrop = diag_dec_drops
                        dts = diag_dec_time_sum
                        e2e_avg = diag_e2e_sum_ms / max(1, diag_e2e_n)
                        e2e_max = diag_e2e_max_ms
                        gmax = diag_recv_gap_max_ms
                        gmin = diag_recv_gap_min_ms if diag_recv_gap_min_ms < 1e8 else 0.0
                        smax = diag_recv_size_max
                        diag_recv_n = 0
                        diag_recv_bytes = 0
                        diag_recv_drops = 0
                        diag_dec_n = 0
                        diag_dec_drops = 0
                        diag_dec_time_sum = 0.0
                        diag_e2e_sum_ms = 0.0
                        diag_e2e_max_ms = 0.0
                        diag_e2e_n = 0
                        diag_recv_gap_max_ms = 0.0
                        diag_recv_gap_min_ms = 1e9
                        diag_recv_size_max = 0
                    avg_kb = (rb / max(1, rn)) / 1024.0
                    bw_mbps = (rb * 8 / 1_000_000.0) / elapsed if elapsed > 0 else 0.0
                    dec_avg_ms = (dts / max(1, dn)) * 1000.0
                    log.info(
                        "видео: net=%.1f кадр/с (%.0f КБ ср., max %d КБ, %.1f Мбит/с) | "
                        "gap recv: %.0f..%.0f мс | "
                        "decode=%.1f кадр/с (avg %.1f мс) | "
                        "drops raw=%d frame=%d | latency recv→show: avg %.0f мс, max %.0f мс",
                        rn / elapsed if elapsed > 0 else 0.0,
                        avg_kb,
                        smax // 1024,
                        bw_mbps,
                        gmin,
                        gmax,
                        dn / elapsed if elapsed > 0 else 0.0,
                        dec_avg_ms,
                        rdrop,
                        ddrop,
                        e2e_avg,
                        e2e_max,
                    )
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
                if use_win_keys:
                    _pump_win32_gui_dropping_romeo_key_repeats()
                key = cv2.waitKeyEx(10)
                u = _key_u16(key)
                quit_now = (
                    u in (ord("q"), ord("Q"), ord("й"), ord("Й"), 27)
                    or (key & 0xFF) == 27
                    or _quit_keypress()
                )
                if quit_now:
                    log.info("выход по клавише")
                    if romeo is not None:
                        romeo.send_json_cmd({"action": "turret_stop"})
                        romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                    user_quit = True
                    break
                if romeo is not None:
                    if use_win_keys:
                        if not win_hwnd:
                            win_hwnd = _user32.FindWindowW(None, window)
                        pressed = _read_input_state_win(win_hwnd)
                        _romeo_keyboard_state(
                            romeo,
                            pressed,
                            turret_mode=romeo_turret_mode,
                            turret_smooth_v=romeo_turret_smooth_v,
                            drive_mode=romeo_drive_mode,
                            drive_release_debounce_ms=romeo_drive_stop_grace_ms,
                            turret_release_debounce_ms=romeo_turret_smooth_stop_grace_ms,
                            state=repeat_state,
                        )
                    else:
                        _romeo_keyboard(
                            romeo,
                            key,
                            prev_key,
                            romeo_debug=romeo_debug,
                            turret_mode=romeo_turret_mode,
                            turret_smooth_v=romeo_turret_smooth_v,
                            turret_smooth_stop_grace_ms=romeo_turret_smooth_stop_grace_ms,
                            drive_stop_grace_ms=romeo_drive_stop_grace_ms,
                            drive_mode=romeo_drive_mode,
                            state=repeat_state,
                        )
                prev_key = key
                continue

            cv2.imshow(window, last_np)
            if n == 1:
                _try_raise_preview_window(window)
            if use_win_keys:
                _pump_win32_gui_dropping_romeo_key_repeats()
            key = cv2.waitKeyEx(1)
            u = _key_u16(key)
            quit_now = (
                u in (ord("q"), ord("Q"), ord("й"), ord("Й"), 27)
                or (key & 0xFF) == 27
                or _quit_keypress()
            )
            if quit_now:
                log.info("выход по клавише")
                if romeo is not None:
                    romeo.send_json_cmd({"action": "turret_stop"})
                    romeo.send_json_cmd({"action": "drive", "dir": "stop"})
                user_quit = True
                break

            if romeo is not None:
                if use_win_keys:
                    if not win_hwnd:
                        win_hwnd = _user32.FindWindowW(None, window)
                    pressed = _read_input_state_win(win_hwnd)
                    _romeo_keyboard_state(
                        romeo,
                        pressed,
                        turret_mode=romeo_turret_mode,
                        turret_smooth_v=romeo_turret_smooth_v,
                        drive_mode=romeo_drive_mode,
                        drive_release_debounce_ms=romeo_drive_stop_grace_ms,
                        turret_release_debounce_ms=romeo_turret_smooth_stop_grace_ms,
                        state=repeat_state,
                    )
                else:
                    _romeo_keyboard(
                        romeo,
                        key,
                        prev_key,
                        romeo_debug=romeo_debug,
                        turret_mode=romeo_turret_mode,
                        turret_smooth_v=romeo_turret_smooth_v,
                        turret_smooth_stop_grace_ms=romeo_turret_smooth_stop_grace_ms,
                        drive_stop_grace_ms=romeo_drive_stop_grace_ms,
                        drive_mode=romeo_drive_mode,
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
        th_rx.join(timeout=4.0)
        _put_drop_to(raw_q, _VIDEO_EOF)
        th_dec.join(timeout=4.0)
    if user_quit:
        cv2.destroyAllWindows()
    return user_quit


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
    romeo_turret_smooth_stop_grace_ms: float,
    romeo_drive_stop_grace_ms: float,
    romeo_drive_mode: str,
    romeo_rx_timeout: float,
    romeo_max_attempts: int,
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
                    rx_timeout=romeo_rx_timeout,
                    max_attempts=romeo_max_attempts,
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
                    romeo_turret_smooth_stop_grace_ms=romeo_turret_smooth_stop_grace_ms,
                    romeo_drive_stop_grace_ms=romeo_drive_stop_grace_ms,
                    romeo_drive_mode=romeo_drive_mode,
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
    host: str | None,
    port: int,
    window: str,
    connect_timeout: float,
    romeo_control_port: int,
    romeo_host: str | None,
    romeo_connect_timeout: float,
    romeo_debug: bool,
    discover_port: int,
    discover_token: str | None,
    discover_timeout: float,
    romeo_turret_mode: str,
    romeo_turret_smooth_v: float,
    romeo_turret_smooth_stop_grace_ms: float,
    romeo_drive_stop_grace_ms: float,
    romeo_drive_mode: str,
    romeo_rx_timeout: float,
    romeo_max_attempts: int,
    reconnect: bool,
    reconnect_delay: float,
    display_max_width: int | None,
    show_fps_title: bool,
) -> None:
    if host is None:
        if discover_port <= 0:
            log.error("Не указан --host и --discover-port=0; нечего искать.")
            sys.exit(1)
        log.info("UDP discovery → broadcast :%s (таймаут %.1f с) ...", discover_port, discover_timeout)
        info = discover_pi(discover_port, discover_token, timeout=discover_timeout)
        if info is None:
            log.error("Discovery: ответа от Pi нет. Укажите --host явно.")
            sys.exit(1)
        host = info["host"]
        if port == 0:
            port = info["tcp"]
        if romeo_control_port < 0 and info.get("control"):
            romeo_control_port = int(info["control"])
        log.info("Discovery: Pi=%s tcp=%s control=%s name=%s",
                 host, info["tcp"], info.get("control"), info.get("name"))

    rhost = romeo_host or host
    romeo: RomeoControlClient | None = None
    if romeo_control_port > 0:
        romeo = RomeoControlClient(
            rhost,
            romeo_control_port,
            romeo_connect_timeout,
            debug=romeo_debug,
            rx_timeout=romeo_rx_timeout,
            max_attempts=romeo_max_attempts,
        )
        # writer thread всё равно стартует — он сам будет пробовать подключиться
        # каждый раз, когда есть команда в очереди и сокет упал.
        romeo.try_connect()

    miss = None
    if romeo is not None and not romeo.is_connected():
        miss = (
            f"Romeo: пока не удалось открыть второй TCP к {rhost}:{romeo_control_port}; "
            "клиент будет повторять подключение в фоне."
        )

    attempt = 0
    try:
        while True:
            attempt += 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(max(1.0, connect_timeout))
            log.info(
                "TCP видео: подключение к %s:%s (таймаут %.0f с, попытка %d)...",
                host, port, connect_timeout, attempt,
            )
            try:
                sock.connect((host, port))
            except OSError as e:
                log.error("TCP видео: не удалось подключиться: %s", e)
                sock.close()
                if attempt == 1 and host.startswith("10.42."):
                    log.error(
                        "Адрес 10.42.x — сеть точки Pi. Нужно подключить ПК к Wi‑Fi SSID малинки "
                        "(Ethernet к роутеру не подставляет эту подсеть)."
                    )
                if not reconnect:
                    sys.exit(1)
                log.info("Повторная попытка через %.1f с... (Ctrl+C — отмена)", reconnect_delay)
                try:
                    time.sleep(reconnect_delay)
                except KeyboardInterrupt:
                    return
                continue
            sock.settimeout(None)
            log.info("TCP видео: соединение установлено")

            try:
                user_quit = stream_to_window(
                    sock,
                    window,
                    romeo,
                    romeo_debug=romeo_debug,
                    romeo_missing_warn=miss,
                    romeo_turret_mode=romeo_turret_mode,
                    romeo_turret_smooth_v=romeo_turret_smooth_v,
                    romeo_turret_smooth_stop_grace_ms=romeo_turret_smooth_stop_grace_ms,
                    romeo_drive_stop_grace_ms=romeo_drive_stop_grace_ms,
                    romeo_drive_mode=romeo_drive_mode,
                    display_max_width=display_max_width,
                    show_fps_title=show_fps_title,
                )
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

            miss = None  # повторно не пугаем тем же предупреждением

            if user_quit:
                return
            if not reconnect:
                return

            log.warning(
                "Связь с Pi разорвана. Авто-переподключение через %.1f с... "
                "(F5/Q в окне видео — выход; Ctrl+C — выход в консоли)",
                reconnect_delay,
            )
            try:
                time.sleep(reconnect_delay)
            except KeyboardInterrupt:
                return
    finally:
        if romeo is not None:
            romeo.close()


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
        "--romeo-turret-stop-grace-ms",
        type=float,
        default=80.0,
        help="Задержка перед turret_stop при «дыре» в опросе клавиатуры/потере фокуса окна (0 = сразу).",
    )
    p_listen.add_argument(
        "--romeo-drive-stop-grace-ms",
        type=float,
        default=80.0,
        help="Только для --romeo-drive-mode hold: задержка перед drive stop при «дыре» в опросе (0 = сразу).",
    )
    p_listen.add_argument(
        "--romeo-drive-mode",
        choices=["hold", "toggle"],
        default="hold",
        help="hold (по умолчанию): зажал W — поехал, отпустил — стоп; ровно один пакет на key-down "
             "и один на key-up, без повторов во время удержания. "
             "toggle: тап W включает/выключает движение, тап S переключает направление.",
    )
    p_listen.add_argument(
        "--romeo-rx-timeout",
        type=float,
        default=0.6,
        help="Сколько ждать JSON-подтверждение Pi на каждую команду перед повтором, с (по умолчанию 0.6).",
    )
    p_listen.add_argument(
        "--romeo-max-attempts",
        type=int,
        default=3,
        help="Сколько раз повторять команду, если не пришло подтверждение (по умолчанию 3).",
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
    p_conn.add_argument(
        "--host",
        default=None,
        help="Pi IP or hostname (если не указан — будет UDP discovery)",
    )
    p_conn.add_argument("--port", type=int, default=5000, help="TCP port видео (0 = взять из discovery)")
    p_conn.add_argument(
        "--connect-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for TCP connect (avoid infinite hang)",
    )
    p_conn.add_argument(
        "--discover-port",
        type=int,
        default=DISCOVERY_PORT_DEFAULT,
        help="UDP discovery port (для авто-поиска при отсутствии --host; 0 — выкл.)",
    )
    p_conn.add_argument(
        "--discover-token",
        default=None,
        help="Discovery token (must match Pi --discover-token)",
    )
    p_conn.add_argument(
        "--discover-timeout",
        type=float,
        default=2.5,
        help="Seconds to wait for discovery hello",
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
        "--romeo-turret-stop-grace-ms",
        type=float,
        default=80.0,
        help="Задержка перед turret_stop при «дыре» в опросе клавиатуры/потере фокуса окна (0 = сразу).",
    )
    p_conn.add_argument(
        "--romeo-drive-stop-grace-ms",
        type=float,
        default=80.0,
        help="Только для --romeo-drive-mode hold: задержка перед drive stop при «дыре» в опросе (0 = сразу).",
    )
    p_conn.add_argument(
        "--romeo-drive-mode",
        choices=["hold", "toggle"],
        default="hold",
        help="hold (по умолчанию): зажал W — поехал, отпустил — стоп; ровно один пакет на key-down "
             "и один на key-up, без повторов во время удержания. "
             "toggle: тап W включает/выключает движение, тап S переключает направление.",
    )
    p_conn.add_argument(
        "--romeo-rx-timeout",
        type=float,
        default=0.6,
        help="Сколько ждать JSON-подтверждение Pi на каждую команду перед повтором, с (по умолчанию 0.6).",
    )
    p_conn.add_argument(
        "--romeo-max-attempts",
        type=int,
        default=3,
        help="Сколько раз повторять команду, если не пришло подтверждение (по умолчанию 3).",
    )
    p_conn.add_argument(
        "--no-reconnect",
        dest="reconnect",
        action="store_false",
        help="Отключить авто-переподключение к видео-TCP при обрыве (по умолчанию включено).",
    )
    p_conn.set_defaults(reconnect=True)
    p_conn.add_argument(
        "--reconnect-delay",
        type=float,
        default=2.0,
        help="Пауза между попытками переподключения видео, с (по умолчанию 2.0).",
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
            args.romeo_turret_stop_grace_ms,
            args.romeo_drive_stop_grace_ms,
            args.romeo_drive_mode,
            args.romeo_rx_timeout,
            args.romeo_max_attempts,
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
            args.discover_port,
            args.discover_token,
            args.discover_timeout,
            args.romeo_turret_mode,
            args.romeo_turret_smooth_v,
            args.romeo_turret_stop_grace_ms,
            args.romeo_drive_stop_grace_ms,
            args.romeo_drive_mode,
            args.romeo_rx_timeout,
            args.romeo_max_attempts,
            args.reconnect,
            args.reconnect_delay,
            dmw,
            args.show_fps,
        )


if __name__ == "__main__":
    main()
