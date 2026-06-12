"""Pi microphone capture for WebRTC (aiortc MediaPlayer)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiortc import RTCPeerConnection

log = logging.getLogger("camstream.webrtc")


def _open_player(device: str, fmt: str, options: dict[str, str]) -> Any | None:
    from aiortc.contrib.media import MediaPlayer

    player = MediaPlayer(device, format=fmt, options=options)
    if not player.audio:
        player.stop()
        return None
    return player


def open_pi_microphone() -> Any | None:
    """
    Open default Pi microphone via ffmpeg/PyAV.
    WEBRTC_AUDIO_DEVICE / WEBRTC_AUDIO_FORMAT override defaults.
    """
    device = (os.environ.get("WEBRTC_AUDIO_DEVICE") or "default").strip()
    fmt = (os.environ.get("WEBRTC_AUDIO_FORMAT") or "pulse").strip()
    options = {
        "sample_rate": os.environ.get("WEBRTC_AUDIO_SAMPLE_RATE", "48000"),
        "channels": os.environ.get("WEBRTC_AUDIO_CHANNELS", "1"),
    }
    attempts = [(device, fmt)]
    if fmt != "alsa":
        attempts.append(("default", "alsa"))
    if device != "default" or fmt != "pulse":
        attempts.append(("default", "pulse"))

    seen: set[tuple[str, str]] = set()
    last_err: Exception | None = None
    for dev, audio_fmt in attempts:
        key = (dev, audio_fmt)
        if key in seen:
            continue
        seen.add(key)
        try:
            player = _open_player(dev, audio_fmt, options)
            if player:
                log.info("WebRTC: Pi mic opened (%s, format=%s)", dev, audio_fmt)
                return player
        except Exception as exc:
            last_err = exc
            log.debug("WebRTC: mic open failed (%s, %s): %s", dev, audio_fmt, exc)

    if last_err:
        log.warning("WebRTC: Pi microphone unavailable: %s", last_err)
    else:
        log.warning("WebRTC: Pi microphone unavailable (no audio track)")
    return None


def attach_microphone_to_pc(pc: RTCPeerConnection) -> Any | None:
    """Attach Pi mic to negotiated audio m-line (must run before createAnswer)."""
    player = open_pi_microphone()
    if not player or not player.audio:
        return None

    attached = False
    for tx in pc.getTransceivers():
        if tx.kind == "audio":
            tx.sender.replaceTrack(player.audio)
            attached = True
            log.info("WebRTC: Pi mic → browser (audio transceiver)")
            break

    if not attached:
        pc.addTrack(player.audio)
        log.info("WebRTC: Pi mic → browser (addTrack)")

    return player


def stop_audio_player(player: Any | None) -> None:
    if not player:
        return
    try:
        player.stop()
    except Exception as exc:
        log.debug("WebRTC: stop audio player: %s", exc)
