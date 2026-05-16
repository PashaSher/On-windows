Облачный слой для удалённого WebRTC (без VPN для конечного пользователя)
====================================================================

1) ice_config_server.py
   Отдаёт GET /api/ice → JSON {"iceServers":[...]} с публичными STUN + ваш TURN.
   Учётные данные TURN не хранятся в webrtc-client.html — только URL этого API.

   Пример (Windows CMD, локально для теста):
     set TURN_URLS=turn:ВАШ_ХОСТ:3478
     set TURN_USERNAME=u
     set TURN_PASSWORD=p
     python cloud\ice_config_server.py --host 127.0.0.1 --port 8788

   Linux / VPS (из корня репозитория):
     cp cloud/ice-config-server.env.example cloud/ice-config-server.env
     # отредактируйте cloud/ice-config-server.env (TURN_*; опционально ICE_CONFIG_TOKEN)
     chmod +x scripts/ice-config-server.sh
     ./scripts/ice-config-server.sh
   Файл окружения по умолчанию: cloud/ice-config-server.env; другой путь: ICE_CONFIG_ENV=/path/to/file.
   Хост/порт: ICE_CONFIG_HOST, ICE_CONFIG_PORT (по умолчанию 0.0.0.0 и 8788).

   systemd (автозапуск): deploy/systemd/ice-config-server.service и образец
   deploy/etc/default/ice-config-server.example — скопировать в /etc/default/ice-config-server,
   в unit заменить /root/project на путь к клону, затем daemon-reload, enable --now.
   Логи: journalctl -u ice-config-server.service -f

   Вынести в интернет: VPS + HTTPS (nginx reverse proxy) или туннель (ngrok)
   только для теста. В проде — HTTPS и сузить CORS в ice_config_server.py.

2) webrtc-client.html
   - Поле «Room» → путь в Firebase (например rooms/robot-ABC или короткий id robot-ABC).
   - Поле «ICE config URL» → https://ваш-сервер/api/ice (опционально ?token=...).

   Параметры URL:
     ?room=robot-ABC
     ?ice=https://example.com/api/ice
     ?iceToken=...   (если на сервере задан ICE_CONFIG_TOKEN)

3) Pi (ответчик WebRTC)
   Должен использовать ТУ ЖЕ ветку Firebase rooms/<id>/... и те же iceServers
   (запрос к тому же /api/ice или статическая конфигурация с тем же TURN).

   Режим браузера «только Hetzner» (?profile=hetzner-relay-only):
   на Pi тоже iceTransportPolicy=relay и тот же ICE token — иначе ICE failed.
   Подробно: deploy/pi-webrtc-hetzner-relay.example.txt

4) Шаринг доступа другому человеку (продуктовый уровень)
   - Дайте второму человеку ссылку с тем же ?room=... и рабочим ice URL.
   - Не открывайте общую комнату без правил Firebase — см. FIREBASE_RULES_EXAMPLE.txt
   - Дальше: свои аккаунты, приглашения, отзыв — отдельный бэкенд.
