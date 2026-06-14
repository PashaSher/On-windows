#!/usr/bin/env bash
# Pi 5: максимальная производительность CPU + более ранний старт вентилятора.
set -euo pipefail
HOST="${PI_HOST:-pavel@100.73.9.95}"
export SSHPASS="${PI_SSH_PASS:-2214}"

sshpass -e ssh -o StrictHostKeyChecking=no "$HOST" "sudo bash -s" <<'REMOTE'
set -euo pipefail

# 1) CPU governor = performance (сразу)
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  echo performance > "$gov"
done

# 2) systemd: performance после каждой загрузки
cat > /etc/systemd/system/cpu-performance.service <<'UNIT'
[Unit]
Description=Set CPU governor to performance (Pi WebRTC / управление)
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$g"; done'

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable cpu-performance.service
systemctl start cpu-performance.service

# 3) config.txt: вентилятор раньше (45°C), cooling_fan явно
CFG=""
for p in /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$p" ] && CFG="$p" && break
done
if [ -z "$CFG" ]; then
  echo "WARN: config.txt not found"
else
  MARK="# Mobile_Raspberry max performance"
  if ! grep -q "$MARK" "$CFG"; then
    cat >> "$CFG" <<'CFGEND'

# Mobile_Raspberry max performance
dtparam=cooling_fan=on
dtparam=fan_temp0=45000
dtparam=fan_temp0_hyst=5000
dtparam=fan_temp0_speed=100
dtparam=fan_temp1=55000
dtparam=fan_temp1_hyst=4000
dtparam=fan_temp1_speed=160
dtparam=fan_temp2=62000
dtparam=fan_temp2_hyst=3000
dtparam=fan_temp2_speed=220
dtparam=fan_temp3=70000
dtparam=fan_temp3_hyst=3000
dtparam=fan_temp3_speed=255
CFGEND
    echo "config.txt updated (reboot to apply fan thresholds)"
  else
    echo "config.txt already patched"
  fi
fi

echo "=== status ==="
echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
vcgencmd get_throttled
vcgencmd measure_temp
echo "fan_state=$(cat /sys/class/thermal/cooling_device0/cur_state 2>/dev/null || echo n/a)"
REMOTE

echo "Pi: cpu-performance.service enabled"
