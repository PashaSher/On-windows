#!/usr/bin/env bash
# Pi: собираем NAL в целые кадры + ровный pace — убирает слайдшоу на TURN.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

video = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_video.py")
text = video.read_text(encoding="utf-8")

old_init = """        self._packets_total = 0
        self._t0 = 0.0

    def _packet_pts(self) -> int:
        \"\"\"PTS по монотонным часам — ровный темп кадров для браузера.\"\"\"
        elapsed = max(0.0, time.monotonic() - self._t0)
        frame_index = int(elapsed * self._fps)
        return frame_index * self._pts_step"""

new_init = """        self._packets_total = 0
        self._frames_total = 0
        self._t0 = 0.0
        self._last_nal_mono = 0.0
        self._last_frame_out_mono = 0.0
        self._frame_index = -1

    def _next_frame_index(self, packet: av.Packet) -> int:
        now = time.monotonic()
        gap = now - self._last_nal_mono if self._last_nal_mono > 0 else 999.0
        if (
            packet.is_keyframe
            or self._frame_index < 0
            or gap > (0.55 / max(1.0, float(self._fps)))
        ):
            self._frame_index += 1
        self._last_nal_mono = now
        return self._frame_index

    def _make_frame_packet(self, chunks: list[bytes], is_keyframe: bool, frame_index: int) -> av.Packet:
        merged = av.Packet(b"".join(chunks))
        merged.pts = frame_index * self._pts_step
        merged.time_base = self._time_base
        merged.is_keyframe = is_keyframe
        return merged"""

if old_init not in text:
    raise SystemExit("H264PassthroughTrack header block not found")
text = text.replace(old_init, new_init, 1)

old_demux = """            container = av.open(proc.stdout, format="h264", mode="r")
            for packet in container.demux(video=0):
                if packet.size == 0:
                    continue
                self._packets_total += 1
                if self._packets_total <= 3 or self._packets_total % 300 == 0:
                    log.info(
                        "H264PassthroughTrack: packet #%d size=%d key=%s",
                        self._packets_total,
                        packet.size,
                        packet.is_keyframe,
                    )
                packet.pts = self._packet_pts()
                packet.time_base = self._time_base
                try:
                    self._packet_q.put(packet, timeout=2.0)
                except queue.Full:
                    try:
                        self._packet_q.get_nowait()
                    except queue.Empty:
                        pass
                    self._packet_q.put(packet, timeout=2.0)"""

new_demux = """            container = av.open(proc.stdout, format="h264", mode="r")
            pending: list[bytes] = []
            pending_key = False
            pending_index = -1
            for packet in container.demux(video=0):
                if packet.size == 0:
                    continue
                self._packets_total += 1
                frame_index = self._next_frame_index(packet)
                new_frame = frame_index != pending_index
                if new_frame and pending:
                    frame_pkt = self._make_frame_packet(pending, pending_key, pending_index)
                    self._frames_total += 1
                    if self._frames_total <= 3 or self._frames_total % 60 == 0:
                        log.info(
                            "H264PassthroughTrack: frame #%d nals=%d key=%s bytes=%d",
                            self._frames_total,
                            len(pending),
                            pending_key,
                            frame_pkt.size,
                        )
                    try:
                        self._packet_q.put(frame_pkt, timeout=2.0)
                    except queue.Full:
                        try:
                            self._packet_q.get_nowait()
                        except queue.Empty:
                            pass
                        self._packet_q.put(frame_pkt, timeout=2.0)
                    pending = []
                    pending_key = False
                pending_index = frame_index
                if packet.is_keyframe:
                    pending_key = True
                pending.append(bytes(packet))
            if pending:
                frame_pkt = self._make_frame_packet(pending, pending_key, pending_index)
                self._frames_total += 1
                try:
                    self._packet_q.put(frame_pkt, timeout=2.0)
                except queue.Full:
                    pass"""

if old_demux not in text:
    raise SystemExit("demux loop not found")
text = text.replace(old_demux, new_demux, 1)

old_recv = """        self._got_first_frame = True
        return packet"""

new_recv = """        self._got_first_frame = True
        target_interval = 1.0 / max(1.0, float(self._fps))
        now = time.monotonic()
        if self._last_frame_out_mono > 0:
            delay = target_interval - (now - self._last_frame_out_mono)
            if delay > 0.002:
                await asyncio.sleep(delay)
        self._last_frame_out_mono = time.monotonic()
        return packet"""

if old_recv not in text:
    raise SystemExit("recv block not found")
text = text.replace(old_recv, new_recv, 1)

video.write_text(text, encoding="utf-8")
print("patched", video)

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "CAMSTREAM_VIDEO_BITRATE": "400000",
    "CAMSTREAM_VIDEO_FPS": "15",
    "CAMSTREAM_VIDEO_WIDTH": "480",
    "CAMSTREAM_VIDEO_HEIGHT": "270",
    "CAMSTREAM_VIDEO_INTRA": "4",
}
out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.strip().startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && ps aux | grep stream_camera | grep -v grep"
