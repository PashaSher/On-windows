#!/usr/bin/env bash
# Pi: C ALSA tunnel + AudioWorklet WS playback in browser
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_PROXY="/home/pavel/operator-proxy"
REMOTE_WEB="/home/pavel/operator-web"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "mkdir -p '$REMOTE_PROXY' '$REMOTE_WEB'"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_tunnel.c" \
  "$REPO/scripts/pi_patch/run_audio_relay_tunnel.sh" \
  "$REPO/scripts/pi_patch/audio_relay_store.py" \
  "$REPO/scripts/pi_patch/operator_proxy_server.py" \
  "$HOST:$REMOTE_PROXY/"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/webrtc-client.html" \
  "$HOST:$REMOTE_WEB/webrtc-client.html"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "python3 -" <<PY
from pathlib import Path
env = Path("/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env")
lines = env.read_text(encoding="utf-8").splitlines()
updates = {
    "WEBRTC_AUDIO": "0",
    "WEBRTC_AUDIO_PLAYBACK": "0",
    "AUDIO_RELAY_PUBLISH_URL": "http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/publish",
    "ICE_CONFIG_TOKEN": "$ICE_TOKEN",
}
out, seen = [], set()
skip = False
for ln in lines:
    if ln.startswith("WEBRTC_AUDIO"):
        if not skip:
            out.append("WEBRTC_AUDIO=0")
            out.append("WEBRTC_AUDIO_PLAYBACK=0")
            skip = True
        continue
    if ln.startswith("WEBRTC_AUDIO_"):
        continue
    if "=" in ln and not ln.startswith("#"):
        k = ln.split("=", 1)[0]
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(ln)
            seen.add(k)
    else:
        out.append(ln)
if not skip:
    out.append("WEBRTC_AUDIO=0")
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\\n".join(out) + "\\n", encoding="utf-8")
print("env ok")
PY

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "ICE_TOKEN='$ICE_TOKEN' bash -s" <<'EOF'
set -e
PROXY=/home/pavel/operator-proxy
sudo apt-get install -y -qq libasound2-dev >/dev/null 2>&1 || sudo apt-get install -y libasound2-dev
cc -O2 -o "$PROXY/audio_relay_tunnel" "$PROXY/audio_relay_tunnel.c" -lasound
chmod +x "$PROXY/run_audio_relay_tunnel.sh" "$PROXY/audio_relay_tunnel"
ln -sf "$PROXY/operator_proxy_server.py" "$PROXY/server.py"
sudo tee /etc/systemd/system/pi-audio-relay.service >/dev/null <<UNIT
[Unit]
Description=Pi mic C PCM tunnel to HTTP relay
After=network-online.target operator-proxy.service
Wants=operator-proxy.service

[Service]
Type=simple
User=pavel
WorkingDirectory=$PROXY
EnvironmentFile=-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env
ExecStart=/bin/bash $PROXY/run_audio_relay_tunnel.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable operator-proxy pi-audio-relay
sudo systemctl restart operator-proxy
sleep 2
sudo systemctl restart pi-audio-relay
sleep 2
sudo systemctl restart camstream
sleep 2
systemctl is-active operator-proxy pi-audio-relay camstream
curl -sS -m 2 http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/status
echo
python3 - <<PY
import socket, struct, base64
TOKEN="$ICE_TOKEN"
req=(
    f"GET /api/audio-relay/rooms/pi-camera/listen-ws?token={TOKEN} HTTP/1.1\r\n"
    "Host: 127.0.0.1:8888\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
)
s=socket.create_connection(("127.0.0.1",8888),timeout=5)
s.settimeout(3)
s.sendall(req.encode())
s.recv(512)
hdr=s.recv(2)
ln=hdr[1]&127
if ln==126: ln=struct.unpack("!H", s.recv(2))[0]
payload=s.recv(ln)
print("ws pcm bytes", len(payload), "mod640", len(payload)%640)
PY
EOF

echo "deploy ok"
