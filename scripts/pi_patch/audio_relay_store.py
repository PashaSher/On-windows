"""In-memory OGG audio relay: Pi publish → browser listen (local on operator-proxy)."""

from __future__ import annotations

import queue
import threading
import time
from typing import Iterator

_MAX_LISTENERS = 8
_HEARTBEAT_SEC = 5.0
_BOOTSTRAP_MAX = 4096


class AudioRelayStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[str, list[queue.Queue[bytes | None]]] = {}
        self._publishers: dict[str, float] = {}
        self._bootstrap: dict[str, bytes] = {}

    def register_listener(self, room: str) -> queue.Queue[bytes | None]:
        q: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        with self._lock:
            lst = self._listeners.setdefault(room, [])
            if len(lst) >= _MAX_LISTENERS:
                old = lst.pop(0)
                try:
                    old.put_nowait(None)
                except queue.Full:
                    pass
            lst.append(q)
            prelude = self._bootstrap.get(room, b"")
        if prelude:
            try:
                q.put_nowait(prelude)
            except queue.Full:
                pass
        return q

    def unregister_listener(self, room: str, q: queue.Queue[bytes | None]) -> None:
        with self._lock:
            lst = self._listeners.get(room, [])
            if q in lst:
                lst.remove(q)
            if not lst:
                self._listeners.pop(room, None)

    def mark_publisher(self, room: str, active: bool) -> None:
        with self._lock:
            if active:
                self._publishers[room] = time.monotonic()
                self._bootstrap[room] = b""
            else:
                self._publishers.pop(room, None)
                for q in self._listeners.get(room, []):
                    try:
                        q.put_nowait(None)
                    except queue.Full:
                        pass

    def publisher_active(self, room: str) -> bool:
        with self._lock:
            return room in self._publishers

    def publish(self, room: str, chunk: bytes) -> int:
        if not chunk:
            return 0
        with self._lock:
            boot = self._bootstrap.get(room, b"")
            if len(boot) < _BOOTSTRAP_MAX:
                self._bootstrap[room] = boot + chunk[: _BOOTSTRAP_MAX - len(boot)]
            listeners = list(self._listeners.get(room, []))
        sent = 0
        for q in listeners:
            try:
                q.put_nowait(chunk)
                sent += 1
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(chunk)
                    sent += 1
                except queue.Full:
                    pass
        return sent

    def iter_listener(self, room: str, q: queue.Queue[bytes | None]) -> Iterator[bytes]:
        while True:
            try:
                item = q.get(timeout=_HEARTBEAT_SEC)
            except queue.Empty:
                continue
            if item is None:
                break
            yield item


AUDIO_RELAY = AudioRelayStore()
# Browser mic → Pi speaker (отдельный канал, не WebRTC).
AUDIO_TALK = AudioRelayStore()
