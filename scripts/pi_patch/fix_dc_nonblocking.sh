#!/usr/bin/env bash
# Pi: DC-команды (drive/turret) не блокируют WebRTC — иначе FPS падает при движении.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
cli = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/cli.py")
text = host.read_text()

old_wire = '''            @channel.on("message")
            def on_msg(msg):
                self._handle_data_channel_message(channel, msg)'''

new_wire = '''            @channel.on("message")
            def on_msg(msg):
                if self._async_loop:
                    asyncio.ensure_future(
                        self._handle_data_channel_message_async(channel, msg)
                    )
                else:
                    self._handle_data_channel_message(channel, msg)'''

if old_wire not in text:
    if "_handle_data_channel_message_async" in text:
        print("webrtc_host already patched")
    else:
        raise SystemExit("wire block not found")
else:
    text = text.replace(old_wire, new_wire)

async_helper = '''
    _MOTION_ACTIONS = frozenset({"drive", "turret_smooth", "turret_stop", "home"})

    def _dc_action(self, obj: dict) -> str | None:
        if isinstance(obj.get("action"), str):
            return obj["action"]
        return None

    async def _handle_data_channel_message_async(self, channel, message: str | bytes) -> None:
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
        response: dict
        if self._command_handler:
            try:
                response = await loop.run_in_executor(
                    None, lambda: self._command_handler(obj) or {"ok": False, "error": "no handler"}
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

if "async def _handle_data_channel_message_async" not in text:
    anchor = "    def _handle_data_channel_message(self, channel, message: str | bytes) -> None:"
    if anchor not in text:
        raise SystemExit("sync handler anchor not found")
    text = text.replace(anchor, async_helper + anchor)

host.write_text(text)
print("patched", host)

ct = cli.read_text()
old_loop = '''            for cmd_line in lines:
                try:
                    chunk = romeo_exchange(
                        romeo_usb, romeo_baud, cmd_line,
                        append_lf=True, read_timeout=0.45, read_idle=0.03,
                    )'''

new_loop = '''            motion = str(obj.get("action") or "") in (
                "drive", "turret_smooth", "turret_stop", "home"
            )
            for cmd_line in lines:
                try:
                    chunk = romeo_exchange(
                        romeo_usb, romeo_baud, cmd_line,
                        append_lf=True,
                        read_timeout=0.08 if motion else 0.45,
                        read_idle=0.02 if motion else 0.03,
                    )'''

if old_loop not in ct:
    if "read_timeout=0.08 if motion" in ct:
        print("cli already patched")
    else:
        raise SystemExit("cli romeo_exchange block not found")
else:
    ct = ct.replace(old_loop, new_loop)
    cli.write_text(ct)
    print("patched", cli)
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo systemctl restart camstream.service && sleep 2 && systemctl is-active camstream.service"
