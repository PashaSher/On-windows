"""Watchdog гусениц: если 10 с нет drive stop — Pi сам шлёт MS на Romeo."""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("camstream")

DRIVE_MOVE_DIRS = frozenset({"forward", "back", "left", "right"})

_WATCHDOG: DriveWatchdog | None = None


def _timeout_sec() -> float:
    raw = os.environ.get("ROMEO_DRIVE_WATCHDOG_SEC", "10").strip() or "10"
    try:
        sec = float(raw)
    except ValueError:
        sec = 10.0
    return max(1.0, sec)


class DriveWatchdog:
    def __init__(self, romeo_port: str, baud: int, *, timeout_sec: float | None = None) -> None:
        self._port = romeo_port
        self._baud = baud
        self._timeout = timeout_sec if timeout_sec is not None else _timeout_sec()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._moving = False

    def notify_command(self, obj: dict) -> None:
        if not isinstance(obj, dict):
            return
        action = str(obj.get("action") or "").strip().lower()
        if action != "drive":
            return
        direction = str(obj.get("dir") or "").strip().lower()
        if direction == "stop" or direction not in DRIVE_MOVE_DIRS:
            self._disarm("drive_stop")
            return
        self._arm(direction)

    def force_stop(self, reason: str = "force") -> None:
        with self._lock:
            moving = self._moving
            self._moving = False
            self._cancel_timer_locked()
        if moving:
            self._send_stop(reason)

    def _arm(self, direction: str) -> None:
        with self._lock:
            self._moving = True
            self._cancel_timer_locked()
            timer = threading.Timer(self._timeout, self._on_timeout)
            timer.daemon = True
            self._timer = timer
            timer.start()
            log.debug("drive watchdog: arm %.1fs dir=%s", self._timeout, direction)

    def _disarm(self, reason: str) -> None:
        with self._lock:
            was = self._moving
            self._moving = False
            self._cancel_timer_locked()
        if was:
            log.debug("drive watchdog: disarm (%s)", reason)

    def _on_timeout(self) -> None:
        with self._lock:
            if not self._moving:
                return
            self._moving = False
            self._cancel_timer_locked()
        self._send_stop("watchdog_timeout")

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _send_stop(self, reason: str) -> None:
        try:
            from rpi_tools.romeo_usb import romeo_exchange

            romeo_exchange(
                self._port,
                self._baud,
                "MS",
                append_lf=True,
                read_timeout=0.2,
                read_idle=0.02,
                log_send=True,
            )
            log.warning(
                "drive watchdog: отправлен MS без команды браузера (%s, timeout=%.1fs)",
                reason,
                self._timeout,
            )
        except Exception as exc:
            log.error("drive watchdog: MS failed (%s): %s", reason, exc)


def init_drive_watchdog(romeo_port: str, baud: int, *, timeout_sec: float | None = None) -> DriveWatchdog:
    global _WATCHDOG
    _WATCHDOG = DriveWatchdog(romeo_port, baud, timeout_sec=timeout_sec)
    log.info(
        "drive watchdog: enabled port=%s timeout=%.1fs",
        romeo_port,
        _WATCHDOG._timeout,
    )
    return _WATCHDOG


def get_drive_watchdog() -> DriveWatchdog | None:
    return _WATCHDOG


def notify_drive_command(obj: dict) -> None:
    wd = _WATCHDOG
    if wd is not None:
        wd.notify_command(obj)


def force_drive_stop(reason: str = "force") -> None:
    wd = _WATCHDOG
    if wd is not None:
        wd.force_stop(reason)
