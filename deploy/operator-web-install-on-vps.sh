#!/usr/bin/env bash
# На VPS (root): установка nginx для публичной страницы оператора.
set -euo pipefail

WEB_ROOT="${WEB_ROOT:-/var/www/operator}"
NGINX_SITE="${NGINX_SITE:-/etc/nginx/sites-available/operator-web}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_EXAMPLE="${SCRIPT_DIR}/nginx/operator-web.conf.example"

if [[ ! -f "$CONF_EXAMPLE" ]]; then
  echo "Missing $CONF_EXAMPLE — copy deploy/ to VPS first"
  exit 1
fi

apt-get update -qq
apt-get install -y nginx

mkdir -p "$WEB_ROOT"
chown -R www-data:www-data "$WEB_ROOT"

cp "$CONF_EXAMPLE" "$NGINX_SITE"
ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/operator-web"
nginx -t
systemctl enable nginx
systemctl reload nginx

echo "OK. Upload bundle to $WEB_ROOT then open:"
echo "  http://$(hostname -I | awk '{print $1}')/webrtc-client.html"
