#!/usr/bin/env bash
# Открыть порты TURN/WebRTC в Hetzner Cloud Firewall (нужен API token).
# Использование на ПК или VPS:
#   export HCLOUD_TOKEN='...'   # Cloud Console → Security → API tokens
#   bash scripts/open_hetzner_turn_firewall.sh
set -euo pipefail

TOKEN="${HCLOUD_TOKEN:-}"
SERVER_IP="${SERVER_IP:-116.203.148.254}"
FW_NAME="${FW_NAME:-webrtc-turn-$(date +%Y%m%d)}"

if [[ -z "$TOKEN" ]]; then
  echo "Set HCLOUD_TOKEN (Hetzner Cloud API token with Read&Write)"
  exit 1
fi

API="https://api.hetzner.cloud/v1"
auth=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

echo "Looking up server with IP $SERVER_IP..."
srv_json=$(curl -sf "${auth[@]}" "$API/servers")
server_id=$(echo "$srv_json" | python3 -c "
import json,sys
ip=sys.argv[1]
for s in json.load(sys.stdin).get('servers',[]):
    for n in s.get('public_net',{}).get('ipv4',{}), s.get('public_net',{}).get('ipv6',{}):
        pass
    v4=s.get('public_net',{}).get('ipv4',{})
    if v4.get('ip')==ip:
        print(s['id']); raise SystemExit
for s in json.load(open('/dev/stdin')):
    pass
" 2>/dev/null <<< "$srv_json" || true)

# simpler lookup
server_id=$(curl -sf "${auth[@]}" "$API/servers" | python3 - <<PY
import json,sys
ip="$SERVER_IP"
data=json.load(sys.stdin)
for s in data.get("servers",[]):
    if s.get("public_net",{}).get("ipv4",{}).get("ip")==ip:
        print(s["id"])
        break
PY
)

if [[ -z "${server_id:-}" ]]; then
  echo "Server not found for IP $SERVER_IP"
  exit 1
fi
echo "Server id: $server_id"

rules='[
  {"direction":"in","protocol":"tcp","port":"22","source_ips":["0.0.0.0/0","::/0"],"description":"ssh"},
  {"direction":"in","protocol":"tcp","port":"80","source_ips":["0.0.0.0/0","::/0"],"description":"http"},
  {"direction":"in","protocol":"tcp","port":"443","source_ips":["0.0.0.0/0","::/0"],"description":"https"},
  {"direction":"in","protocol":"tcp","port":"8788","source_ips":["0.0.0.0/0","::/0"],"description":"ice-api"},
  {"direction":"in","protocol":"udp","port":"3478","source_ips":["0.0.0.0/0","::/0"],"description":"turn"},
  {"direction":"in","protocol":"tcp","port":"3478","source_ips":["0.0.0.0/0","::/0"],"description":"turn-tcp"},
  {"direction":"in","protocol":"udp","port":"49160-65535","source_ips":["0.0.0.0/0","::/0"],"description":"turn-relay-udp"}
]'

body=$(python3 -c "import json; print(json.dumps({'name':'$FW_NAME','rules':json.loads('''$rules''')}))")

echo "Creating firewall $FW_NAME..."
fw_id=$(curl -sf "${auth[@]}" -d "$body" "$API/firewalls" | python3 -c "import json,sys; print(json.load(sys.stdin)['firewall']['id'])")

echo "Applying firewall $fw_id to server $server_id..."
curl -sf "${auth[@]}" -d "{\"type\":\"server\",\"server\":{\"id\":$server_id},\"apply_to\":[{\"type\":\"server\",\"server\":{\"id\":$server_id}}]}" \
  "$API/firewalls/$fw_id/actions/apply_to_resources" >/dev/null

echo "Done. Firewall $FW_NAME ($fw_id) attached to server $server_id"
echo "Wait ~30s, then Ctrl+F5 in browser and Connect again."
