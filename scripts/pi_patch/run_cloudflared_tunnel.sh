#!/usr/bin/env bash
# Cloudflare quick tunnel → operator-proxy; сохраняем URL в public-url.txt
set -euo pipefail
LOCAL_PORT="${LOCAL_PORT:-8888}"
URL_FILE="${URL_FILE:-/home/pavel/operator-web/public-url.txt}"
CF="${CF:-/usr/local/bin/cloudflared}"

mkdir -p "$(dirname "$URL_FILE")"

write_url() {
  local u="$1"
  if [[ -n "$u" ]]; then
    printf '%s/cam\n' "$u" >"$URL_FILE"
    chmod 644 "$URL_FILE" 2>/dev/null || true
  fi
}

# shellcheck disable=SC2064
trap 'write_url ""' EXIT

"$CF" tunnel --url "http://127.0.0.1:${LOCAL_PORT}" --no-autoupdate 2>&1 | while IFS= read -r line; do
  printf '%s\n' "$line"
  if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
    write_url "${BASH_REMATCH[1]}"
  fi
done
