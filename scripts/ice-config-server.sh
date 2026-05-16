#!/usr/bin/env bash
# Ручной запуск ICE config API из корня репозитория (Linux / VPS / WSL).
# Переменные: из файла cloud/ice-config-server.env (если есть) или из ICE_CONFIG_ENV.
# Уже заданные в окружении export'ы не затираются — файл только дополняет (set -a source).
#
# Пример:
#   ./scripts/ice-config-server.sh
#   ICE_CONFIG_ENV=/etc/default/ice-config-server ./scripts/ice-config-server.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ICE_CONFIG_ENV:-$REPO_ROOT/cloud/ice-config-server.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
cd "$REPO_ROOT"
HOST="${ICE_CONFIG_HOST:-0.0.0.0}"
PORT="${ICE_CONFIG_PORT:-8788}"
exec python3 "$REPO_ROOT/cloud/ice_config_server.py" --host "$HOST" --port "$PORT"
