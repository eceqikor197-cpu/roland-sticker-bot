"""Тесты безопасности модуля Harvester — Фаза 5.

Проверяют:
1. HARVESTER_ENABLED=False и PUBLIC_CATALOG=False по умолчанию.
2. Воркер Harvester не запускает цикл при HARVESTER_ENABLED=False.
3. Безопасную миграцию таблицы assets (source_url, license, uploaded_by).
4. Валидацию enum license ('own', 'licensed', 'cc0', 'unknown').
5. Блокировку отправки в Hot Folder для license=unknown без admin_confirmed=True.
6. Разрешение отправки в Hot Folder для license in ('own', 'licensed', 'cc0') и unknown с подтверждением.
7. Защиту веб-каталога: редирект на /login при PUBLIC_CATALOG=False для неаутентифицированных пользователей.
8. Отображение предупреждающего бейджа для unknown лицензий в веб-каталоге.
9. Регистрацию авторских макетов через register_manual_asset и проверку ожидания загрузки.
"""

from __future__ import annotations

import http.client
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Generator, Tuple

import pytest
from PIL import Image

import config
import harvester.config as h_config
import harvester.database as h_db
import harvester.storage.rclone_sync as h_sync
import telegram_bot
from harvester.bot_integration import (
    _waiting_for_manual_upload,
    check_and_consume_waiting_upload,
    register_manual_asset,
    render_harvester_web_catalog,
)
from harvester.worker import run_forever_daemon


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт изолированную тестовую базу данных Harvester."""
    db_path = tmp_path / "test_harvester.db"
    monkeypatch.setattr(h_db, "DB_PATH", db_path)
    monkeypatch.setattr(h_config, "DB_PATH", db_path)
    h_db.init_db()
    return db_path


@pytest.fixture
def temp_hot_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tuple[Path, Path]:
    """Перенаправляет Hot Folder во временный каталог (строгое правило AGENTS.md)."""
    hot_eps = tmp_path / "hot_queue_eps"
    hot_prev = tmp_path / "hot_previews"
    hot_eps.mkdir(parents=True, exist_ok=True)
    hot_prev.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(h_config, "HOT_QUEUE_EPS_DIR", hot_eps)
    monkeypatch.setattr(h_sync, "HOT_QUEUE_EPS_DIR", hot_eps)
    monkeypatch.setattr(h_config, "HOT_PREVIEWS_DIR", hot_prev)
    monkeypatch.setattr(h_sync, "HOT_PREVIEWS_DIR", hot_prev)
    return hot_eps, hot_prev


@pytest.fixture
def test_server() -> Generator[Tuple[str, int], None, None]:
    """Запускает AdminWebHandler на случайном свободном порту 127.0.0.1."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), telegram_bot.AdminWebHandler)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    telegram_bot.web_session_store.clear()
    telegram_bot.login_rate_limiter.reset()

    yield host, port

    server.shutdown()
    server.server_close()
    telegram_bot.web_session_store.clear()
    telegram_bot.login_rate_limiter.reset()


def make_request(
    host: str,
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
) -> Tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    hdrs = dict(headers or {})
    conn.request(method, path, headers=hdrs)
    resp = conn.getresponse()
    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
    resp_body = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, resp_headers, resp_body


def test_defaults_harvester_disabled() -> None:
    """Проверяет безопасные значения по умолчанию: парсер и публичный каталог выключены."""
    assert config.HARVESTER_ENABLED is False
    assert config.PUBLIC_CATALOG is False
    assert h_config.HARVESTER_ENABLED is False
    assert h_config.PUBLIC_CATALOG is False


def test_worker_daemon_exits_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Воркер не начинает цикл обработки, если HARVESTER_ENABLED=False."""
    monkeypatch.setattr(h_config, "HARVESTER_ENABLED", False)
    # run_forever_daemon должен завершиться мгновенно
    run_forever_daemon()


def test_safe_assets_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет миграцию таблицы assets без колонок source_url, license, uploaded_by."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE assets (
            asset_id TEXT PRIMARY KEY,
            topic_id INTEGER NOT NULL,
            category_lvl1 TEXT NOT NULL,
            category_lvl2 TEXT NOT NULL,
            folder_slug TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            title TEXT NOT NULL,
            source_name TEXT NOT NULL,
            phash_hex TEXT NOT NULL,
            width_px INTEGER NOT NULL,
            height_px INTEGER NOT NULL,
            width_mm REAL NOT NULL,
            height_mm REAL NOT NULL,
            offset_mm REAL NOT NULL,
            perf_cut_mode TEXT NOT NULL,
            num_contours INTEGER NOT NULL,
            eps_size_mb REAL NOT NULL,
            rel_folder_path TEXT NOT NULL,
            eps_filename TEXT NOT NULL,
            png_filename TEXT NOT NULL,
            local_eps_path TEXT NOT NULL,
            local_png_path TEXT NOT NULL,
            local_preview_path TEXT NOT NULL,
            gdrive_folder_path TEXT NOT NULL,
            synced_to_gdrive INTEGER NOT NULL DEFAULT 0,
            in_hot_folder INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO assets (
            asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
            topic_name, asset_type, title, source_name, phash_hex,
            width_px, height_px, width_mm, height_mm, offset_mm,
            perf_cut_mode, num_contours, eps_size_mb, rel_folder_path,
            eps_filename, png_filename, local_eps_path, local_png_path,
            local_preview_path, gdrive_folder_path, created_at
        ) VALUES (
            'legacy_1', 1, 'cat1', 'cat2', 'slug',
            'topic', 'single_sticker', 'Legacy 1', 'scraper', '',
            100, 100, 100.0, 100.0, 2.5,
            'none', 1, 0.5, 'c1/c2/slug',
            'legacy_1.eps', 'legacy_1.png', '', '', '', '', 1234567.0
        )
        """
    )
    conn.commit()

    # Запускаем миграцию
    h_db._migrate_assets_table(conn)

    cursor = conn.execute("PRAGMA table_info(assets)")
    cols = {row[1]: row for row in cursor.fetchall()}
    assert "source_url" in cols
    assert "license" in cols
    assert "uploaded_by" in cols

    row = conn.execute("SELECT asset_id, license, source_url, uploaded_by FROM assets WHERE asset_id='legacy_1'").fetchone()
    assert row[0] == "legacy_1"
    assert row[1] == "unknown"  # значение по умолчанию
    assert row[2] == ""
    assert row[3] == ""
    conn.close()


def _dummy_asset(
    asset_id: str,
    license: str,
    local_eps_path: str = "",
    title: str = "Title",
    topic_name: str = "Test",
) -> dict:
    return {
        "asset_id": asset_id,
        "topic_id": 1,
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Разное",
        "folder_slug": "slug",
        "topic_name": topic_name,
        "asset_type": "single_sticker",
        "title": title,
        "source_name": "test",
        "license": license,
        "source_url": "https://example.com/asset",
        "uploaded_by": "123",
        "rel_folder_path": "03_Одиночные_Стикеры/Разное/slug",
        "eps_filename": f"{asset_id}.eps",
        "png_filename": f"{asset_id}.png",
        "local_eps_path": local_eps_path,
        "created_at": 1000.0,
    }


def test_license_enum_validation(temp_db: Path) -> None:
    """Проверяет валидацию поля license при регистрации макета."""
    for valid_lic in ("own", "licensed", "cc0", "unknown"):
        asset_id = f"test_{valid_lic}"
        h_db.register_asset(_dummy_asset(asset_id, valid_lic))
        stored = h_db.get_asset(asset_id)
        assert stored is not None
        assert stored["license"] == valid_lic
        assert stored["source_url"] == "https://example.com/asset"
        assert stored["uploaded_by"] == "123"

    # Невалидное значение нормализуется в unknown
    h_db.register_asset(_dummy_asset("test_invalid_lic", "pirated_or_illegal"))
    stored_inv = h_db.get_asset("test_invalid_lic")
    assert stored_inv is not None
    assert stored_inv["license"] == "unknown"


def test_hot_folder_blocks_unknown_without_admin_confirmation(
    temp_db: Path,
    temp_hot_folders: Tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Макет с license=unknown НЕ отправляется в Hot Folder без явного admin_confirmed=True."""
    hot_eps, _ = temp_hot_folders
    mock_eps = tmp_path / "sample.eps"
    # Файл должен быть > 100 байт для ensure_local_eps_for_asset
    mock_eps.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n" + b"% pad\n" * 25 + b"%%EOF\n")

    h_db.register_asset(_dummy_asset("unlicensed_asset", "unknown", local_eps_path=str(mock_eps)))

    # 1. Без подтверждения — блокировка
    res = h_sync.copy_harvester_asset_to_hot_folder("unlicensed_asset", admin_confirmed=False)
    assert res is None
    assert list(hot_eps.glob("*.eps")) == []

    # 2. С подтверждением администратора — отправка успешна
    res_ok = h_sync.copy_harvester_asset_to_hot_folder("unlicensed_asset", admin_confirmed=True)
    assert res_ok is not None
    assert res_ok.exists()
    assert res_ok.parent == hot_eps


def test_hot_folder_allows_licensed_assets_without_confirmation(
    temp_db: Path,
    temp_hot_folders: Tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Макеты с license in ('own', 'licensed', 'cc0') отправляются в Hot Folder штатно."""
    hot_eps, _ = temp_hot_folders
    mock_eps = tmp_path / "licensed.eps"
    mock_eps.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n" + b"% pad\n" * 25 + b"%%EOF\n")

    for lic in ("own", "licensed", "cc0"):
        aid = f"asset_{lic}"
        h_db.register_asset(_dummy_asset(aid, lic, local_eps_path=str(mock_eps)))
        res = h_sync.copy_harvester_asset_to_hot_folder(aid, admin_confirmed=False)
        assert res is not None
        assert res.exists()
        assert res.parent == hot_eps


def test_web_catalog_access_gated(test_server: Tuple[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """При PUBLIC_CATALOG=False каталог /harvester и файлы /harvester_file/* требуют авторизации."""
    host, port = test_server

    # 1. PUBLIC_CATALOG=False, без cookie -> 302/303 Redirect на /login
    monkeypatch.setattr(telegram_bot, "PUBLIC_CATALOG", False)
    status, headers, _ = make_request(host, port, "GET", "/harvester")
    assert status in (302, 303)
    assert headers.get("location", "").startswith("/login")

    status_f, headers_f, _ = make_request(host, port, "GET", "/harvester_file/test123_preview.jpg")
    assert status_f in (302, 303)
    assert headers_f.get("location", "").startswith("/login")

    # 2. PUBLIC_CATALOG=False, с сессией админа -> 200 OK
    session_id, _ = telegram_bot.web_session_store.create_session()
    auth_headers = {"Cookie": f"roland_session={session_id}"}
    status_admin, _, body_admin = make_request(host, port, "GET", "/harvester", headers=auth_headers)
    assert status_admin == 200
    assert "каталог" in body_admin.lower() or "стикер" in body_admin.lower() or "отключён" in body_admin.lower()

    # 3. PUBLIC_CATALOG=True, без cookie -> 200 OK
    monkeypatch.setattr(telegram_bot, "PUBLIC_CATALOG", True)
    status_pub, _, body_pub = make_request(host, port, "GET", "/harvester")
    assert status_pub == 200


def test_render_web_catalog_badges_and_disabled_view(
    temp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет рендеринг предупреждающего бейджа для unknown и плашки отключённого модуля."""
    import harvester.bot_integration as h_bi

    # 1. HARVESTER_ENABLED=False
    monkeypatch.setattr(h_config, "HARVESTER_ENABLED", False)
    monkeypatch.setattr(h_bi, "HARVESTER_ENABLED", False)
    raw_off = render_harvester_web_catalog()
    html_off = raw_off.decode("utf-8", errors="replace") if isinstance(raw_off, bytes) else str(raw_off)
    assert "Модуль отключён" in html_off

    # 2. HARVESTER_ENABLED=True
    monkeypatch.setattr(h_config, "HARVESTER_ENABLED", True)
    monkeypatch.setattr(h_bi, "HARVESTER_ENABLED", True)
    h_db.register_asset(_dummy_asset(
        "item_unknown",
        "unknown",
        title="Тестовый макет без прав",
        topic_name="Тестовый макет без прав",
    ))
    h_db.register_asset(_dummy_asset(
        "item_own",
        "own",
        title="Авторский стикер",
        topic_name="Авторский стикер",
    ))

    # Для обычного пользователя — бейдж с предупреждением
    raw_user = render_harvester_web_catalog(is_admin=False)
    html_user = raw_user.decode("utf-8", errors="replace") if isinstance(raw_user, bytes) else str(raw_user)
    assert "Лицензия не подтверждена, не печатать на продажу" in html_user
    assert "Собственный макет" in html_user

    # Для админа — кнопка с обязательным confirm_unlicensed=1
    raw_admin = render_harvester_web_catalog(is_admin=True)
    html_admin = raw_admin.decode("utf-8", errors="replace") if isinstance(raw_admin, bytes) else str(raw_admin)
    assert 'name="confirm_unlicensed"' in html_admin
    assert 'value="1"' in html_admin
    assert "⚠️ В Hot Folder (Подтвердить)" in html_admin


def test_manual_asset_registration(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет регистрацию авторского макета через register_manual_asset."""
    staging_dir = tmp_path / "staging"
    previews_dir = tmp_path / "previews"
    monkeypatch.setattr(h_config, "STAGING_DIR", staging_dir)
    monkeypatch.setattr(h_config, "PREVIEWS_DIR", previews_dir)
    import harvester.bot_integration as h_bi
    monkeypatch.setattr(h_bi, "STAGING_DIR", staging_dir)
    monkeypatch.setattr(h_bi, "PREVIEWS_DIR", previews_dir)

    # Создаём простое изображение с альфа-каналом
    test_img_path = tmp_path / "my_author_sticker.png"
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    for x in range(50, 150):
        for y in range(50, 150):
            im.putpixel((x, y), (255, 0, 128, 255))
    im.save(test_img_path)

    asset = register_manual_asset(
        image_path=test_img_path,
        title="Мой Авторский Стикер",
        license_type="own",
        uploaded_by=777888,
        topic_name="Фирменные лого",
    )

    assert asset["license"] == "own"
    assert asset["uploaded_by"] == "777888"
    assert asset["source_name"] == "manual_upload"
    assert Path(asset["local_eps_path"]).exists()
    assert Path(asset["local_png_path"]).exists()

    db_asset = h_db.get_asset(asset["asset_id"])
    assert db_asset is not None
    assert db_asset["license"] == "own"
    assert db_asset["uploaded_by"] == "777888"


def test_waiting_for_manual_upload_flow() -> None:
    """Проверяет жизненный цикл состояния ожидания загрузки авторского макета."""
    user_id = 123456
    _waiting_for_manual_upload[user_id] = {
        "license": "licensed",
        "topic": "Game Art",
        "ts": 1000000000.0,  # устаревший timestamp
    }
    # Протухший запрос (>600 сек) сбрасывается и возвращает None
    expired = check_and_consume_waiting_upload(user_id)
    assert expired is None
    assert user_id not in _waiting_for_manual_upload

    # Свежий запрос
    import time
    _waiting_for_manual_upload[user_id] = {
        "license": "cc0",
        "topic": "Public Art",
        "ts": time.time(),
    }
    fresh = check_and_consume_waiting_upload(user_id)
    assert fresh is not None
    assert fresh["license"] == "cc0"
    assert fresh["topic"] == "Public Art"
    # Повторный вызов возвращает None (одноразовое потребление)
    assert check_and_consume_waiting_upload(user_id) is None
