#!/usr/bin/env python3
"""
WebRTC signaling на VPS (замена Firebase) для Pi / stream_camera.

Пример (callee):
    from pi_vps_signaling import VpsSignalingClient

    sig = VpsSignalingClient("http://116.203.148.254/api/signal", "pi-camera", ice_token="...")
    sig.set_host(need_offer=True, host_launch_id=int(time.time() * 1000), status="waiting")
    offer = sig.wait_offer(timeout=120)
    # ... create answer, set_local_description ...
    sig.put_answer({"type": answer.type, "sdp": answer.sdp})
    sig.post_callee_candidate(candidate.to_json())
    for cand in sig.poll_caller_candidates(since=0):
        pc.addIceCandidate(cand)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


def normalize_room(room: str) -> str:
    r = (room or "").strip().strip("/")
    if r.startswith("rooms/"):
        r = r[6:]
    return r or "pi-camera"


class VpsSignalingClient:
    def __init__(self, api_base: str, room: str, ice_token: str = "") -> None:
        self.api_base = api_base.rstrip("/")
        self.room = normalize_room(room)
        self.ice_token = (ice_token or "").strip()
        self._since = 0

    def _url(self, *parts: str) -> str:
        tail = "/".join(parts)
        return f"{self.api_base}/rooms/{self.room}" + (f"/{tail}" if tail else "")

    def _request(
        self,
        method: str,
        path_parts: tuple[str, ...] = (),
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = self._url(*path_parts)
        hdrs = {"Content-Type": "application/json"}
        if self.ice_token:
            hdrs["Authorization"] = f"Bearer {self.ice_token}"
        if headers:
            hdrs.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"signal {method} {url} -> {e.code} {err_body}") from e

    def set_host(
        self,
        *,
        need_offer: bool | None = None,
        host_launch_id: int | None = None,
        host_session_id: int | None = None,
        status: str | None = None,
    ) -> None:
        patch: dict[str, Any] = {}
        if need_offer is not None:
            patch["needOffer"] = need_offer
        if host_launch_id is not None:
            patch["hostLaunchId"] = host_launch_id
        if host_session_id is not None:
            patch["hostSessionId"] = host_session_id
        if status is not None:
            patch["status"] = status
        self._request("PUT", ("host",), patch)

    def put_answer(self, answer: dict[str, Any]) -> None:
        self._request("PUT", ("answer",), answer)

    def post_callee_candidate(self, cand: dict[str, Any]) -> None:
        self._request("POST", ("callee-candidates",), cand)

    def clear_answer_side(self) -> None:
        self._request("PUT", ("answer",), None)

    def wait_events(self, timeout: float = 25.0) -> dict[str, Any]:
        url = (
            f"{self._url('events')}?since={self._since}"
            f"&timeout={max(1, min(int(timeout), 60))}"
        )
        req = urllib.request.Request(url, method="GET")
        if self.ice_token:
            req.add_header("Authorization", f"Bearer {self.ice_token}")
        http_wait = max(timeout + 12.0, 20.0)
        with urllib.request.urlopen(req, timeout=http_wait) as resp:
            ev = json.loads(resp.read().decode("utf-8"))
        self._since = int(ev.get("seq", self._since))
        return ev

    def wait_offer(self, timeout: float = 120.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = self.wait_events(timeout=min(25.0, deadline - time.monotonic()))
            offer = ev.get("offer")
            if offer and isinstance(offer, dict) and offer.get("sdp"):
                return offer
        raise TimeoutError("no offer from operator")

    def poll_caller_candidates(self, since_ids: set[str] | None = None) -> list[dict[str, Any]]:
        ev = self.wait_events(timeout=1.0)
        bag = ev.get("callerCandidates") or {}
        out: list[dict[str, Any]] = []
        for cid, raw in bag.items():
            if since_ids is not None and cid in since_ids:
                continue
            if isinstance(raw, dict):
                out.append(raw)
                if since_ids is not None:
                    since_ids.add(cid)
        return out
