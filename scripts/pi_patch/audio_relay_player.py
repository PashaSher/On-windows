#!/usr/bin/env python3
"""Browser → Pi: PCM из HTTP audio relay → aplay (отдельно от WebRTC)."""

from __future__ import annotations

import http.client
import logging
import os
import subprocess
import sys
import time
import urllib.parse

log = logging.getLogger("pi.audio_talk")

SAMPLE_RATE = 16_000
CHANNELS = 1
FRAME_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHANNELS * 2 * FRAME_MS // 1000  # 640


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def resolve_alsa_playback_device() -> str:
    override = _env("AUDIO_TALK_ALSA") or _env("WEBRTC_AUDIO_ALSA")
    if override:
        return override
    try:
        with open("/proc/asound/cards", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                low = line.lower()
                if "voice" not in low and "googlevoi" not in low:
                    continue
                parts = line.strip().split()
                if parts and parts[0].isdigit():
                    return f"plughw:{parts[0]},0"
    except OSError:
        pass
    return "default"


def _parse_listen_url(raw: str) -> tuple[str, int, str]:
    u = urllib.parse.urlparse(raw)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    path = u.path or "/"
    if u.query:
        path = f"{path}?{u.query}"
    return host, port, path


def run_once(listen_url: str, token: str, device: str) -> None:
    host, port, path = _parse_listen_url(listen_url)
    headers = {"Authorization": f"Bearer {token}"}
    conn = http.client.HTTPConnection(host, port, timeout=3600)
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    if resp.status != 200:
        body = resp.read(256).decode(errors="replace")
        raise RuntimeError(f"talk-listen HTTP {resp.status}: {body[:120]}")
    sock = getattr(getattr(resp, "fp", None), "raw", None)
    sock = getattr(sock, "_sock", sock) if sock else None
    if sock is not None:
        sock.settimeout(3600)

    cmd = [
        "aplay",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(SAMPLE_RATE),
        "-c",
        str(CHANNELS),
        "-F",
        "20000",
        "-B",
        "80000",
        "-t",
        "raw",
        "-q",
        "-",
    ]
    total = 0
    proc = None
    stdin = None
    try:
        while True:
            chunk = resp.read(CHUNK_BYTES)
            if not chunk:
                break
            if proc is None:
                log.info("aplay %s ← %s (first chunk %d B)", device, listen_url, len(chunk))
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
                stdin = proc.stdin
                if not stdin:
                    raise RuntimeError("aplay stdin missing")
            stdin.write(chunk)
            total += len(chunk)
    finally:
        if stdin:
            try:
                stdin.close()
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        err = ""
        if proc is not None and proc.stderr:
            err = proc.stderr.read().decode(errors="replace").strip()
        if err and proc is not None and proc.returncode not in (0, -15):
            log.warning("aplay stderr: %s", err[:300])
        conn.close()
        log.info("talk playback ended: %d bytes", total)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    room = _env("WEBRTC_ROOM", "pi-camera") or "pi-camera"
    base = _env("AUDIO_RELAY_URL") or "http://127.0.0.1:8888/api/audio-relay"
    base = base.rstrip("/")
    listen_url = _env("AUDIO_TALK_LISTEN_URL") or f"{base}/rooms/{room}/talk-listen"
    token = _env("ICE_CONFIG_TOKEN")
    if not token:
        log.error("ICE_CONFIG_TOKEN required")
        return 1
    if "?" not in listen_url:
        listen_url = f"{listen_url}?token={urllib.parse.quote(token)}"
    device = resolve_alsa_playback_device()
    backoff = 2.0
    while True:
        try:
            run_once(listen_url, token, device)
        except KeyboardInterrupt:
            log.info("stop")
            return 0
        except Exception as exc:
            log.warning("talk player error: %s — retry in %.0fs", exc, backoff)
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 30.0)


if __name__ == "__main__":
    sys.exit(main())
