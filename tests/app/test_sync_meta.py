"""
Тесты этапа S2: обогащение метаданных, миграции, sync_catalog, CLI лицензий и такдауна.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import sticker_app.config as config
from sticker_app.catalog_reader import get_sticker, list_popular_tags, list_stickers
from sticker_app.db import get_app_db, run_migrations
from sticker_app.main import app
from sticker_app.set_license import set_license
from sticker_app.sync_catalog import sync_catalog
from sticker_app.tag_extractor import extract_tags
from tests.app.conftest import DemoCatalogContext


def test_migrations_and_schema(tmp_path: Path) -> None:
    """Проверка создания таблиц asset_meta и license_audit при миграциях."""
    app_db = tmp_path / "app.db"
    applied = run_migrations(db_path=app_db)
    assert "001_initial.sql" in applied

    # Повторный запуск идемпотентен
    applied_again = run_migrations(db_path=app_db)
    assert len(applied_again) == 0

    conn = get_app_db(app_db)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "asset_meta" in tables
    assert "license_audit" in tables
    assert "schema_migrations" in tables
    conn.close()


def test_tag_extractor_cleaning() -> None:
    """Проверка очистки тегов от префиксов (03_), стоп-слов и пунктуации."""
    tags = extract_tags(
        category_lvl1="03_Одиночные_Стикеры",
        category_lvl2="Сериалы_и_Кино",
        topic_name="Cyberpunk 2077",
        folder_slug="Cyberpunk_2077",
        title="Cyberpunk Samurai Logo Red Edition!",
        search_queries='["cyberpunk 2077 stickers", "samurai logo png", "lucy edgerunners"]',
        max_tags=12,
    )

    # Проверяем отсутствие технических префиксов
    for t in tags:
        assert not t.startswith("03_")
        assert not t.startswith("01_")
        assert "_" not in t
        assert t == t.lower()

    # Проверяем отсутствие стоп-слов
    assert "стикеры" not in tags
    assert "наклейки" not in tags
    assert "одиночные" not in tags
    assert "png" not in tags
    assert "и" not in tags

    # Проверяем наличие ключевых слов
    assert "cyberpunk" in tags
    assert "samurai" in tags
    assert "logo" in tags
    assert "2077" in tags
    assert "сериалы" in tags
    assert "кино" in tags
    assert len(tags) <= 12


def test_sync_catalog_and_idempotency(demo_catalog: DemoCatalogContext, tmp_path: Path) -> None:
    """Синхронизация каталога и проверка идемпотентности."""
    harvester_db = demo_catalog.db_path
    staging_dir = demo_catalog.storage_root
    app_db = tmp_path / "app.db"

    # Первый запуск
    stats1 = sync_catalog(
        harvester_db_path=harvester_db,
        app_db_path=app_db,
        storage_root=staging_dir,
    )
    assert stats1["processed"] > 0
    assert stats1["usable"] == stats1["processed"]
    assert stats1["takedown"] == 0

    conn = get_app_db(app_db)
    count1 = conn.execute("SELECT COUNT(*) FROM asset_meta").fetchone()[0]
    assert count1 == stats1["processed"]

    # Проверяем наполнение одного макета
    sample = conn.execute("SELECT * FROM asset_meta LIMIT 1").fetchone()
    assert sample["license"] in ("own", "licensed", "cc0", "unknown")
    assert sample["aspect"] > 0
    assert 0.0 <= sample["alpha_coverage"] <= 1.0
    assert sample["tags_json"] != "[]"
    assert sample["palette_json"] != "[]"
    conn.close()

    # Второй запуск (проверка идемпотентности)
    stats2 = sync_catalog(
        harvester_db_path=harvester_db,
        app_db_path=app_db,
        storage_root=staging_dir,
    )
    assert stats2["processed"] == stats1["processed"]

    conn = get_app_db(app_db)
    count2 = conn.execute("SELECT COUNT(*) FROM asset_meta").fetchone()[0]
    assert count2 == count1
    conn.close()


def test_manual_license_retention_after_sync(demo_catalog: DemoCatalogContext, tmp_path: Path) -> None:
    """Проверка того, что ручная лицензия не затирается последующим sync_catalog."""
    harvester_db = demo_catalog.db_path
    staging_dir = demo_catalog.storage_root
    app_db = tmp_path / "app.db"

    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    # Получаем первый asset_id
    conn = get_app_db(app_db)
    first_asset = conn.execute("SELECT asset_id FROM asset_meta LIMIT 1").fetchone()["asset_id"]
    conn.close()

    # Вручную меняем лицензию через set_license
    res = set_license(
        asset_id=first_asset,
        new_license="licensed",
        reason="Manual contract verification",
        changed_by="admin",
        app_db_path=app_db,
        harvester_db_path=harvester_db,
    )
    assert res["updated"] == 1
    assert res["audited"] == 1

    conn = get_app_db(app_db)
    row = conn.execute("SELECT license FROM asset_meta WHERE asset_id = ?", (first_asset,)).fetchone()
    assert row["license"] == "licensed"

    audit_row = conn.execute(
        "SELECT * FROM license_audit WHERE asset_id = ?", (first_asset,)
    ).fetchone()
    assert audit_row["new_license"] == "licensed"
    assert audit_row["changed_by"] == "admin"
    conn.close()

    # Перезапускаем sync_catalog
    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    # Лицензия должна остаться 'licensed'!
    conn = get_app_db(app_db)
    row_after = conn.execute(
        "SELECT license FROM asset_meta WHERE asset_id = ?", (first_asset,)
    ).fetchone()
    assert row_after["license"] == "licensed"
    conn.close()


def test_batch_topic_license_update(demo_catalog: DemoCatalogContext, tmp_path: Path) -> None:
    """Пакетное обновление лицензий для всей темы."""
    harvester_db = demo_catalog.db_path
    staging_dir = demo_catalog.storage_root
    app_db = tmp_path / "app.db"

    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    # Меняем лицензию для всех стикеров темы 'retro_wave'
    res = set_license(
        topic_slug="retro_wave",
        new_license="cc0",
        reason="Open domain release",
        changed_by="rights_holder",
        app_db_path=app_db,
        harvester_db_path=harvester_db,
    )
    assert res["updated"] > 0
    assert res["audited"] > 0

    conn = get_app_db(app_db)
    rows = conn.execute(
        "SELECT license FROM asset_meta WHERE asset_id IN ({})".format(
            ",".join(f"'{aid}'" for aid in res["target_ids"])
        )
    ).fetchall()
    assert len(rows) == res["updated"]
    for r in rows:
        assert r["license"] == "cc0"
    conn.close()


def test_takedown_and_license_filter(demo_catalog: DemoCatalogContext, tmp_path: Path, monkeypatch) -> None:
    """Проверка фильтрации: takedown никогда не попадает, unknown зависит от LICENSE_FILTER."""
    harvester_db = demo_catalog.db_path
    staging_dir = demo_catalog.storage_root
    app_db = tmp_path / "app.db"

    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    conn = get_app_db(app_db)
    all_assets = [r["asset_id"] for r in conn.execute("SELECT asset_id FROM asset_meta LIMIT 2").fetchall()]
    conn.close()
    asset_unknown = all_assets[0]
    asset_takedown = all_assets[1]

    # Помечаем один как unknown, а второй в takedown
    set_license(asset_id=asset_unknown, new_license="unknown", app_db_path=app_db, harvester_db_path=harvester_db)
    set_license(asset_id=asset_takedown, takedown=1, reason="DMCA notice", app_db_path=app_db, harvester_db_path=harvester_db)

    # 1. При LICENSE_FILTER=off:
    # asset_unknown ДОЛЖЕН попадать (takedown=0)
    # asset_takedown НЕ ДОЛЖЕН попадать НИКОГДА
    monkeypatch.setattr(config, "LICENSE_FILTER", False)
    res_off = list_stickers(
        db_path=harvester_db,
        storage_root=staging_dir,
        app_db_path=app_db,
        page_size=100,
    )
    found_ids_off = {s.asset_id for s in res_off.items}
    assert asset_unknown in found_ids_off
    assert asset_takedown not in found_ids_off

    # get_sticker на takedown макет должен возвращать None
    assert get_sticker(asset_takedown, db_path=harvester_db, storage_root=staging_dir, app_db_path=app_db) is None
    assert get_sticker(asset_unknown, db_path=harvester_db, storage_root=staging_dir, app_db_path=app_db) is not None

    # 2. При LICENSE_FILTER=on:
    # asset_unknown НЕ ДОЛЖЕН попадать
    # asset_takedown НЕ ДОЛЖЕН попадать
    monkeypatch.setattr(config, "LICENSE_FILTER", True)
    # Пересчитываем usable_in_service для sync
    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    res_on = list_stickers(
        db_path=harvester_db,
        storage_root=staging_dir,
        app_db_path=app_db,
        page_size=100,
    )
    found_ids_on = {s.asset_id for s in res_on.items}
    assert asset_unknown not in found_ids_on
    assert asset_takedown not in found_ids_on


def test_api_tags_and_stickers_metadata(demo_catalog: DemoCatalogContext, tmp_path: Path, monkeypatch) -> None:
    """Проверка эндпоинта /api/v1/tags и обогащенных полей в /api/v1/stickers."""
    harvester_db = demo_catalog.db_path
    staging_dir = demo_catalog.storage_root
    app_db = tmp_path / "app.db"

    sync_catalog(harvester_db_path=harvester_db, app_db_path=app_db, storage_root=staging_dir)

    monkeypatch.setattr(config, "HARVESTER_DB_PATH", harvester_db)
    monkeypatch.setattr(config, "STORAGE_ROOT", staging_dir)
    monkeypatch.setattr(config, "APP_DB_PATH", app_db)
    monkeypatch.setattr(config, "LICENSE_FILTER", False)

    client = TestClient(app)

    # 1. Проверяем /api/v1/tags и напрямую функцию list_popular_tags
    direct_tags = list_popular_tags(limit=15, app_db_path=app_db)
    assert len(direct_tags) > 0
    assert direct_tags[0].count >= 1

    resp_tags = client.get("/api/v1/tags?limit=15")
    assert resp_tags.status_code == 200
    tags_data = resp_tags.json()
    assert len(tags_data) == len(direct_tags)
    first_tag = tags_data[0]
    assert "name" in first_tag
    assert "count" in first_tag
    assert first_tag["count"] >= 1

    # 2. Проверяем /api/v1/stickers с полями tags, palette, aspect, alpha_coverage
    resp_stickers = client.get("/api/v1/stickers?page=1&page_size=5")
    assert resp_stickers.status_code == 200
    stickers_data = resp_stickers.json()
    item = stickers_data["items"][0]
    assert "tags" in item
    assert isinstance(item["tags"], list)
    assert len(item["tags"]) > 0
    assert "palette" in item
    assert isinstance(item["palette"], list)
    assert "aspect" in item
    assert "alpha_coverage" in item
    assert 0.0 <= item["alpha_coverage"] <= 1.0

    # 3. Детальный запрос
    resp_detail = client.get(f"/api/v1/stickers/{item['asset_id']}")
    assert resp_detail.status_code == 200
    detail_data = resp_detail.json()
    assert detail_data["tags"] == item["tags"]
    assert detail_data["palette"] == item["palette"]
    assert detail_data["aspect"] == item["aspect"]
