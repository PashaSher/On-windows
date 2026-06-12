"""In-memory WebRTC signaling (замена Firebase RTDB) для одного VPS."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


def normalize_room_id(room: str) -> str:
    r = (room or "").strip().strip("/")
    if r.startswith("rooms/"):
        r = r[6:]
    return r or "pi-camera"


@dataclass
class RoomState:
    seq: int = 0
    offer: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    caller_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    callee_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    host: dict[str, Any] = field(
        default_factory=lambda: {
            "needOffer": False,
            "hostLaunchId": None,
            "hostSessionId": None,
            "status": "idle",
        }
    )
    _cand_seq: int = 0
    cond: threading.Condition = field(default_factory=threading.Condition)

    def bump(self) -> None:
        """Вызывать только под r.cond."""
        self.seq += 1
        self.cond.notify_all()

    def next_cand_id(self) -> str:
        self._cand_seq += 1
        return str(self._cand_seq)


class SignalStore:
    def __init__(self) -> None:
        self._rooms: dict[str, RoomState] = {}
        self._lock = threading.Lock()

    def _room(self, room_id: str) -> RoomState:
        rid = normalize_room_id(room_id)
        with self._lock:
            if rid not in self._rooms:
                self._rooms[rid] = RoomState()
            return self._rooms[rid]

    def snapshot(self, room_id: str) -> dict[str, Any]:
        r = self._room(room_id)
        with r.cond:
            return {
                "seq": r.seq,
                "offer": r.offer,
                "answer": r.answer,
                "host": dict(r.host),
                "callerCandidates": dict(r.caller_candidates),
                "calleeCandidates": dict(r.callee_candidates),
            }

    def wait_events(self, room_id: str, since: int, timeout: float = 25.0) -> dict[str, Any]:
        r = self._room(room_id)
        deadline = time.monotonic() + max(0.5, min(timeout, 60.0))
        with r.cond:
            while r.seq <= since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                r.cond.wait(timeout=remaining)
            return self.snapshot(room_id)

    def set_offer(self, room_id: str, data: dict[str, Any] | None) -> None:
        r = self._room(room_id)
        with r.cond:
            r.offer = data
            # Новый или снятый offer — старый answer/ICE Pi недействителен (иначе браузер ловит Failed).
            r.answer = None
            r.callee_candidates.clear()
            r.bump()

    def set_answer(self, room_id: str, data: dict[str, Any] | None) -> None:
        r = self._room(room_id)
        with r.cond:
            if data is not None and r.offer is None:
                return
            r.answer = data
            if data is None:
                r.callee_candidates.clear()
            r.bump()

    def set_host(self, room_id: str, patch: dict[str, Any]) -> None:
        r = self._room(room_id)
        with r.cond:
            r.host.update(patch)
            r.bump()

    def add_caller_candidate(self, room_id: str, cand: dict[str, Any]) -> str:
        r = self._room(room_id)
        with r.cond:
            cid = r.next_cand_id()
            r.caller_candidates[cid] = cand
            r.bump()
            return cid

    def add_callee_candidate(self, room_id: str, cand: dict[str, Any]) -> str:
        r = self._room(room_id)
        with r.cond:
            cid = r.next_cand_id()
            r.callee_candidates[cid] = cand
            r.bump()
            return cid

    def clear_caller_side(self, room_id: str) -> None:
        r = self._room(room_id)
        with r.cond:
            r.offer = None
            r.caller_candidates.clear()
            r.answer = None
            r.callee_candidates.clear()
            r.bump()

    def clear_callee_side(self, room_id: str) -> None:
        """Сброс answer/ICE Pi без удаления offer браузера."""
        r = self._room(room_id)
        with r.cond:
            r.answer = None
            r.callee_candidates.clear()
            r.bump()

    def clear_room(self, room_id: str) -> None:
        rid = normalize_room_id(room_id)
        with self._lock:
            if rid in self._rooms:
                del self._rooms[rid]


STORE = SignalStore()
