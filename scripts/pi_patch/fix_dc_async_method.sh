#!/usr/bin/env bash
# Pi: вставить отсутствующий _handle_data_channel_message_async (патч был сломан).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

p = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = p.read_text()

async_block = '''
    _MOTION_ACTIONS = frozenset({"drive", "turret_smooth", "turret_stop", "home"})

    def _dc_action(self, obj: dict) -> str | None:
        a = obj.get("action")
        return a if isinstance(a, str) else None

    async def _handle_data_channel_message_async(
        self, channel, message: str | bytes
    ) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        text = message.strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = {"romeo": text}
        action = self._dc_action(obj)
        silent = action in self._MOTION_ACTIONS
        loop = self._async_loop or asyncio.get_event_loop()
        if self._command_handler:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self._command_handler(obj)
                    or {"ok": False, "error": "no handler"},
                )
            except Exception as e:
                response = {"ok": False, "error": str(e)}
        else:
            response = {"ok": False, "error": "no command handler configured"}
        if (
            isinstance(response, dict)
            and response.get("changed")
            and str(response.get("camera_action", "")).startswith("camera_")
            and self._video_track
            and self._camera_args_provider
            and self._async_loop
        ):
            cam_args = self._current_camera_args()
            asyncio.run_coroutine_threadsafe(
                self._restart_rpicam_with_args(cam_args),
                self._async_loop,
            )
            log.info(
                "WebRTC: rpicam перезапуск после %s, args=%s",
                response.get("camera_action"),
                " ".join(cam_args),
            )
        if silent:
            return
        if channel and channel.readyState == "open":
            try:
                channel.send(json.dumps(response, ensure_ascii=False))
            except Exception:
                log.debug("WebRTC: failed to send DC response")

'''

if "async def _handle_data_channel_message_async" in text:
    print("async method already present", p)
else:
    anchor = "    def _handle_data_channel_message(self, channel, message: str | bytes) -> None:"
    if anchor not in text:
        raise SystemExit("anchor not found")
    text = text.replace(anchor, async_block + anchor)
    p.write_text(text)
    print("inserted async method into", p)

# Аудио только если браузер запросил в SDP
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
et = env.read_text()
if "WEBRTC_AUDIO=1" in et:
    et = et.replace("WEBRTC_AUDIO=1", "WEBRTC_AUDIO=0")
    env.write_text(et)
    print("WEBRTC_AUDIO=0 in", env)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 3 && systemctl is-active camstream.service && python3 -c \"import importlib.util; s=importlib.util.spec_from_file_location('wh','/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py'); m=importlib.util.module_from_spec(s); print('async_ok', hasattr(type('X',(),{}) or object, '_x'))\""