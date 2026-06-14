"""WebRTC signaling via VPS HTTP API (replaces Firebase RTDB)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

log = logging.getLogger("camstream.webrtc")

_IDLE_POLL_SEC = 60.0
_ACTIVE_POLL_SEC = 20.0


def normalize_room(room: str) -> str:
    r = (room or "").strip().strip("/")
    if r.startswith("rooms/"):
        r = r[6:]
    return r or "pi-camera"


class _VpsHttp:
    def __init__(self, api_base: str, room: str, ice_token: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.room = normalize_room(room)
        self.ice_token = (ice_token or "").strip()
        self._since = 0

    def _headers(self, *, auth: bool = False) -> dict[str, str]:
        hdrs = {"Content-Type": "application/json"}
        if auth and self.ice_token:
            hdrs["Authorization"] = f"Bearer {self.ice_token}"
        return hdrs

    def _url(self, *parts: str) -> str:
        tail = "/".join(parts)
        return f"{self.api_base}/rooms/{self.room}" + (f"/{tail}" if tail else "")

    def _request(
        self,
        method: str,
        path_parts: tuple[str, ...] = (),
        body: dict | None = None,
        *,
        auth: bool = False,
        timeout_sec: float = 35.0,
    ) -> Any:
        url = self._url(*path_parts)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(auth=auth), method=method)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

    def clear_room(self) -> None:
        self._request("DELETE", (), auth=True)

    def clear_caller_side(self, *, timeout_sec: float = 3.0) -> bool:
        """Сбросить offer и ICE браузера (X-Clear: caller)."""
        url = self._url()
        req = urllib.request.Request(
            url,
            method="DELETE",
            headers={**self._headers(auth=True), "X-Clear": "caller"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("VPS: DELETE caller %s — %s (продолжаем)", self.room, exc)
            return False

    def clear_callee_side(self, *, timeout_sec: float = 3.0) -> bool:
        url = self._url()
        req = urllib.request.Request(
            url,
            method="DELETE",
            headers={**self._headers(auth=True), "X-Clear": "callee"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("VPS: DELETE callee %s — %s (продолжаем)", self.room, exc)
            return False

    def fetch_room(self) -> dict[str, Any] | None:
        try:
            return self._request("GET", (), auth=True, timeout_sec=8.0)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

    def wait_events(self, timeout: float = 25.0) -> dict[str, Any]:
        url = (
            f"{self._url('events')}?since={self._since}"
            f"&timeout={max(1, min(int(timeout), 60))}"
        )
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        http_wait = max(timeout + 12.0, 20.0)
        with urllib.request.urlopen(req, timeout=http_wait) as resp:
            ev = json.loads(resp.read().decode("utf-8"))
        self._since = int(ev.get("seq", self._since))
        return ev

    def set_host(self, patch: dict[str, Any]) -> None:
        self._request("PUT", ("host",), patch, auth=True)

    def put_answer(self, answer: dict[str, Any]) -> None:
        self._request("PUT", ("answer",), answer, auth=True)

    def post_callee_candidate(self, cand: dict[str, Any]) -> None:
        self._request("POST", ("callee-candidates",), cand, auth=True)


class VpsSignaling:
    """Async API compatible with FirebaseSignaling for webrtc_host."""

    def __init__(self, room_id: str, api_base: str | None = None, ice_token: str | None = None) -> None:
        self._room_id = normalize_room(room_id)
        base = (api_base or os.environ.get("WEBRTC_SIGNAL_URL", "")).strip()
        token = ice_token if ice_token is not None else os.environ.get("ICE_CONFIG_TOKEN", "")
        if not base:
            raise RuntimeError("WEBRTC_SIGNAL_URL is not set")
        self._http = _VpsHttp(base, self._room_id, token)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._poll_task: asyncio.Task | None = None
        self._remote_cb: Callable[[dict], None] | None = None
        self._events_handler: Callable[[dict], None] | None = None
        self._seen_caller: set[str] = set()
        self._last_ufrag: str | None = None
        self._power_idle = False

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def last_ufrag(self) -> str | None:
        return self._last_ufrag

    @property
    def power_idle(self) -> bool:
        return self._power_idle

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_event_loop()
        self._loop = loop
        return loop

    def _run_sync(self, fn):
        loop = self._bind_loop()
        return loop.run_in_executor(None, fn)

    @staticmethod
    def _extract_ufrag(sdp: str) -> str | None:
        for line in sdp.splitlines():
            if line.startswith("a=ice-ufrag:"):
                return line.split(":", 1)[1].strip()
        return None

    @staticmethod
    def _coerce_offer(data: Any) -> dict | None:
        if data is None:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        if isinstance(data, dict) and data.get("sdp"):
            return data
        return None

    @staticmethod
    def _is_plausible_browser_offer(sdp: str) -> tuple[bool, str]:
        text = sdp or ""
        if "a=ice-ufrag:" not in text:
            return False, "no ice-ufrag"
        if "m=video" not in text and "m=application" not in text:
            return False, "no m=video/application"
        return True, "ok"

    @staticmethod
    def telemetry_ping_from_events(ev: dict[str, Any]) -> int | None:
        host = ev.get("host") or {}
        ping = host.get("telemetryPing")
        if ping is None:
            return None
        try:
            return int(ping)
        except (TypeError, ValueError):
            return None

    def set_events_handler(self, handler: Callable[[dict], None] | None) -> None:
        self._events_handler = handler

    async def enter_power_idle(self, session_id: int) -> None:
        """Disconnect / нет сессии: камера и телеметрия выкл, только long-poll offer."""

        def _go() -> None:
            self._http.set_host({
                "status": "idle",
                "powerSave": True,
                "needOffer": True,
                "hostSessionId": session_id,
            })

        await self._run_sync(_go)
        self._power_idle = True
        log.info(
            "VPS: power idle — status=idle powerSave=true hostSessionId=%s",
            session_id,
        )

    async def enter_power_active(self) -> None:
        def _go() -> None:
            self._http.set_host({
                "status": "waking",
                "powerSave": False,
            })

        await self._run_sync(_go)
        self._power_idle = False
        log.info("VPS: power active — status=waking powerSave=false")

    async def end_session_for_reconnect(self, session_id: int, *, power_idle: bool = True) -> None:
        """Завершение сессии: очистка SDP, переход в idle (энергосбережение)."""

        def _go() -> None:
            self._http.clear_callee_side(timeout_sec=3.0)
            if power_idle:
                self._http.clear_caller_side(timeout_sec=3.0)
            patch: dict[str, Any] = {
                "status": "idle" if power_idle else "waiting",
                "powerSave": bool(power_idle),
                "needOffer": True,
                "hostSessionId": session_id,
            }
            self._http.set_host(patch)

        await self._run_sync(_go)
        self._power_idle = bool(power_idle)
        self._seen_caller.clear()
        log.info(
            "VPS: session ended — hostSessionId=%s powerSave=%s",
            session_id,
            power_idle,
        )

    async def push_host_telemetry(self, patch: dict[str, Any]) -> None:
        if self._power_idle:
            return

        def _go() -> None:
            self._http.set_host(patch)

        await self._run_sync(_go)

    async def reset_room_for_host_launch(self, launch_id: int) -> None:
        def _go() -> None:
            try:
                # Не удалять offer оператора — иначе при пробуждении из powerSave
                # браузер уже отправил offer, а clear_room() его стирает.
                self._http.clear_callee_side()
            except urllib.error.HTTPError as exc:
                log.debug("VPS: clear_callee_side: %s", exc)
            self._http.set_host({
                "needOffer": True,
                "hostLaunchId": launch_id,
                "hostSessionId": 0,
                "status": "waiting",
                "powerSave": False,
            })

        await self._run_sync(_go)
        self._power_idle = False
        log.info(
            "VPS: room %r — Pi start (hostLaunchId=%s), needOffer=true",
            self._room_id,
            launch_id,
        )

    async def reset_room_for_retry(self, session_id: int) -> None:
        def _go() -> None:
            self._http.set_host({
                "needOffer": True,
                "hostSessionId": session_id,
                "status": "waiting",
                "powerSave": False,
            })

        await self._run_sync(_go)
        self._power_idle = False
        log.info("VPS: room %r — retry cycle %s, needOffer=true", self._room_id, session_id)

    async def create_room(self, *, clear_offer: bool = True) -> None:
        await self.reset_room_for_host_launch(int(time.time() * 1000))

    async def peek_new_browser_offer(self, current_ufrag: str | None) -> bool:
        def _go() -> bool:
            snap = self._http.fetch_room() or {}
            offer = self._coerce_offer(snap.get("offer"))
            if not offer:
                return False
            ufrag = self._extract_ufrag(offer.get("sdp", ""))
            if not ufrag or ufrag == (current_ufrag or ""):
                return False
            ok, _ = self._is_plausible_browser_offer(offer.get("sdp", ""))
            return ok

        return bool(await self._run_sync(_go))

    async def wait_for_offer(
        self,
        prev_ufrag: str | None = None,
        *,
        should_stop: Callable[[], bool] | None = None,
        power_idle: bool = False,
    ) -> dict:
        poll_timeout = _IDLE_POLL_SEC if power_idle else _ACTIVE_POLL_SEC

        def _poll_once() -> dict[str, Any] | None:
            try:
                ev = self._http.wait_events(timeout=poll_timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.debug("VPS: wait_events (no offer yet): %s", exc)
                return None
            handler = self._events_handler
            if handler and not self._power_idle:
                try:
                    handler(ev)
                except Exception:
                    log.debug("VPS: events handler failed", exc_info=True)
            offer = self._coerce_offer(ev.get("offer"))
            if not offer:
                return None
            ufrag = self._extract_ufrag(offer.get("sdp", ""))
            if prev_ufrag and ufrag == prev_ufrag:
                return None
            ok, reason = self._is_plausible_browser_offer(offer.get("sdp", ""))
            if not ok:
                log.debug("VPS: ignore offer (%s)", reason)
                return None
            return offer

        mode = "idle/powerSave" if power_idle else "active"
        log.info("VPS: waiting for offer on room %s (%s, poll=%.0fs)", self._room_id, mode, poll_timeout)
        while True:
            if should_stop and should_stop():
                raise asyncio.CancelledError("wait_for_offer stopped")
            offer = await self._run_sync(_poll_once)
            if offer:
                self._last_ufrag = self._extract_ufrag(offer.get("sdp", ""))
                log.info(
                    "VPS: received offer (type=%s, ufrag=%s)",
                    offer.get("type", "?"),
                    self._last_ufrag,
                )
                return offer

    async def send_answer(self, answer: dict) -> None:
        def _go() -> None:
            self._http.put_answer(answer)
            self._http.set_host({"status": "negotiating", "needOffer": False, "powerSave": False})

        await self._run_sync(_go)
        self._power_idle = False
        log.info("VPS: answer sent (status=negotiating, needOffer=false)")

    async def mark_failed_need_reconnect(self) -> None:
        await self._run_sync(
            lambda: self._http.set_host({"status": "waiting", "needOffer": True, "powerSave": False})
        )
        self._power_idle = False
        log.info("VPS: session failed — needOffer=true, status=waiting")

    async def send_ice_candidate(self, candidate: dict) -> None:
        if self._power_idle:
            return
        await self._run_sync(lambda: self._http.post_callee_candidate(candidate))

    def listen_remote_candidates(self, callback: Callable[[dict], None]) -> None:
        self._remote_cb = callback
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_remote_loop())

    async def _poll_remote_loop(self) -> None:
        while self._remote_cb is not None:
            if self._power_idle:
                await asyncio.sleep(1.0)
                continue
            try:
                for c in await self.poll_remote_candidates():
                    if self._remote_cb:
                        self._remote_cb(c)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("VPS: poll callerCandidates: %s", exc)
            await asyncio.sleep(0.4)

    async def poll_remote_candidates(self) -> list[dict]:
        def _go() -> list[dict]:
            try:
                ev = self._http.wait_events(timeout=2.0)
            except (urllib.error.URLError, TimeoutError, OSError):
                return []
            bag = ev.get("callerCandidates") or {}
            out: list[dict] = []
            for cid, raw in bag.items():
                if cid in self._seen_caller:
                    continue
                if isinstance(raw, dict) and raw.get("candidate"):
                    self._seen_caller.add(cid)
                    out.append(raw)
            return out

        return await self._run_sync(_go)

    async def set_status(self, status: str) -> None:
        patch: dict[str, Any] = {"status": status}
        if status == "connected":
            patch["powerSave"] = False
        await self._run_sync(lambda: self._http.set_host(patch))
        if status == "connected":
            self._power_idle = False

    async def cleanup(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        self._remote_cb = None
        log.info("VPS: polling stopped for room %r", self._room_id)


def make_signaling(
    room_id: str,
    *,
    signal_url: str | None = None,
    ice_token: str | None = None,
) -> VpsSignaling:
    return VpsSignaling(room_id, api_base=signal_url, ice_token=ice_token)
