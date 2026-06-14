#!/usr/bin/env bash
# На VPS от root — одна команда, чтобы /cam отдавал актуальный клиент с :8788.
set -euo pipefail
TOKEN="${ICE_CONFIG_TOKEN:-698567c765668e1abf9c7456c0d89991fd65ac8c606f262e}"
WEB="/var/www/operator/webrtc-client.html"
curl -fsSL -H "Authorization: Bearer ${TOKEN}" \
  "http://127.0.0.1:8788/api/operator-static/webrtc-client.html" \
  -o "${WEB}.new"
grep -q 'duplex-audio-talk-v' "${WEB}.new"
mv "${WEB}.new" "${WEB}"
chown www-data:www-data "${WEB}" 2>/dev/null || true
systemctl reload nginx 2>/dev/null || true
grep -o 'Operator build: [^"]*' "${WEB}" | head -1
echo "OK: https://116.203.148.254/cam"
