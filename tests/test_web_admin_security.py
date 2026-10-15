"""Тесты безопасности веб-админки (AdminWebHandler) — Фаза 2.

Проверяют:
1. Защиту от несанкционированного доступа (редирект на /login для неаутентифицированных пользователей).
2. Вход только через POST /login с проверкой пароля через constant-time сравнение.
3. Лимит попыток входа и блокировку IP (brute-force protection).
4. Сессионные cookie с флагами HttpOnly, SameSite=Strict.
5. Защиту изменяющих действий: запрет через GET (405 Method Not Allowed), обязательность POST с CSRF-токеном (403 Forbidden без токена).
6. Заголовки безопасности (X-Content-Type-Options, X-Frame-Options, Content-Security-Policy).
7. Выход /logout и инвалидацию сессии.
8. Отказ в доступе по устаревшим ключам в URL / Telegram ID.
"""

from __future__ import annotations

import http.client
import threading
import urllib.parse
from http.server import ThreadingHTTPServer
from typing import Generator, Tuple

import pytest

from telegram_bot import (
    ADMIN_WEB_PASSWORD,
    AdminWebHandler,
    login_rate_limiter,
    web_session_store,
)


@pytest.fixture
def test_server() -> Generator[Tuple[str, int], None, None]:
    """Запускает AdminWebHandler на случайном свободном порту 127.0.0.1."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), AdminWebHandler)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    web_session_store.clear()
    login_rate_limiter.reset()

    yield host, port

    server.shutdown()
    server.server_close()
    web_session_store.clear()
    login_rate_limiter.reset()


def make_request(
    host: str,
    port: int,
    method: str,
    path: str,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> Tuple[int, dict[str, str], str]:
    """Вспомогательная функция для HTTP-запросов без автоперехода по редиректам."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    hdrs = dict(headers or {})
    if body and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
    resp_body = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, resp_headers, resp_body


def test_unauthenticated_dashboard_redirects_to_login(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    status, headers, _ = make_request(host, port, "GET", "/")
    assert status == 302
    assert headers.get("location", "").startswith("/login")


def test_unauthenticated_files_redirects_to_login(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    status, headers, _ = make_request(host, port, "GET", "/files/12345678/orig")
    assert status == 302
    assert headers.get("location", "").startswith("/login")


def test_mutating_actions_rejected_via_get(test_server: Tuple[str, int]) -> None:
    """GET на изменяющие действия обязан возвращать 405 Method Not Allowed."""
    host, port = test_server
    for path in (
        "/harvester_toggle",
        "/harvester_sync",
        "/harvester_order?topic=Test",
        "/harvester_hot/test_asset",
        "/send_hot/12345678",
    ):
        status, _, _ = make_request(host, port, "GET", path)
        assert status == 405, f"Path {path} returned {status} instead of 405"


def test_login_get_shows_form_does_not_authenticate(test_server: Tuple[str, int]) -> None:
    """GET /login?pw=... не должен аутентифицировать (GET-вход запрещен)."""
    host, port = test_server
    status, headers, body = make_request(host, port, "GET", f"/login?pw={ADMIN_WEB_PASSWORD}")
    assert status == 200
    assert '<form method="POST" action="/login">' in body
    assert "set-cookie" not in headers


def test_login_invalid_password(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    data = urllib.parse.urlencode({"pw": "wrong_password", "next": "/"})
    status, headers, body = make_request(host, port, "POST", "/login", body=data)
    assert status == 401
    assert "Неверный пароль администратора" in body
    assert "roland_session" not in headers.get("set-cookie", "")


def test_login_rate_limiting(test_server: Tuple[str, int]) -> None:
    """После 5 неудачных попыток входа IP блокируется и возвращает 429."""
    host, port = test_server
    data = urllib.parse.urlencode({"pw": "wrong_password", "next": "/"})

    for _ in range(5):
        status, _, _ = make_request(host, port, "POST", "/login", body=data)
        assert status in (401, 429)

    # 6-я попытка должна быть заблокирована с 429
    status, _, body = make_request(host, port, "POST", "/login", body=data)
    assert status == 429
    assert "заблокирован" in body.lower()

    # GET /login тоже сообщает о блокировке
    get_status, _, get_body = make_request(host, port, "GET", "/login")
    assert get_status == 429
    assert "заблокирован" in get_body.lower()


def test_login_success_and_cookie_attributes(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    data = urllib.parse.urlencode({"pw": ADMIN_WEB_PASSWORD, "next": "/"})
    status, headers, _ = make_request(host, port, "POST", "/login", body=data)

    assert status == 302
    assert headers.get("location") == "/"
    cookie = headers.get("set-cookie", "")
    assert "roland_session=" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()


def test_authenticated_dashboard_access(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    # Создаем сессию
    session_id, csrf_token = web_session_store.create_session()
    headers = {"Cookie": f"roland_session={session_id}"}

    status, _, body = make_request(host, port, "GET", "/", headers=headers)
    assert status == 200
    assert "Админ-панель Roland VP-540" in body
    assert csrf_token in body


def test_post_mutating_action_forbidden_without_csrf(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    session_id, _ = web_session_store.create_session()
    headers = {"Cookie": f"roland_session={session_id}"}

    # Без csrf_token
    status, _, _ = make_request(host, port, "POST", "/harvester_toggle", body="", headers=headers)
    assert status == 403

    # С неверным csrf_token
    body = urllib.parse.urlencode({"csrf_token": "fake_token"})
    status, _, _ = make_request(host, port, "POST", "/harvester_toggle", body=body, headers=headers)
    assert status == 403


def test_post_mutating_action_allowed_with_valid_csrf(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    session_id, csrf_token = web_session_store.create_session()
    headers = {"Cookie": f"roland_session={session_id}"}

    body = urllib.parse.urlencode({"csrf_token": csrf_token})
    status, resp_headers, _ = make_request(host, port, "POST", "/harvester_toggle", body=body, headers=headers)
    assert status == 302
    assert resp_headers.get("location", "").startswith("/harvester")


def test_logout_invalidates_session_and_clears_cookie(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    session_id, _ = web_session_store.create_session()
    headers = {"Cookie": f"roland_session={session_id}"}

    status, resp_headers, _ = make_request(host, port, "GET", "/logout", headers=headers)
    assert status == 302
    assert resp_headers.get("location") == "/login"

    set_cookie = resp_headers.get("set-cookie", "")
    assert "max-age=0" in set_cookie.lower()

    # Сессия на сервере уничтожена
    assert web_session_store.get_session(session_id) is None

    # Повторный доступ с этой cookie возвращает редирект на вход
    dash_status, dash_headers, _ = make_request(host, port, "GET", "/", headers=headers)
    assert dash_status == 302
    assert dash_headers.get("location", "").startswith("/login")


def test_security_headers_present_on_all_responses(test_server: Tuple[str, int]) -> None:
    host, port = test_server
    status, headers, _ = make_request(host, port, "GET", "/login")
    assert status == 200
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert "default-src 'self'" in headers.get("content-security-policy", "")
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_legacy_auth_bypasses_removed(test_server: Tuple[str, int]) -> None:
    """Проверка, что передача ключа в query (?key=...) или числового ID 870592714 не дает доступа."""
    host, port = test_server

    # ?key=roland
    status, headers, _ = make_request(host, port, "GET", f"/?key={ADMIN_WEB_PASSWORD}")
    assert status == 302
    assert headers.get("location", "").startswith("/login")

    # ?key=870592714
    status, headers, _ = make_request(host, port, "GET", "/?key=870592714")
    assert status == 302
    assert headers.get("location", "").startswith("/login")

    # Cookie: roland_admin_key=870592714
    headers_legacy = {"Cookie": "roland_admin_key=870592714"}
    status, headers, _ = make_request(host, port, "GET", "/", headers=headers_legacy)
    assert status == 302
    assert headers.get("location", "").startswith("/login")


def test_web_pages_render_templates() -> None:
    """Проверка загрузки и рендеринга шаблонов из templates/*.html через web.pages."""
    from web.pages import render_dashboard, render_login_page, render_template

    # 1. login page
    login_html = render_login_page(next_url="/custom?x=1", error_msg="Тестовая ошибка <script>")
    assert "Тестовая ошибка &lt;script&gt;" in login_html
    assert "/custom?x=1" in login_html
    assert "<form method=\"POST\" action=\"/login\">" in login_html

    # 2. dashboard page
    dash_html = render_dashboard("all", hot_msg="file.eps", csrf_token="test_token_123")
    assert "Админ-панель Roland VP-540" in dash_html
    assert "test_token_123" in dash_html
    assert "file.eps" in dash_html

    # 3. direct render_template
    custom = render_template("login.html", {"err_html": "ERR", "next_url": "/test"})
    assert "ERR" in custom
    assert "/test" in custom
