#!/usr/bin/env python3
"""
ПК: видео и управление Romeo — параллельно (разные потоки / отдельные процессы).

Рекомендуемый operator pipeline:

  python examples/pc_parallel_client.py --host 10.42.0.1 --video-transport udp --player gstreamer

Текущий рабочий hotspot-режим: MPEG-TS/H.264 over UDP. На Pi используется
`udp_h264`, а на ПК — `tsdemux -> h264parse -> avdec_h264`.

RTSP/H.264 оставлен как отдельный режим для стандартного RTSP endpoint.

Старый операторский RTP/UDP режим тоже оставлен:

  python examples/pc_parallel_client.py --host 10.42.0.1 --video-transport rtp --player gstreamer

Старый совместимый режим TCP MJPEG тоже оставлен:

  python examples/pc_parallel_client.py --host 10.42.0.1 --video-transport jpeg_tcp --window

Stress-тест управления:

  python examples/pc_parallel_client.py --host 10.42.0.1 --video-transport rtp --player gstreamer --stress

См. также docs/pc-remote-control.ru.md.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from receive_stream import (  # type: ignore
    RomeoControlClient,
    _IS_WIN,
    _read_input_state_win,
    _romeo_keyboard_state,
    _stop_romeo_motion,
    _user32,
    _wifi_signal_percent_win,
)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    left = n
    while left > 0:
        part = sock.recv(left)
        if not part:
            raise EOFError("TCP закрыт")
        chunks.append(part)
        left -= len(part)
    return b"".join(chunks)


def _normalize_gst_candidate(path: str | None) -> str | None:
    if not path:
        return None
    p = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
    if not p:
        return None
    if os.path.isdir(p):
        exe = "gst-launch-1.0.exe" if sys.platform == "win32" else "gst-launch-1.0"
        return os.path.join(p, exe)
    return p


def resolve_gst_launch_path(gst_launch_path: str | None = None) -> str | None:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        p = _normalize_gst_candidate(candidate)
        if not p:
            return
        k = os.path.normcase(os.path.normpath(p))
        if k in seen:
            return
        seen.add(k)
        candidates.append(p)

    add(gst_launch_path)
    add(os.environ.get("GST_LAUNCH_PATH"))
    add("gst-launch-1.0.exe" if sys.platform == "win32" else "gst-launch-1.0")
    add("gst-launch-1.0")

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        pf = os.environ.get("ProgramFiles")
        if local:
            add(os.path.join(local, "Programs", "gstreamer", "1.0", "msvc_x86_64", "bin", "gst-launch-1.0.exe"))
        if pf:
            add(os.path.join(pf, "gstreamer", "1.0", "msvc_x86_64", "bin", "gst-launch-1.0.exe"))
        add(r"C:\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe")

    for candidate in candidates:
        if os.path.basename(candidate) == candidate and not os.path.dirname(candidate):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        elif os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _normalize_gst_sink_name(name: str | None) -> str | None:
    if not name:
        return None
    sink = str(name).strip().strip('"').strip("'")
    return sink or None


def _resolve_gst_inspect_path(gst_launch_path: str) -> str | None:
    inspect_name = "gst-inspect-1.0.exe" if sys.platform == "win32" else "gst-inspect-1.0"
    gst_dir = os.path.dirname(gst_launch_path)
    if gst_dir:
        candidate = os.path.join(gst_dir, inspect_name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(inspect_name)


def _gst_element_exists(gst_launch_path: str, element_name: str) -> bool:
    gst_inspect = _resolve_gst_inspect_path(gst_launch_path)
    if not gst_inspect:
        return False
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run([gst_inspect, element_name], check=False, **kwargs)
    except OSError:
        return False
    return result.returncode == 0


def resolve_gst_video_sink(gst_launch_path: str, gst_video_sink: str | None = None) -> str:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        sink = _normalize_gst_sink_name(candidate)
        if not sink:
            return
        key = sink.casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(sink)

    add(gst_video_sink)
    add(os.environ.get("GST_VIDEO_SINK"))
    if sys.platform == "win32":
        # autovideosink can resolve to fakevideosink on some Windows installs.
        for sink in ("d3d11videosink", "d3d12videosink", "glimagesink", "d3dvideosink", "dshowvideosink"):
            add(sink)
    add("autovideosink")

    for sink in candidates:
        if _gst_element_exists(gst_launch_path, sink):
            return sink
    return "autovideosink"


def _normalize_rtsp_path(rtsp_path: str | None) -> str:
    path = str(rtsp_path or "camera").strip().strip('"').strip("'")
    if not path:
        path = "camera"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _build_rtsp_url(host: str, port: int, rtsp_path: str | None = None) -> str:
    return f"rtsp://{host}:{int(port)}{_normalize_rtsp_path(rtsp_path)}"


def _link_quality_style(wifi_pct: int | None) -> tuple[str, str, int]:
    if wifi_pct is None:
        return "Scanning", "#8a929c", 0
    if wifi_pct >= 82:
        return "Excellent", "#4fd18b", 4
    if wifi_pct >= 60:
        return "Good", "#68b9ff", 3
    if wifi_pct >= 35:
        return "Fair", "#ffbe55", 2
    return "Weak", "#ff6b7a", 1


def _battery_style(voltage: float | None, age_s: float) -> tuple[str, str, str]:
    if voltage is None or age_s > 60.0:
        return "-- V", "#8a929c", "#2b3139"
    if voltage < 16.0:
        return f"{voltage:.1f} V", "#ff707a", "#402126"
    if voltage < 18.0:
        return f"{voltage:.1f} V", "#ffbe55", "#40331f"
    return f"{voltage:.1f} V", "#4fd18b", "#203527"


class StatusHudPanel:
    def __init__(self, stop: threading.Event, romeo: RomeoControlClient | None) -> None:
        self._stop = stop
        self._romeo = romeo
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()

    def start(self) -> None:
        if not _IS_WIN:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="status-hud", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            return

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.95)
        try:
            root.wm_attributes("-toolwindow", True)
        except Exception:
            pass

        width, height = 236, 98
        sx = root.winfo_screenwidth()
        root.geometry(f"{width}x{height}+{max(12, sx - width - 20)}+18")

        canvas = tk.Canvas(
            root,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg="#11161c",
        )
        canvas.pack()

        wifi_pct: int | None = None
        battery_v: float | None = None
        battery_age_s: float = 1e9
        next_wifi_poll = 0.0
        next_battery_poll = 0.0

        def draw_round_rect(x1: int, y1: int, x2: int, y2: int, r: int, *, fill: str, outline: str = "") -> None:
            pts = [
                x1 + r, y1,
                x2 - r, y1,
                x2, y1,
                x2, y1 + r,
                x2, y2 - r,
                x2, y2,
                x2 - r, y2,
                x1 + r, y2,
                x1, y2,
                x1, y2 - r,
                x1, y1 + r,
                x1, y1,
            ]
            canvas.create_polygon(pts, smooth=True, splinesteps=24, fill=fill, outline=outline)

        def render() -> None:
            canvas.delete("all")
            draw_round_rect(0, 0, width, height, 18, fill="#11161c", outline="#27313b")

            quality_text, quality_color, bars_on = _link_quality_style(wifi_pct)
            batt_text, batt_color, batt_bg = _battery_style(battery_v, battery_age_s)
            signal_text = "--%" if wifi_pct is None else f"{wifi_pct}%"

            canvas.create_text(18, 18, text="LINK", fill="#8f98a3", anchor="w", font=("Segoe UI", 9, "bold"))
            canvas.create_text(18, 40, text=quality_text, fill="#eef3f8", anchor="w", font=("Segoe UI Semibold", 16))
            canvas.create_text(18, 62, text=signal_text, fill=quality_color, anchor="w", font=("Segoe UI Semibold", 11))

            bx = width - 74
            for idx in range(4):
                bar_h = 10 + idx * 8
                x1 = bx + idx * 11
                y1 = 60 - bar_h
                color = quality_color if idx < bars_on else "#2a3037"
                draw_round_rect(x1, y1, x1 + 8, 60, 3, fill=color)

            draw_round_rect(16, 72, width - 16, 92, 10, fill="#171d24")
            canvas.create_text(28, 82, text="BATTERY", fill="#8f98a3", anchor="w", font=("Segoe UI", 8, "bold"))
            draw_round_rect(width - 88, 75, width - 26, 89, 7, fill=batt_bg)
            draw_round_rect(width - 24, 79, width - 20, 85, 2, fill=batt_color)
            canvas.create_text(width - 56, 82, text=batt_text, fill=batt_color, anchor="c", font=("Segoe UI Semibold", 10))

        def tick() -> None:
            nonlocal wifi_pct, battery_v, battery_age_s, next_wifi_poll, next_battery_poll
            if self._closed.is_set() or self._stop.is_set():
                try:
                    root.destroy()
                except Exception:
                    pass
                return

            now = time.monotonic()
            if now >= next_wifi_poll:
                next_wifi_poll = now + 1.5
                wifi_pct = _wifi_signal_percent_win()

            if self._romeo is not None:
                if now >= next_battery_poll:
                    next_battery_poll = now + 10.0
                    if not self._romeo.is_connected():
                        self._romeo.try_connect(quiet=True)
                    self._romeo.send_json_cmd({"action": "adc_read"})
                battery_v, battery_age_s = self._romeo.get_battery_v()

            render()
            root.after(350, tick)

        root.deiconify()
        tick()
        try:
            root.mainloop()
        finally:
            self._closed.set()


def start_gstreamer_rtsp_player(
    host: str,
    port: int,
    *,
    rtsp_path: str | None = None,
    gst_launch_path: str | None = None,
    gst_video_sink: str | None = None,
    latency_ms: int = 60,
    quiet: bool = False,
) -> subprocess.Popen | None:
    gst = resolve_gst_launch_path(gst_launch_path)
    if gst is None:
        print(
            "[video-rtsp] gst-launch-1.0 не найден. Установите GStreamer или задайте --gst-launch-path / GST_LAUNCH_PATH.",
            file=sys.stderr,
            flush=True,
        )
        return None
    video_sink = resolve_gst_video_sink(gst, gst_video_sink)
    url = _build_rtsp_url(host, port, rtsp_path)
    cmd = [
        gst,
        *(["-q"] if quiet else ["-v"]),
        "rtspsrc",
        f"location={url}",
        "protocols=tcp",
        f"latency={max(0, int(latency_ms))}",
        "!",
        "rtph264depay",
        "!",
        "h264parse",
        "!",
        "avdec_h264",
        "!",
        "queue",
        "max-size-buffers=1",
        "leaky=downstream",
        "!",
        "videoconvert",
        "!",
        video_sink,
        "sync=false",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
    }
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        print(f"[video-rtsp] не удалось запустить GStreamer RTSP player: {e}", file=sys.stderr, flush=True)
        return None
    if not quiet:
        print(f"[video-rtsp] GStreamer RTSP player: {url} ({gst}, sink={video_sink})", flush=True)
    return proc


def start_gstreamer_udp_player(
    port: int,
    *,
    gst_launch_path: str | None = None,
    gst_video_sink: str | None = None,
    quiet: bool = False,
) -> subprocess.Popen | None:
    gst = resolve_gst_launch_path(gst_launch_path)
    if gst is None:
        print(
            "[video-udp] gst-launch-1.0 не найден. Установите GStreamer или задайте --gst-launch-path / GST_LAUNCH_PATH.",
            file=sys.stderr,
            flush=True,
        )
        return None
    video_sink = resolve_gst_video_sink(gst, gst_video_sink)
    cmd = [
        gst,
        *(["-q"] if quiet else ["-v"]),
        "udpsrc",
        f"port={int(port)}",
        "buffer-size=262144",
        "!",
        "tsdemux",
        "!",
        "h264parse",
        "!",
        "avdec_h264",
        "!",
        "queue",
        "max-size-buffers=1",
        "leaky=downstream",
        "!",
        "videoconvert",
        "!",
        video_sink,
        "sync=false",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
    }
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        print(f"[video-udp] не удалось запустить GStreamer MPEG-TS player: {e}", file=sys.stderr, flush=True)
        return None
    if not quiet:
        print(f"[video-udp] GStreamer MPEG-TS/H.264 приёмник на UDP:{port} ({gst}, sink={video_sink})", flush=True)
    return proc


def start_gstreamer_player(
    port: int,
    *,
    gst_launch_path: str | None = None,
    gst_video_sink: str | None = None,
    latency_ms: int = 10,
    quiet: bool = False,
) -> subprocess.Popen | None:
    gst = resolve_gst_launch_path(gst_launch_path)
    if gst is None:
        print(
            "[video-rtp] gst-launch-1.0 не найден. Установите GStreamer или задайте --gst-launch-path / GST_LAUNCH_PATH.",
            file=sys.stderr,
            flush=True,
        )
        return None
    video_sink = resolve_gst_video_sink(gst, gst_video_sink)

    caps = "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
    cmd = [
        gst,
        *(["-q"] if quiet else ["-v"]),
        "udpsrc",
        f"port={int(port)}",
        f"caps={caps}",
        "!",
        "rtpjitterbuffer",
        f"latency={max(0, int(latency_ms))}",
        "drop-on-latency=true",
        "!",
        "rtph264depay",
        "!",
        "h264parse",
        "!",
        "avdec_h264",
        "!",
        "queue",
        "max-size-buffers=1",
        "leaky=downstream",
        "!",
        "videoconvert",
        "!",
        video_sink,
        "sync=false",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        print(f"[video-rtp] не удалось запустить GStreamer: {e}", file=sys.stderr, flush=True)
        return None
    if not quiet:
        print(f"[video-rtp] GStreamer RTP приёмник на UDP:{port} ({gst}, sink={video_sink})", flush=True)
    return proc


def stop_external_player(proc: subprocess.Popen | None, *, quiet: bool = False) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass
    if not quiet:
        print("[video] внешний плеер остановлен", flush=True)


def video_receiver(
    host: str,
    port: int,
    stop: threading.Event,
    frame_queue: queue.Queue | None,
    quiet: bool,
) -> None:
    """Только читает legacy видео-поток (4 байта BE длина + JPEG)."""
    tag = "video"
    try:
        with socket.create_connection((host, port), timeout=15) as s:
            if not quiet:
                print(f"[{tag}] TCP {host}:{port}", flush=True)
            n_frames = 0
            n_bytes = 0
            t0 = time.monotonic()
            last_report = t0
            while not stop.is_set():
                try:
                    hdr = recv_exact(s, 4)
                except EOFError:
                    break
                (length,) = struct.unpack(">I", hdr)
                payload = recv_exact(s, length)
                n_frames += 1
                n_bytes += len(payload)
                if frame_queue is not None:
                    try:
                        frame_queue.put_nowait(payload)
                    except queue.Full:
                        try:
                            frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            frame_queue.put_nowait(payload)
                        except queue.Full:
                            pass
                now = time.monotonic()
                if not quiet and now - last_report >= 2.0:
                    dt = now - t0
                    fps = n_frames / dt if dt > 0 else 0.0
                    mb_s = (n_bytes / max(dt, 1e-9)) / (1024 * 1024)
                    print(f"[{tag}] кадров={n_frames}  ~{fps:.1f} FPS  ~{mb_s:.2f} MiB/s", flush=True)
                    last_report = now
    except OSError as e:
        if not stop.is_set():
            print(f"[{tag}] ошибка: {e}", file=sys.stderr, flush=True)
    finally:
        stop.set()
        if not quiet:
            print(f"[{tag}] поток остановлен", flush=True)


def _read_json_line(sock: socket.socket, buf: bytearray, deadline: float) -> tuple[dict | None, bool]:
    while time.monotonic() < deadline:
        i = buf.find(b"\n")
        if i >= 0:
            line = bytes(buf[:i]).decode("utf-8", errors="replace")
            del buf[: i + 1]
            try:
                return json.loads(line), False
            except json.JSONDecodeError:
                return None, False
        try:
            sock.settimeout(min(0.2, max(0.01, deadline - time.monotonic())))
            chunk = sock.recv(4096)
            if not chunk:
                return None, True
            buf.extend(chunk)
        except TimeoutError:
            continue
    return None, False


def _camera_control_help_text() -> str:
    return (
        "Camera shortcuts:\n"
        "  day | night | cloudy | indoor | sport | hdr | mono | auto\n"
        "  preset <name>\n"
        "  zoom+ | zoom- | zoom reset | zoom <factor>\n"
        "  cam status | cam presets | help\n"
        "\n"
        "Also supported:\n"
        "  raw JSON, e.g. {\"action\":\"camera_preset\",\"preset\":\"night\"}\n"
        "  legacy Romeo text/JSON, e.g. MF / MS / {\"action\":\"drive\",\"dir\":\"forward\"}"
    )


def _json_line(obj: dict[str, object]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _translate_control_command(raw: str) -> tuple[str | None, str | None]:
    text = raw.strip()
    if not text:
        return None, None

    lowered = text.casefold()
    preset_names = {"auto", "day", "cloudy", "indoor", "night", "sport", "hdr", "mono"}

    if lowered in {"help", "?"}:
        return None, _camera_control_help_text()

    if text.startswith("{"):
        return text, None

    if lowered in preset_names:
        return _json_line({"action": "camera_preset", "preset": lowered}), None

    if lowered.startswith("preset "):
        preset = text.split(None, 1)[1].strip().casefold()
        if preset not in preset_names:
            return None, f"Unknown preset: {preset}. Use `cam presets` or `help`."
        return _json_line({"action": "camera_preset", "preset": preset}), None

    if lowered == "zoom+":
        return _json_line({"action": "camera_zoom", "op": "in"}), None
    if lowered == "zoom-":
        return _json_line({"action": "camera_zoom", "op": "out"}), None
    if lowered == "zoom reset":
        return _json_line({"action": "camera_zoom", "op": "reset"}), None
    if lowered.startswith("zoom "):
        arg = text.split(None, 1)[1].strip()
        try:
            factor = float(arg)
        except ValueError:
            return None, f"Invalid zoom factor: {arg}"
        if factor <= 0.0:
            return None, "Zoom factor must be > 0"
        return _json_line({"action": "camera_zoom", "factor": factor}), None

    if lowered == "cam status":
        return _json_line({"action": "camera_status"}), None
    if lowered == "cam presets":
        return _json_line({"action": "camera_presets"}), None

    return text, None


_CAMERA_PRESET_HOTKEYS: tuple[tuple[str, str, str], ...] = (
    ("camera_preset_1", "day", "1"),
    ("camera_preset_2", "night", "2"),
    ("camera_preset_3", "cloudy", "3"),
    ("camera_preset_4", "indoor", "4"),
    ("camera_preset_5", "sport", "5"),
    ("camera_preset_6", "hdr", "6"),
    ("camera_preset_7", "mono", "7"),
    ("camera_preset_8", "auto", "8"),
)
_CAMERA_HOTKEY_RELEASE_DEBOUNCE_S = 0.12


def _handle_camera_hotkeys(
    romeo: RomeoControlClient,
    pressed: dict | None,
    state: dict,
    *,
    quiet: bool,
) -> None:
    focus_ok = bool(pressed and pressed.get("focus", False))
    now = time.monotonic()
    handled = state.setdefault("camera_hotkeys_handled", {})
    release_from = state.setdefault("camera_hotkeys_release_from", {})

    current: dict[str, bool] = {}
    for key_name, _preset, _label in _CAMERA_PRESET_HOTKEYS:
        current[key_name] = focus_ok and bool(pressed and pressed.get(key_name))
    current["camera_zoom_in"] = focus_ok and bool(pressed and pressed.get("camera_zoom_in"))
    current["camera_zoom_out"] = focus_ok and bool(pressed and pressed.get("camera_zoom_out"))
    current["camera_zoom_reset"] = focus_ok and bool(pressed and pressed.get("camera_zoom_reset"))
    current["camera_status"] = focus_ok and bool(pressed and pressed.get("camera_status"))

    for key_name, is_down in current.items():
        if is_down:
            release_from[key_name] = None
            continue
        released_at = release_from.get(key_name)
        if released_at is None:
            release_from[key_name] = now
            continue
        if now - float(released_at) >= _CAMERA_HOTKEY_RELEASE_DEBOUNCE_S:
            handled[key_name] = False

    def send_once(key_name: str, payload: dict[str, object], note: str) -> None:
        if not current[key_name] or bool(handled.get(key_name, False)):
            return
        romeo.send_json_cmd(payload)
        handled[key_name] = True
        if not quiet:
            print(f"[camera] {note}", flush=True)

    for key_name, preset, label in _CAMERA_PRESET_HOTKEYS:
        send_once(key_name, {"action": "camera_preset", "preset": preset}, f"preset {preset} [{label}]")

    for key_name, payload, note in (
        ("camera_zoom_in", {"action": "camera_zoom", "op": "in"}, "zoom in [+]"),
        ("camera_zoom_out", {"action": "camera_zoom", "op": "out"}, "zoom out [-]"),
        ("camera_zoom_reset", {"action": "camera_zoom", "op": "reset"}, "zoom reset [0]"),
        ("camera_status", {"action": "camera_status"}, "status requested [P]"),
    ):
        send_once(key_name, payload, note)


def control_interactive(host: str, port: int, stop: threading.Event, quiet: bool) -> None:
    tag = "control"
    try:
        s = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        print(f"[{tag}] нет соединения {host}:{port}: {e}", file=sys.stderr, flush=True)
        stop.set()
        return
    buf = bytearray()
    try:
        if not quiet:
            print(
                f"[{tag}] TCP {host}:{port} — camera shortcuts / JSON / Romeo raw. `help` — список, пусто — выход",
                flush=True,
            )
        while not stop.is_set():
            try:
                line = input("control> ")
            except EOFError:
                break
            raw = line.strip()
            if not raw:
                break
            payload, local_msg = _translate_control_command(raw)
            if local_msg:
                print(local_msg, flush=True)
            if payload is None:
                continue
            s.sendall((payload + "\n").encode("utf-8"))
            obj, closed = _read_json_line(s, buf, time.monotonic() + 5.0)
            if closed:
                break
            if obj is not None:
                print(f"[{tag}] {obj}", flush=True)
            elif not quiet:
                print(f"[{tag}] нет ответа за таймаут", flush=True)
    finally:
        stop.set()
        try:
            s.close()
        except OSError:
            pass
        if not quiet:
            print(f"[{tag}] выход", flush=True)


def keyboard_control_loop(
    host: str,
    port: int,
    stop: threading.Event,
    *,
    player_proc: subprocess.Popen | None,
    video_thread: threading.Thread | None,
    romeo: RomeoControlClient | None = None,
    quiet: bool,
) -> None:
    tag = "control"
    if not _IS_WIN or _user32 is None:
        print(
            f"[{tag}] глобальная клавиатура поддержана только на Windows; используйте --control-mode interactive",
            file=sys.stderr,
            flush=True,
        )
        stop.set()
        return

    owns_romeo = romeo is None
    if romeo is None:
        romeo = RomeoControlClient(
            host,
            port,
            connect_timeout=5.0,
            debug=False,
            rx_timeout=0.6,
            max_attempts=3,
        )
        romeo.try_connect()
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
    if not quiet:
        print(
            "[control] Win32 keyboard active: WASD drive, IJKL/стрелки turret, Space stop, H home, M save, "
            "1..8 presets, +/- zoom, 0 reset, P status, Q/Esc exit.",
            flush=True,
        )

    try:
        while not stop.is_set():
            if player_proc is not None and player_proc.poll() is not None:
                stop.set()
                break
            if video_thread is not None and not video_thread.is_alive():
                stop.set()
                break

            pressed = _read_input_state_win(None, require_focus=False)
            if pressed and pressed.get("quit"):
                if not quiet:
                    print(f"[{tag}] выход по клавише", flush=True)
                _stop_romeo_motion(romeo)
                stop.set()
                break

            _romeo_keyboard_state(
                romeo,
                pressed,
                turret_mode="smooth",
                turret_smooth_v=0.0,
                drive_mode="hold",
                drive_release_debounce_ms=80.0,
                turret_release_debounce_ms=80.0,
                state=repeat_state,
            )
            _handle_camera_hotkeys(romeo, pressed, repeat_state, quiet=quiet)
            time.sleep(0.02)
    finally:
        _stop_romeo_motion(romeo)
        if owns_romeo:
            romeo.close()
        if not quiet:
            print(f"[{tag}] выход", flush=True)


def stress_control_worker(
    host: str,
    port: int,
    stop: threading.Event,
    interval: float,
    seconds: float,
    quiet: bool,
) -> None:
    """Часто шлёт одну и ту же команду в отдельном потоке (проверка, что видео не блокируется)."""
    tag = "stress"
    try:
        s = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        print(f"[{tag}] {e}", file=sys.stderr, flush=True)
        return
    buf = bytearray()
    fwd = '{"action":"drive","dir":"forward"}\n'.encode("utf-8")
    stp = '{"action":"drive","dir":"stop"}\n'.encode("utf-8")
    t_end = time.monotonic() + max(0.5, seconds)
    n = 0
    try:
        while not stop.is_set() and time.monotonic() < t_end:
            s.sendall(fwd)
            _read_json_line(s, buf, time.monotonic() + 1.0)
            n += 1
            time.sleep(max(0.005, interval))
        s.sendall(stp)
        _read_json_line(s, buf, time.monotonic() + 2.0)
    finally:
        try:
            s.close()
        except OSError:
            pass
        if not quiet:
            print(f"[{tag}] отправлено ~{n} команд за {seconds:.1f} с", flush=True)


def display_loop(frame_queue: queue.Queue, stop: threading.Event) -> None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        print("[display] pip install opencv-python-headless numpy", file=sys.stderr, flush=True)
        return
    while not stop.is_set():
        try:
            jpeg = frame_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            continue
        cv2.imshow("Pi stream (parallel)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            stop.set()
            break
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="ПК: параллельно видео и управление Romeo")
    ap.add_argument("--host", required=True, help="IP Raspberry Pi")
    ap.add_argument(
        "--video-transport",
        choices=["udp", "rtsp", "rtp", "jpeg_tcp"],
        default="rtsp",
        help="Видео transport: udp (рабочий hotspot MPEG-TS/H.264), rtsp, rtp (legacy operator path) или legacy jpeg_tcp",
    )
    ap.add_argument(
        "--player",
        choices=["gstreamer", "none"],
        default="gstreamer",
        help="Для udp/rtsp/rtp: внешний video player. Сейчас поддержан gstreamer.",
    )
    ap.add_argument("--video-port", type=int, default=5000)
    ap.add_argument("--rtsp-port", type=int, default=8554)
    ap.add_argument("--rtsp-path", default="camera", help="RTSP mount path на Pi (по умолчанию /camera)")
    ap.add_argument("--control-port", type=int, default=5001)
    ap.add_argument(
        "--gst-launch-path",
        default=None,
        help="Путь к gst-launch-1.0(.exe) или к папке bin. Если не задан, используется GST_LAUNCH_PATH/PATH/типовые Windows-пути.",
    )
    ap.add_argument(
        "--gst-video-sink",
        default=None,
        help="GStreamer sink element. Если не задан, автоматически выбирается рабочий sink "
        "(на Windows приоритет у d3d11videosink, затем d3d12/gl/dshow и только потом autovideosink).",
    )
    ap.add_argument(
        "--rtp-latency-ms",
        type=int,
        default=10,
        help="Latency для rtpjitterbuffer в GStreamer (мс)",
    )
    ap.add_argument(
        "--rtsp-latency-ms",
        type=int,
        default=60,
        help="RTSP latency для rtspsrc в GStreamer (мс)",
    )
    ap.add_argument(
        "--control-mode",
        choices=["keyboard", "interactive"],
        default="keyboard" if sys.platform == "win32" else "interactive",
        help="Управление Romeo: keyboard (авто, Win32) или interactive (control> prompt)",
    )
    ap.add_argument("--no-control", action="store_true", help="только поток видео")
    ap.add_argument("--window", action="store_true", help="окно OpenCV (только legacy jpeg_tcp)")
    ap.add_argument("--stress", action="store_true", help="фоном слать drive forward с интервалом")
    ap.add_argument("--stress-interval", type=float, default=0.03, help="сек между командами")
    ap.add_argument("--stress-seconds", type=float, default=8.0, help="длительность stress")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    stop = threading.Event()
    frame_queue: queue.Queue | None = None
    vt: threading.Thread | None = None
    player_proc: subprocess.Popen | None = None
    shared_romeo: RomeoControlClient | None = None
    hud: StatusHudPanel | None = None

    if args.video_transport == "udp":
        if args.window:
            print("[video-udp] --window не используется: видео идёт через внешний GStreamer.", file=sys.stderr, flush=True)
        if args.player != "gstreamer":
            print("[video-udp] для UDP сейчас поддержан только --player gstreamer.", file=sys.stderr, flush=True)
            return 2
        player_proc = start_gstreamer_udp_player(
            args.video_port,
            gst_launch_path=args.gst_launch_path,
            gst_video_sink=args.gst_video_sink,
            quiet=args.quiet,
        )
        if player_proc is None:
            return 2
    elif args.video_transport == "rtsp":
        if args.window:
            print("[video-rtsp] --window не используется: видео идёт через внешний GStreamer.", file=sys.stderr, flush=True)
        if args.player != "gstreamer":
            print("[video-rtsp] для RTSP сейчас поддержан только --player gstreamer.", file=sys.stderr, flush=True)
            return 2
        player_proc = start_gstreamer_rtsp_player(
            args.host,
            args.rtsp_port,
            rtsp_path=args.rtsp_path,
            gst_launch_path=args.gst_launch_path,
            gst_video_sink=args.gst_video_sink,
            latency_ms=args.rtsp_latency_ms,
            quiet=args.quiet,
        )
        if player_proc is None:
            return 2
    elif args.video_transport == "rtp":
        if args.window:
            print("[video-rtp] --window не используется: видео идёт через внешний GStreamer.", file=sys.stderr, flush=True)
        if args.player != "gstreamer":
            print("[video-rtp] для RTP сейчас поддержан только --player gstreamer.", file=sys.stderr, flush=True)
            return 2
        player_proc = start_gstreamer_player(
            args.video_port,
            gst_launch_path=args.gst_launch_path,
            gst_video_sink=args.gst_video_sink,
            latency_ms=args.rtp_latency_ms,
            quiet=args.quiet,
        )
        if player_proc is None:
            return 2
    else:
        frame_queue = queue.Queue(maxsize=2) if args.window else None
        vt = threading.Thread(
            target=video_receiver,
            args=(args.host, args.video_port, stop, frame_queue, args.quiet),
            name="video-tcp",
            daemon=True,
        )
        vt.start()

    disp_t: threading.Thread | None = None
    if args.video_transport == "jpeg_tcp" and args.window and frame_queue is not None:
        disp_t = threading.Thread(
            target=display_loop,
            args=(frame_queue, stop),
            name="display",
            daemon=True,
        )
        disp_t.start()

    stress_t: threading.Thread | None = None
    if args.stress:
        stress_t = threading.Thread(
            target=stress_control_worker,
            args=(
                args.host,
                args.control_port,
                stop,
                args.stress_interval,
                args.stress_seconds,
                args.quiet,
            ),
            name="stress-control",
            daemon=True,
        )
        stress_t.start()

    if not args.no_control and args.control_mode == "keyboard":
        shared_romeo = RomeoControlClient(
            args.host,
            args.control_port,
            connect_timeout=5.0,
            debug=False,
            rx_timeout=0.6,
            max_attempts=3,
        )
        shared_romeo.try_connect(quiet=True)

    if (
        not args.quiet
        and args.video_transport == "udp"
        and args.player == "gstreamer"
        and _IS_WIN
    ):
        hud = StatusHudPanel(stop, shared_romeo)
        hud.start()

    try:
        if args.no_control:
            if not args.quiet:
                print("Режим только видео. Ctrl+C — выход.", flush=True)
            while not stop.is_set():
                if player_proc is not None and player_proc.poll() is not None:
                    stop.set()
                    break
                if vt is not None and not vt.is_alive():
                    stop.set()
                    break
                time.sleep(0.25)
        elif args.stress:
            if not args.quiet:
                print(
                    f"Stress управления ~{args.stress_seconds:.0f} с (отдельный поток), видео — параллельно. Ctrl+C — выход.",
                    flush=True,
                )
            if stress_t is not None:
                stress_t.join(timeout=max(5.0, args.stress_seconds + 3.0))
            if not args.quiet:
                print("Stress завершён (роботу отправлен stop). Видео продолжается. Ctrl+C — выход.", flush=True)
            while not stop.is_set():
                if player_proc is not None and player_proc.poll() is not None:
                    stop.set()
                    break
                if vt is not None and not vt.is_alive():
                    stop.set()
                    break
                time.sleep(0.25)
        else:
            if args.control_mode == "keyboard":
                keyboard_control_loop(
                    args.host,
                    args.control_port,
                    stop,
                    player_proc=player_proc,
                    video_thread=vt,
                    romeo=shared_romeo,
                    quiet=args.quiet,
                )
            else:
                control_interactive(args.host, args.control_port, stop, args.quiet)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        if hud is not None:
            hud.stop()
        if vt is not None:
            vt.join(timeout=3.0)
        if shared_romeo is not None:
            shared_romeo.close()
        stop_external_player(player_proc, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
