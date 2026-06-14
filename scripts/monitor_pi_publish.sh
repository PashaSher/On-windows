#!/usr/bin/env bash
# Pi-side publish monitor during audio session (run via SSH).
set -euo pipefail
DURATION="${1:-180}"
INTERVAL="${2:-1}"
VPS="${VPS:-116.203.148.254}"
ROOM="${ROOM:-pi-camera}"

ts() { date -u +"%H:%M:%S"; }

echo "[$(ts)] === PI PUBLISH MONITOR ${DURATION}s ==="
prev_pub=""
prev_sq=""
t0=$(date +%s)

while (( $(date +%s) - t0 < DURATION )); do
  elapsed=$(( $(date +%s) - t0 ))
  status=$(curl -sS -m 3 "http://${VPS}/api/audio-relay/rooms/${ROOM}/status" 2>/dev/null || echo '{"error":true}')
  pub=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('publisherActive', '?'))" "$status" 2>/dev/null || echo "?")
  sq=$(ss -tn state established "( dport = :80 and dst = ${VPS} )" 2>/dev/null | awk 'NR>1 {print $2}' | sort -n | tail -1)
  sq=${sq:-0}
  if [[ "$pub" != "$prev_pub" && -n "$prev_pub" ]]; then
    echo "[$(ts)] EVENT publisherActive ${prev_pub} -> ${pub} t=${elapsed}s"
  fi
  if [[ "$sq" != "$prev_sq" && -n "$prev_sq" && "$sq" -gt 4000 ]]; then
    echo "[$(ts)] WARN Send-Q=${sq} t=${elapsed}s (publish TCP backlog)"
  fi
  if (( elapsed % 15 == 0 )); then
    echo "[$(ts)] TICK t=${elapsed}s pub=${pub} max_sendq=${sq}"
    journalctl -u pi-audio-relay -n 2 --no-pager 2>/dev/null | tail -1 || true
  fi
  prev_pub=$pub
  prev_sq=$sq
  sleep "$INTERVAL"
done
echo "[$(ts)] === PI MONITOR DONE ==="
