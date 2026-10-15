---
description: Фаза 2: веб-админка (класс AdminWebHandler в telegram_bot.py). Читай AGENTS.md
---

Фаза 2: веб-админка (класс AdminWebHandler в telegram_bot.py). Читай AGENTS.md.

Проблемы:
- Валидными «паролями» считаются: сгенерированный ключ, ADMIN_WEB_PASSWORD (по умолчанию «roland») и числовой Telegram ID админа.
- Пароль передаётся в URL (GET /login?pw=...), значение cookie равно самому паролю, нет Secure, нет лимита попыток, сравнение не constant-time.
- Сервер слушает 0.0.0.0/:: по HTTP; бот сам поднимает публичный туннель localhost.run и шлёт админу ссылку с ключом в URL.

Задачи:
1. Вход только через POST /login (форма). Пароль сравнивай через hmac.compare_digest с значением из config.
2. После входа выдавай случайный session id (secrets.token_urlsafe(32)), храни серверно (в памяти или SQLite) со сроком жизни; в cookie только этот id: HttpOnly, SameSite=Strict, Secure (если включён TLS/прокси через config).
3. Лимит попыток входа: не более 5 за 10 минут на IP, затем блокировка на 15 минут (с логом).
4. Удали из проверок числовой ID и любые запасные ключи. Убери ключи из URL везде, включая сообщения админу в Telegram.
5. Все изменяющие действия (/harvester_toggle, /harvester_sync, /harvester_order, /send_hot/...) переведи на POST с CSRF-токеном сессии.
6. По умолчанию слушай только 127.0.0.1 (WEB_HOST). Автозапуск туннеля localhost.run (start_cloudflared_tunnel_supervisor) оставь только если ENABLE_PUBLIC_TUNNEL=true; по умолчанию выключено; в документации напиши, что безопаснее использовать Tailscale или Cloudflare Access.
7. Добавь заголовки безопасности: X-Content-Type-Options, X-Frame-Options, Content-Security-Policy (минимальную).
8. Тесты на: неверный пароль, блокировка после лимита, доступ без сессии к /, /files/..., /send_hot/... возвращает редирект на вход, GET не выполняет изменяющих действий.

Критерий готовности: ни один маршрут, кроме публичного каталога (если включён), не доступен без сессии; отчёт по формату AGENTS.md.
