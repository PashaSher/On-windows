#!/usr/bin/env bash
# Дуплексный звук отдельно от WebRTC:
#   Pi mic  → HTTP relay → браузер
#   PC mic  → HTTP relay → Pi speaker
# WebRTC = только видео + управление (WEBRTC_AUDIO=0).
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_PROXY="/home/pavel/operator-proxy"
REMOTE_WEB="/home/pavel/operator-web"

echo "== duplex HTTP audio relay (video/control untouched) =="
sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "mkdir -p '$REMOTE_PROXY' '$REMOTE_WEB'"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/operator_proxy_server.py" \
  "$REPO/scripts/pi_patch/audio_relay_store.py" \
  "$REPO/scripts/pi_patch/audio_relay_publisher.py" \
  "$REPO/scripts/pi_patch/audio_relay_player.py" \
  "$REPO/scripts/pi_patch/audio_relay_tunnel.c" \
  "$REPO/scripts/pi_patch/run_audio_relay_tunnel.sh" \
  "$HOST:$REMOTE_PROXY/"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_WEB/webrtc-client.html"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<PY
from pathlib import Path
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
out = []
skip_webrtc_audio = False
updates = {
    "WEBRTC_AUDIO": "0",
    "WEBRTC_AUDIO_PLAYBACK": "0",
    "AUDIO_RELAY_ENABLED": "1",
    "AUDIO_RELAY_DC": "0",
    "AUDIO_RELAY_URL": "http://127.0.0.1:8888/api/audio-relay",
    "AUDIO_RELAY_PUBLISH_URL": "http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/publish",
    "AUDIO_TALK_ENABLED": "1",
    "AUDIO_TALK_LISTEN_URL": "http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/talk-listen",
    "ICE_CONFIG_TOKEN": "$ICE_TOKEN",
}
seen = set()
for ln in lines:
    if ln.startswith("WEBRTC_AUDIO"):
        if not skip_webrtc_audio:
            out.append("WEBRTC_AUDIO=0")
            out.append("WEBRTC_AUDIO_PLAYBACK=0")
            skip_webrtc_audio = True
        continue
    if ln.startswith("WEBRTC_AUDIO_"):
        continue
    if "=" in ln and not ln.startswith("#"):
        key = ln.split("=", 1)[0]
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(ln)
            seen.add(key)
    else:
        out.append(ln)
if not skip_webrtc_audio:
    out.append("WEBRTC_AUDIO=0")
    out.append("WEBRTC_AUDIO_PLAYBACK=0")
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\\n".join(out) + "\\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo tee /etc/systemd/system/operator-proxy.service >/dev/null" <<EOF
[Unit]
Description=Operator web + local HTTP audio relay (duplex)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pavel
WorkingDirectory=$REMOTE_PROXY
Environment=OPERATOR_WEB_ROOT=$REMOTE_WEB
Environment=OPERATOR_VPS_ORIGIN=http://116.203.148.254
Environment=ICE_CONFIG_TOKEN=$ICE_TOKEN
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $REMOTE_PROXY/operator_proxy_server.py --host 0.0.0.0 --port 8888
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo apt-get install -y -qq libasound2-dev >/dev/null 2>&1 || sudo apt-get install -y libasound2-dev; cc -O2 -o '$REMOTE_PROXY/audio_relay_tunnel' '$REMOTE_PROXY/audio_relay_tunnel.c' -lasound; chmod +x '$REMOTE_PROXY/run_audio_relay_tunnel.sh' '$REMOTE_PROXY/audio_relay_tunnel'"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo tee /etc/systemd/system/pi-audio-relay.service >/dev/null" <<'EOF'
[Unit]
Description=Pi mic C PCM tunnel to HTTP relay (no WebRTC audio)
After=network-online.target operator-proxy.service
Wants=network-online.target operator-proxy.service

[Service]
Type=simple
User=pavel
WorkingDirectory=/home/pavel/operator-proxy
EnvironmentFile=-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env
ExecStart=/bin/bash /home/pavel/operator-proxy/run_audio_relay_tunnel.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo tee /etc/systemd/system/pi-audio-talk.service >/dev/null" <<'EOF'
[Unit]
Description=Browser mic PCM to Pi speaker via HTTP audio relay
After=network-online.target operator-proxy.service
Wants=network-online.target operator-proxy.service

[Service]
Type=simple
User=pavel
Group=pavel
WorkingDirectory=/home/pavel/projects/Mobile_Raspberry_5-
EnvironmentFile=-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env
ExecStart=/home/pavel/projects/Mobile_Raspberry_5-/.venv/bin/python /home/pavel/operator-proxy/audio_relay_player.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" \
  "chmod +x $REMOTE_PROXY/operator_proxy_server.py $REMOTE_PROXY/audio_relay_publisher.py $REMOTE_PROXY/audio_relay_player.py; \
   ln -sf $REMOTE_PROXY/operator_proxy_server.py $REMOTE_PROXY/server.py; \
   sudo systemctl daemon-reload; \
   sudo systemctl enable operator-proxy.service pi-audio-relay.service pi-audio-talk.service; \
   sudo systemctl restart operator-proxy.service; sleep 2; \
   sudo systemctl restart pi-audio-relay.service pi-audio-talk.service; sleep 2; \
   sudo systemctl restart camstream.service; sleep 3; \
   systemctl is-active operator-proxy pi-audio-relay pi-audio-talk camstream; \
   curl -sS -m 3 http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/status; echo; \
   journalctl -u pi-audio-relay -n 3 --no-pager; \
   journalctl -u pi-audio-talk -n 3 --no-pager"

echo ""
echo "Готово: дуплексный звук отдельно от WebRTC"
echo "  Pi → браузер: /api/audio-relay/.../listen-ws"
echo "  браузер → Pi: /api/audio-relay/.../talk-publish-ws"
echo "  WebRTC: только видео + управление"
echo "  Оператор: http://100.73.9.95:8888/cam?autostart=1"
