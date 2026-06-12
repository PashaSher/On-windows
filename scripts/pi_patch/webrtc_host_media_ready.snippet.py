def _peer_media_ready(
    pc: RTCPeerConnection,
    video_track: object | None = None,
    *,
    min_packets: int = 30,
) -> bool:
    """Медиа уже идёт; aiortc на Pi часто остаётся ice=checking при рабочем RTP через TURN."""
    if pc.connectionState == "connected":
        return True
    if pc.iceConnectionState in ("connected", "completed"):
        return True
    if video_track is not None:
        n = int(getattr(video_track, "_packets_total", 0) or 0)
        if n >= min_packets:
            return True
    return False


# После ice=disconnected ждём восстановления; дольше — завершаем сессию и ждём новый offer.
_ICE_DISCONNECT_GRACE_SEC = 4.0


def _env_power_save() -> bool:
    v = os.environ.get("WEBRTC_POWER_SAVE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")
