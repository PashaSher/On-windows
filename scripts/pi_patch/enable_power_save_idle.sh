#!/usr/bin/env bash
# Pi: энергосбережение без сессии — idle/powerSave, без телеметрии и pi-audio-relay.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_TOOLS="/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools"
REMOTE_ENV="/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env"

echo "== sync power save modules =="
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/webrtc_vps_signaling.py" \
  "$REPO/scripts/pi_patch/pi_power_save.py" \
  "$HOST:$REMOTE_TOOLS/"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<'PY'
from pathlib import Path

host = Path("/home/pavel/projects/Mobile_Raspberry_5-/rpi_tools/webrtc_host.py")
text = host.read_text(encoding="utf-8")
changed = False

if "_power_idle_side_effects" not in text:
    anchor = "    def _set_telemetry_active(self, active: bool) -> None:\n        self._telemetry_active = active\n"
    insert = anchor + '''
    async def _power_idle_side_effects(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            from rpi_tools.pi_power_save import set_publishers_active
            await loop.run_in_executor(None, lambda: set_publishers_active(False))
        except ImportError:
            pass

    async def _power_active_side_effects(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            from rpi_tools.pi_power_save import set_publishers_active
            await loop.run_in_executor(None, lambda: set_publishers_active(True))
        except ImportError:
            pass
'''
    if anchor not in text:
        raise SystemExit("webrtc_host.py: telemetry anchor missing")
    text = text.replace(anchor, insert, 1)
    changed = True

if "await self._power_idle_side_effects()" not in text:
    old = '''        if self._power_save:
            log.info(
                "WebRTC: режим экономии — камера и телеметрия выкл, ждём Connect "
                "(hostSessionId=%s)",
                self._host_session_id,
            )'''
    new = old + '''
            await self._power_idle_side_effects()'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

if "await self._power_active_side_effects()" not in text:
    old = '''        if self._power_save:
            log.info("WebRTC: ping (offer) с VPS — включаем камеру")'''
    new = old + '''
        await self._power_active_side_effects()'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

startup_old = '''            if callable(enter_idle):
                await enter_idle(0)
            log.info(
                "WebRTC: старт в режиме экономии — камера выкл, ждём Connect (offer) на VPS"
            )'''
startup_new = '''            if callable(enter_idle):
                await enter_idle(0)
            await self._power_idle_side_effects()
            log.info(
                "WebRTC: старт в режиме экономии — камера выкл, ждём Connect (offer) на VPS"
            )'''
if startup_old in text and "await self._power_idle_side_effects()" not in text.split("enter_idle(0)")[1][:200]:
    text = text.replace(startup_old, startup_new, 1)
    changed = True

if changed:
    host.write_text(text, encoding="utf-8")
    print("patched", host)
else:
    print("webrtc_host.py already has power save hooks", host)

env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines() if env.is_file() else []
updates = {
    "WEBRTC_POWER_SAVE": "1",
    "AUDIO_RELAY_POWER_GATED": "1",
}
out, seen = [], set()
for ln in lines:
    if ln.startswith("#") or "=" not in ln:
        out.append(ln)
        continue
    key = ln.split("=", 1)[0]
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(ln)
        seen.add(key)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env ok:", updates)
PY

echo "== restart camstream =="
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "sudo systemctl restart camstream.service; sleep 4; systemctl is-active camstream.service; \
   journalctl -u camstream -n 12 --no-pager | tail -8"

echo "Done: Pi idle = powerSave, no telemetry/audio publish until Connect."
