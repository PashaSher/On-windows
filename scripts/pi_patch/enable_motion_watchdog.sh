#!/usr/bin/env bash
# Pi: watchdog гусениц — через 10 с без drive stop сам шлёт MS на Romeo.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/motion_watchdog.py" \
  "$HOST:/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/motion_watchdog.py"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

cli = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/cli.py")
host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")

ct = cli.read_text()
if "init_drive_watchdog" not in ct:
    anchor = "        romeo_usb = args.romeo_usb or ROMEO_USB_PORT\n        romeo_baud = args.romeo_baud\n"
    insert = anchor + (
        "\n"
        "        from rpi_tools.motion_watchdog import init_drive_watchdog, notify_drive_command\n"
        "        init_drive_watchdog(romeo_usb, romeo_baud)\n"
    )
    if anchor not in ct:
        raise SystemExit("cli: romeo_usb anchor not found")
    ct = ct.replace(anchor, insert, 1)
    handler = "        def _webrtc_command_handler(obj: dict) -> dict:\n"
    handler_new = (
        "        def _webrtc_command_handler(obj: dict) -> dict:\n"
        "            notify_drive_command(obj)\n"
    )
    if handler_new not in ct:
        if handler not in ct:
            raise SystemExit("cli: handler anchor not found")
        ct = ct.replace(handler, handler_new, 1)
    cli.write_text(ct)
    print("patched cli.py")
else:
    print("cli.py already has watchdog")

wh = host.read_text()
if 'force_drive_stop("dc_close")' not in wh:
    wire = '''            @channel.on("message")
            def on_msg(msg):'''
    wire_new = '''            @channel.on("close")
            def on_dc_close():
                try:
                    from rpi_tools.motion_watchdog import force_drive_stop
                    force_drive_stop("dc_close")
                except ImportError:
                    pass

            @channel.on("message")
            def on_msg(msg):'''
    if wire not in wh:
        raise SystemExit("webrtc_host: dc wire anchor not found")
    wh = wh.replace(wire, wire_new, 1)

    conn = '''            if state == "connected":
                await self._signaling.set_status("connected")
            elif state == "closed":
                self._request_session_end(f"connection={state} (ice={ice})")'''
    conn_new = '''            if state == "connected":
                await self._signaling.set_status("connected")
            elif state in ("failed", "disconnected", "closed"):
                try:
                    from rpi_tools.motion_watchdog import force_drive_stop
                    force_drive_stop(f"webrtc_{state}")
                except ImportError:
                    pass
            if state == "closed":
                self._request_session_end(f"connection={state} (ice={ice})")'''
    if conn not in wh:
        raise SystemExit("webrtc_host: connectionstate anchor not found")
    wh = wh.replace(conn, conn_new, 1)

    cleanup = "    async def _cleanup_session(self) -> None:\n"
    cleanup_new = (
        "    async def _cleanup_session(self) -> None:\n"
        "        try:\n"
        "            from rpi_tools.motion_watchdog import force_drive_stop\n"
        "            force_drive_stop(\"session_cleanup\")\n"
        "        except ImportError:\n"
        "            pass\n"
    )
    if 'force_drive_stop("session_cleanup")' not in wh:
        if cleanup not in wh:
            raise SystemExit("webrtc_host: cleanup anchor not found")
        wh = wh.replace(cleanup, cleanup_new, 1)

    host.write_text(wh)
    print("patched webrtc_host.py")
else:
    print("webrtc_host.py already has watchdog hooks")

lines = env.read_text(encoding="utf-8").splitlines() if env.is_file() else []
out, seen = [], set()
for ln in lines:
    if ln.startswith("ROMEO_DRIVE_WATCHDOG_SEC="):
        out.append("ROMEO_DRIVE_WATCHDOG_SEC=10")
        seen.add("ROMEO_DRIVE_WATCHDOG_SEC")
    else:
        out.append(ln)
if "ROMEO_DRIVE_WATCHDOG_SEC" not in seen:
    out.append("ROMEO_DRIVE_WATCHDOG_SEC=10")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service; sleep 3; systemctl is-active camstream.service" 2>&1

echo "Motion watchdog enabled (ROMEO_DRIVE_WATCHDOG_SEC=10)"
