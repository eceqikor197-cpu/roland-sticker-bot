# Мини-приложение «Редактор наклейки»

Telegram Mini App, в котором клиент настраивает EPS вместо кнопок бота: размер, ширину и высоту в мм,
кант, фон, режим резки, тираж на А4 или рулон. Превью наклейки, листа А4 и рулона меняется сразу,
цена считается по `pricing.toml`. По кнопке «Получить EPS» сервер делает настоящую конвертацию,
и бот присылает превью и EPS в чат, как раньше.

## Как устроено

- Отдельный процесс `python -m miniapp` (FastAPI + uvicorn), по умолчанию `127.0.0.1:8096`.
  Работает рядом с ботом: та же `bot_storage.db` и папка `bot_workspace/jobs`.
- При первом открытии макета сервер один раз вырезает фон кодом `jpeg2eps.py` и кеширует рисунок
  и силуэт в `jobs/<id>/miniapp/`. Дальше подложка, контур реза и раскладка рисуются в браузере
  (`static/render.js`, `static/geometry.js`), конвертация на каждое движение не запускается.
- `static/geometry.js` повторяет `miniapp/geometry.py` (размеры ячейки как в `jpeg2eps.py`).
  Совпадение проверяет `tests/webapp/geometry.test.mjs` по `tests/golden/miniapp_layout.json`;
  после изменений в геометрии: `python scripts/generate_miniapp_golden.py`.
- Вход только из Telegram: каждый запрос несёт `initData`, сервер проверяет подпись токеном бота.
  Пользователь видит только свои макеты, админ (ADMIN_IDS) все, забаненные не пускаются.
- Готовый EPS шлёт `bot.jobs.process_and_send_job` через Bot API; опрос Telegram этот процесс не запускает.

## API (`/api/v1`, заголовок `X-Telegram-Init-Data`)

| Метод | Что делает |
|---|---|
| `GET /jobs` | последние макеты пользователя |
| `GET /jobs/{id}` | параметры, силуэт, цена |
| `GET /jobs/{id}/source?keep_bg=0|1`, `/artwork.png` | силуэт и вырезанный рисунок для превью |
| `GET /jobs/{id}/preview.png` | последнее превью бота |
| `POST /jobs/{id}/quote` | размеры и цена по параметрам, без конвертации |
| `POST /jobs/{id}/check` | настоящая конвертация во временную папку, PNG превью |
| `POST /jobs/{id}/render` | сохранить параметры, сделать EPS, отправить в чат |

`check` и `render` ограничены 6 конвертациями в минуту на человека (админов не ограничивает).

## Запуск на сервере

1. Публичный HTTPS-адрес (без него Telegram не откроет Mini App).

   **Tailscale Funnel** (без домена, Tailscale на сервере уже есть):
   - в админке Tailscale (Access controls) разрешить Funnel для этой машины и включить HTTPS-сертификаты;
   - `sudo tailscale funnel --bg 8096`;
   - `tailscale funnel status` покажет адрес вида `https://<машина>.<сеть>.ts.net`.

   **Или Cloudflare Tunnel** со своим доменом: `cloudflared tunnel route dns <туннель> miniapp.<домен>`,
   в конфиге туннеля `service: http://localhost:8096`.

2. В `.env`: `MINIAPP_URL=https://<адрес из шага 1>` (адрес должен начинаться с `https://`).
3. Служба `/etc/systemd/system/roland-miniapp.service` (пути как у `roland-bot.service` в README):

   ```ini
   [Unit]
   Description=Roland Sticker Mini App (редактор наклейки)
   After=network.target

   [Service]
   Type=simple
   User=roland
   WorkingDirectory=/opt/roland-sticker-bot
   ExecStart=/opt/roland-sticker-bot/.venv/bin/python -m miniapp
   Restart=always
   RestartSec=5
   EnvironmentFile=/opt/roland-sticker-bot/.env

   [Install]
   WantedBy=multi-user.target
   ```

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now roland-miniapp
   sudo systemctl restart roland-bot   # бот перечитает MINIAPP_URL и покажет кнопки редактора
   ```

4. Проверка: `curl -s -o /dev/null -w '%{http_code}\n' https://<адрес>/` должен вернуть `200`,
   а `https://<адрес>/api/v1/jobs` без Telegram — `401`.

При старте сервер ставит кнопку меню бота «Редактор» (список макетов). Отключить: `MINIAPP_MENU_BUTTON=false`.
Если убрать `MINIAPP_URL`, бот вернётся к кнопкам.
