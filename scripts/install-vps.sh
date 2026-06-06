#!/usr/bin/env bash
# Полная установка WebRTC-бэкенда на пустой VPS (Hetzner / Ubuntu).
#
# Запуск (root):
#   git clone https://github.com/PashaSher/On-windows.git /root/On-windows
#   sudo bash /root/On-windows/scripts/install-vps.sh
#
# Или повторно на уже настроенном сервере (секреты сохраняются):
#   sudo bash /root/On-windows/scripts/install-vps.sh
#
# Переменные окружения (опционально):
#   REPO_DIR=/root/On-windows     — путь к клону репозитория
#   PUBLIC_IP=1.2.3.4             — публичный IP (иначе авто)
#   WEBRTC_ROOM=pi-camera           — имя комнаты
#   GIT_REPO_URL=...              — клонировать, если REPO_DIR пуст
#   KEEP_SECRETS=1                — не менять token/пароль (по умолчанию 1, если уже есть)
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/On-windows}"
GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/PashaSher/On-windows.git}"
WEB_ROOT="${WEB_ROOT:-/var/www/operator}"
WEBRTC_ROOM="${WEBRTC_ROOM:-pi-camera}"
ICE_ENV="/etc/default/ice-config-server"
BOOTSTRAP_JSON="/etc/default/operator-bootstrap.json"
SECRETS_FILE="/root/.on-windows-secrets.env"
NGINX_SITE="/etc/nginx/sites-available/operator-web"
SYSTEMD_UNIT="/etc/systemd/system/ice-config-server.service"
TURN_USER="${TURN_USER:-romeo}"

log() { echo "[install-vps] $*"; }
die() { echo "[install-vps] ERROR: $*" >&2; exit 1; }

if [[ "$(id -u)" -ne 0 ]]; then
  die "Запустите от root: sudo bash $0"
fi

detect_public_ip() {
  if [[ -n "${PUBLIC_IP:-}" ]]; then
    echo "$PUBLIC_IP"
    return
  fi
  local ip
  ip="$(curl -4 -fsS --max-time 5 https://ifconfig.me/ip 2>/dev/null || true)"
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  [[ -n "$ip" ]] || die "Не удалось определить PUBLIC_IP — задайте вручную: PUBLIC_IP=... $0"
  echo "$ip"
}

ensure_repo() {
  if [[ -f "$REPO_DIR/cloud/ice_config_server.py" ]]; then
    log "Репозиторий: $REPO_DIR"
    return
  fi
  log "Клонируем $GIT_REPO_URL → $REPO_DIR"
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --depth 1 "$GIT_REPO_URL" "$REPO_DIR"
}

load_or_generate_secrets() {
  local keep=0
  if [[ "${KEEP_SECRETS:-1}" == "1" && -f "$ICE_ENV" ]]; then
    # shellcheck disable=SC1090
    source "$ICE_ENV"
    if [[ -n "${ICE_CONFIG_TOKEN:-}" && -n "${TURN_PASSWORD:-}" ]]; then
      keep=1
      log "Сохраняем существующие секреты из $ICE_ENV"
    fi
  fi
  if [[ "$keep" -eq 0 ]]; then
    TURN_PASSWORD="$(openssl rand -hex 12)"
    ICE_CONFIG_TOKEN="$(openssl rand -hex 24)"
    log "Сгенерированы новые TURN password и ICE token"
  fi
  mkdir -p "$(dirname "$SECRETS_FILE")"
  cat >"$SECRETS_FILE" <<EOF
PUBLIC_IP=${PUBLIC_IP}
TURN_USER=${TURN_USER}
TURN_PASSWORD=${TURN_PASSWORD}
ICE_CONFIG_TOKEN=${ICE_CONFIG_TOKEN}
WEBRTC_ROOM=${WEBRTC_ROOM}
EOF
  chmod 600 "$SECRETS_FILE"
}

install_packages() {
  log "Установка пакетов (nginx, coturn, git, openssl)…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq nginx coturn git openssl curl python3 ca-certificates
}

configure_coturn() {
  log "Настройка coturn (TURN :3478)…"
  cat >/etc/turnserver.conf <<EOF
listening-port=3478
listening-ip=0.0.0.0
external-ip=${PUBLIC_IP}
relay-ip=${PUBLIC_IP}
fingerprint
lt-cred-mech
user=${TURN_USER}:${TURN_PASSWORD}
realm=${PUBLIC_IP}
min-port=49160
max-port=65535
no-cli
no-tls
no-dtls
log-file=/var/log/turnserver.log
EOF
  if grep -q '^#TURNSERVER_ENABLED=1' /etc/default/coturn 2>/dev/null; then
    sed -i 's/^#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
  elif ! grep -q '^TURNSERVER_ENABLED=1' /etc/default/coturn 2>/dev/null; then
    echo 'TURNSERVER_ENABLED=1' >>/etc/default/coturn
  fi
}

configure_ice_server() {
  log "Настройка ice-config-server…"
  cat >"$ICE_ENV" <<EOF
TURN_URLS=turn:${PUBLIC_IP}:3478?transport=udp,turn:${PUBLIC_IP}:3478?transport=tcp
TURN_USERNAME=${TURN_USER}
TURN_PASSWORD=${TURN_PASSWORD}
ICE_CONFIG_TOKEN=${ICE_CONFIG_TOKEN}
ICE_CONFIG_HOST=0.0.0.0
ICE_CONFIG_PORT=8788
OPERATOR_WEB_ROOT=${WEB_ROOT}
OPERATOR_BOOTSTRAP_FILE=${BOOTSTRAP_JSON}
WEBRTC_ROOM=${WEBRTC_ROOM}
EOF
  chmod 600 "$ICE_ENV"

  cat >"$BOOTSTRAP_JSON" <<EOF
{
  "room": "${WEBRTC_ROOM}",
  "iceConfigUrl": "/api/ice",
  "iceConfigToken": "${ICE_CONFIG_TOKEN}",
  "signalApiBase": "/api/signal",
  "signaling": "vps"
}
EOF
  chmod 600 "$BOOTSTRAP_JSON"
}

deploy_static() {
  log "Копирование веб-интерфейса → $WEB_ROOT"
  mkdir -p "$WEB_ROOT"
  cp -r "$REPO_DIR/deploy/www/"* "$WEB_ROOT/"
  chown -R www-data:www-data "$WEB_ROOT"
}

configure_nginx() {
  log "Настройка nginx (:80, :443)…"
  mkdir -p /etc/nginx/ssl
  if [[ ! -f /etc/nginx/ssl/operator-selfsigned.crt ]]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout /etc/nginx/ssl/operator-selfsigned.key \
      -out /etc/nginx/ssl/operator-selfsigned.crt \
      -subj "/CN=${PUBLIC_IP}" 2>/dev/null
  fi
  sed "s/116\.203\.148\.254/${PUBLIC_IP}/g" \
    "$REPO_DIR/deploy/nginx/operator-web.conf.example" >"$NGINX_SITE"
  rm -f /etc/nginx/sites-enabled/default
  ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/operator-web
  nginx -t
}

configure_systemd() {
  log "systemd: ice-config-server"
  cat >"$SYSTEMD_UNIT" <<EOF
[Unit]
Description=ICE WebRTC config API + VPS signaling
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-${ICE_ENV}
WorkingDirectory=${REPO_DIR}
ExecStart=/bin/bash -lc 'exec /usr/bin/python3 "${REPO_DIR}/cloud/ice_config_server.py" --host "\${ICE_CONFIG_HOST:-0.0.0.0}" --port "\${ICE_CONFIG_PORT:-8788}"'
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

symlink_project() {
  if [[ "$REPO_DIR" != "/root/project" && ! -e /root/project ]]; then
    ln -s "$REPO_DIR" /root/project
    log "Симлинк /root/project → $REPO_DIR"
  fi
}

start_services() {
  log "Запуск сервисов…"
  systemctl enable coturn ice-config-server nginx
  systemctl restart coturn ice-config-server nginx
}

verify() {
  log "Проверка…"
  sleep 2
  systemctl is-active coturn ice-config-server nginx >/dev/null

  local code
  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${ICE_CONFIG_TOKEN}" \
    "http://127.0.0.1/api/ice")"
  [[ "$code" == "200" ]] || die "/api/ice вернул $code (ожидали 200)"

  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1/api/signal/rooms/${WEBRTC_ROOM}")"
  [[ "$code" == "200" ]] || die "/api/signal вернул $code (ожидали 200)"

  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1/ping.html")"
  [[ "$code" == "200" ]] || die "ping.html вернул $code"
}

print_summary() {
  cat <<EOF

================================================================================
  VPS готов: WebRTC оператор + ICE + TURN + signaling
================================================================================

  Браузер (оператор):
    http://${PUBLIC_IP}/cam?iceToken=${ICE_CONFIG_TOKEN}

  Проверка HTTP:
    http://${PUBLIC_IP}/ping.html

  HTTPS (самоподписанный сертификат):
    https://${PUBLIC_IP}/cam?iceToken=${ICE_CONFIG_TOKEN}

  Переменные для Raspberry Pi:
    WEBRTC_ICE_CONFIG_URL=http://${PUBLIC_IP}/api/ice
    WEBRTC_ICE_CONFIG_TOKEN=${ICE_CONFIG_TOKEN}
    WEBRTC_SIGNAL_URL=http://${PUBLIC_IP}/api/signal
    WEBRTC_ROOM=${WEBRTC_ROOM}

  Секреты сохранены: ${SECRETS_FILE}
  Повторный показ:   ${REPO_DIR}/scripts/show-ice-client-env.sh

  Hetzner Firewall (если ICE failed):
    TCP 80, 443, 8788, 3478
    UDP 3478, 49160-65535

  Сервисы:
    systemctl status ice-config-server nginx coturn
    journalctl -u ice-config-server -f

================================================================================
EOF
}

main() {
  PUBLIC_IP="$(detect_public_ip)"
  log "PUBLIC_IP=$PUBLIC_IP"
  ensure_repo
  load_or_generate_secrets
  install_packages
  configure_coturn
  configure_ice_server
  deploy_static
  configure_nginx
  configure_systemd
  symlink_project
  start_services
  verify
  print_summary
}

main "$@"
