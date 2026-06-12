#!/usr/bin/env bash
# Откат Pi к рабочему видео до экспериментов со звуком и frame-agg/shutter.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path
import re

video = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_video.py")
text = video.read_text(encoding="utf-8")

text = text.replace(
    '"--bitrate", str(max(150_000, int(bitrate))),',
    '"--bitrate", str(max(500_000, int(bitrate))),',
)
if '"--low-latency"' not in text:
    text = text.replace(
        '"--flush",\n',
        '"--flush",\n        "--low-latency",\n',
    )
text = text.replace('        "--shutter", "10000",\n', "")

# Replace H264PassthroughTrack class body with simple original demux
start = text.find("class H264PassthroughTrack")
end = text.find("class H264CameraTrack", start)
if start < 0 or end < 0:
    raise SystemExit("H264PassthroughTrack markers not found")

passthrough = '''class H264PassthroughTrack(_H264TrackBase):
    """
    rpicam-vid H.264 → av.Packet (demux) → aiortc H264Encoder.pack() без libx264.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: float = 30.0,
        bitrate: int = 4_000_000,
        intra: int = 30,
        profile: str | None = "high",
        camera_extra_args: list[str] | None = None,
    ) -> None:
        super().__init__(
            width, height, fps, bitrate, intra, profile, camera_extra_args,
        )
        self._demux_thread: threading.Thread | None = None
        self._packet_q: queue.Queue[av.Packet | None] = queue.Queue(
            maxsize=_PASSTHROUGH_QUEUE_MAXSIZE,
        )
        self._queue: asyncio.Queue[av.Packet] = asyncio.Queue(
            maxsize=_PASSTHROUGH_QUEUE_MAXSIZE,
        )
        self._packets_total = 0
        self._t0 = 0.0

    def _frame_pts(self) -> int:
        elapsed = max(0.0, time.monotonic() - self._t0)
        frame_index = int(elapsed * self._fps)
        return frame_index * self._pts_step

    def start_source(self, camera_extra_args: list[str] | None = None) -> None:
        if camera_extra_args is not None:
            self._camera_extra_args = camera_extra_args
        tool = _h264_tool_path()
        if not tool:
            raise RuntimeError("rpicam-vid / libcamera-vid not found")
        cmd = _build_rpicam_command(
            tool,
            self._width, self._height, self._fps,
            self._bitrate, self._intra, self._profile,
            self._camera_extra_args,
        )
        self._log_cmd(cmd)
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._started = True
        self._got_first_frame = False
        self._packets_total = 0
        self._t0 = time.monotonic()
        self._demux_thread = threading.Thread(
            target=self._demux_worker,
            daemon=True,
            name="h264-demux",
        )
        self._demux_thread.start()
        self._loop = asyncio.get_running_loop()
        self._reader_task = self._loop.create_task(self._reader_loop())

    def _demux_worker(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            self._packet_q.put(None)
            return
        try:
            container = av.open(proc.stdout, format="h264", mode="r")
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
                packet.pts = self._frame_pts()
                packet.time_base = self._time_base
                try:
                    self._packet_q.put(packet, timeout=2.0)
                except queue.Full:
                    try:
                        self._packet_q.get_nowait()
                    except queue.Empty:
                        pass
                    self._packet_q.put(packet, timeout=2.0)
        except Exception:
            log.exception("H264PassthroughTrack: demux thread error")
        finally:
            self._packet_q.put(None)
            stderr_tail = ""
            if proc.stderr:
                try:
                    stderr_tail = proc.stderr.read(4096).decode(errors="replace")
                except Exception:
                    pass
            rc = proc.poll()
            log.info(
                "H264PassthroughTrack: demux done (packets=%d, rc=%s, stderr=%s)",
                self._packets_total,
                rc,
                stderr_tail[:500] if stderr_tail else "(empty)",
            )

    async def _reader_loop(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            while True:
                packet = await loop.run_in_executor(None, self._packet_q.get)
                if packet is None:
                    break
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await self._queue.put(packet)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("H264PassthroughTrack: reader loop error")
        finally:
            log.info("H264PassthroughTrack: reader loop finished")

    async def recv(self) -> av.Packet:
        if self.readyState != "live":
            raise MediaStreamError
        if not self._started:
            self.start_source()
        deadline = (
            _FIRST_FRAME_RECV_TIMEOUT_SEC
            if not self._got_first_frame
            else _FRAME_RECV_TIMEOUT_SEC
        )
        try:
            packet = await asyncio.wait_for(self._queue.get(), timeout=deadline)
        except asyncio.TimeoutError:
            self._log_source_health(
                f"нет пакета за {deadline:.0f}s (до первого или между кадрами)"
            )
            raise MediaStreamError
        self._got_first_frame = True
        return packet

    async def restart_source(self, camera_extra_args: list[str] | None = None) -> None:
        log.info("H264PassthroughTrack: restarting source")
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._demux_thread and self._demux_thread.is_alive():
            self._demux_thread.join(timeout=3.0)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._packet_q.empty():
            try:
                self._packet_q.get_nowait()
            except queue.Empty:
                break
        self._t0 = 0.0
        self.start_source(camera_extra_args)


'''

text = text[:start] + passthrough + text[end:]
video.write_text(text, encoding="utf-8")
print("webrtc_video.py restored")

# Revert cli sport preset bootstrap if present
cli = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/cli.py")
ct = cli.read_text(encoding="utf-8")
ct = ct.replace(
    "        camera_state = _CameraControlState()\n"
    "        _cam_preset = os.environ.get(\"CAMSTREAM_CAMERA_PRESET\", \"\").strip()\n"
    "        if _cam_preset:\n"
    "            camera_state.set_preset(_cam_preset)\n"
    "        camera_handler = _make_camera_control_handler(camera_state)\n",
    "        camera_state = _CameraControlState()\n"
    "        camera_handler = _make_camera_control_handler(camera_state)\n",
)
cli.write_text(ct, encoding="utf-8")

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = []
skip_keys = {
    "CAMSTREAM_CAMERA_PRESET", "CAMSTREAM_EV", "CAMSTREAM_AWB_GAINS", "CAMSTREAM_SATURATION",
}
for raw in env.read_text(encoding="utf-8").splitlines():
    key = raw.split("=", 1)[0] if "=" in raw and not raw.strip().startswith("#") else ""
    if key in skip_keys:
        continue
    if raw.startswith("WEBRTC_AUDIO") or raw.startswith("WEBRTC_AUDIO_"):
        continue
    lines.append(raw)

updates = {
    "WEBRTC_AUDIO": "0",
    "CAMSTREAM_WEBRTC_H264_PASSTHROUGH": "1",
    "CAMSTREAM_VIDEO_BITRATE": "500000",
    "CAMSTREAM_VIDEO_FPS": "20",
    "CAMSTREAM_VIDEO_WIDTH": "480",
    "CAMSTREAM_VIDEO_HEIGHT": "270",
    "CAMSTREAM_VIDEO_INTRA": "10",
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
print("env restored", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: caller' >/dev/null; \
   curl -s -X DELETE 'http://116.203.148.254/api/signal/rooms/pi-camera' -H 'Authorization: Bearer 698567c765668e1abf9c7456c0d89991fd65ac8c606f262e' -H 'X-Clear: callee' >/dev/null; \
   sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && \
   ps aux | grep 'stream_camera.py webrtc' | grep -v grep"
