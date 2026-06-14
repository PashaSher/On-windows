"""Энергосбережение Pi: остановка фоновых publishers без активной WebRTC-сессии."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("camstream.power_save")

RELAY_SERVICE = os.environ.get("PI_AUDIO_RELAY_SERVICE", "pi-audio-relay").strip() or "pi-audio-relay"


def _gate_enabled() -> bool:
    v = os.environ.get("AUDIO_RELAY_POWER_GATED", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def set_publishers_active(active: bool) -> None:
    """Включить/выключить pi-audio-relay (PCM → proxy/VPS)."""
    if not _gate_enabled():
        return
    cmd = "start" if active else "stop"
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", cmd, RELAY_SERVICE],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if proc.returncode == 0:
            log.info("power save: systemctl %s %s", cmd, RELAY_SERVICE)
        else:
            err = (proc.stderr or proc.stdout or "").strip()
            log.debug("power save: systemctl %s %s failed: %s", cmd, RELAY_SERVICE, err)
    except OSError as exc:
        log.debug("power save: systemctl %s %s: %s", cmd, RELAY_SERVICE, exc)
