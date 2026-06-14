#!/usr/bin/env bash
# Pi: ffmpeg Opus/Ogg (C) + audio_relay_publish (C) + browser <audio>
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"
ICE_TOKEN="${ICE_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_PROXY="/home/pavel/operator-proxy"
REMOTE_WEB="/home/pavel/operator-web"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "mkdir -p '$REMOTE_PROXY' '$REMOTE_WEB'"
sshpass -e scp -o StrictHostKeyChecking=no \
  "$REPO/scripts/pi_patch/audio_relay_publish.c" \
  "$REPO/scripts/pi_patch/run_audio_relay_ffmpeg.sh" \
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
cc -O2 -o "$PROXY/audio_relay_publish" "$PROXY/audio_relay_publish.c"
chmod +x "$PROXY/run_audio_relay_ffmpeg.sh" "$PROXY/audio_relay_publish"
ln -sf "$PROXY/operator_proxy_server.py" "$PROXY/server.py"
sudo tee /etc/systemd/system/pi-audio-relay.service >/dev/null <<UNIT
[Unit]
Description=Pi mic ffmpeg Opus to HTTP relay (C publish)
After=network-online.target operator-proxy.service
Wants=operator-proxy.service

[Service]
Type=simple
User=pavel
WorkingDirectory=$PROXY
EnvironmentFile=-/home/pavel/projects/Mobile_Raspberry_5-/config/webrtc.vps.env
ExecStart=/bin/bash $PROXY/run_audio_relay_ffmpeg.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/systemd/system/operator-proxy.service >/dev/null <<UNIT
[Unit]
Description=Operator web + Opus audio relay
After=network-online.target

[Service]
Type=simple
User=pavel
WorkingDirectory=$PROXY
Environment=OPERATOR_WEB_ROOT=/home/pavel/operator-web
Environment=OPERATOR_VPS_ORIGIN=http://116.203.148.254
Environment=ICE_CONFIG_TOKEN=$ICE_TOKEN
ExecStart=/usr/bin/python3 $PROXY/operator_proxy_server.py --host 0.0.0.0 --port 8888
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable operator-proxy pi-audio-relay
sudo systemctl restart operator-proxy
sleep 2
sudo systemctl restart pi-audio-relay
sleep 3
sudo systemctl restart camstream
sleep 2
systemctl is-active operator-proxy pi-audio-relay camstream
curl -sS -m 2 http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/status
echo
timeout 10 curl -sS -H "Authorization: Bearer $ICE_TOKEN" http://127.0.0.1:8888/api/audio-relay/rooms/pi-camera/listen -o /tmp/t.ogg || true
ls -la /tmp/t.ogg 2>/dev/null || true
python3 -c "d=open('/tmp/t.ogg','rb').read(4); print('ogg magic:', d)"
EOF

echo "deploy ok"
