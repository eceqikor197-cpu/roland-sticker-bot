"""
Тесты API каталога и раздачи изображений (Этап S1).
"""

from __future__ import annotations

import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import sticker_app.config as app_config
from sticker_app.catalog_reader import PathTraversalError, resolve_asset_path
from sticker_app.main import app
from tests.app.conftest import DemoCatalogContext


@pytest.fixture
def client(demo_catalog: DemoCatalogContext) -> TestClient:
    """FastAPI TestClient, работающий с изолированным демо-каталогом."""
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    """Проверка эндпоинта health."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_stickers_list_pagination(client: TestClient) -> None:
    """Проверка пагинации списка стикеров."""
    resp = client.get("/api/v1/stickers?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()

    meta = data["meta"]
    assert meta["page"] == 1
    assert meta["page_size"] == 10
    assert meta["total_items"] == 40
    assert meta["total_pages"] == 4
    assert meta["has_next"] is True
    assert meta["has_prev"] is False

    items = data["items"]
    assert len(items) == 10

    # Проверяем, что в выдаче нет файловых путей
    first = items[0]
    assert "local_png_path" not in first
    assert "local_eps_path" not in first
    assert "local_preview_path" not in first
    assert "rel_folder_path" not in first
    assert first["asset_id"]
    assert first["thumb_url"].startswith("/api/v1/stickers/")
    assert first["preview_url"].startswith("/api/v1/stickers/")
    assert first["aspect_ratio"] > 0
    assert first["usable_in_service"] is True


def test_stickers_list_filters(client: TestClient) -> None:
    """Проверка фильтрации по категории, теме и поисковому запросу."""
    # Фильтр по категории 2-го уровня
    resp = client.get("/api/v1/stickers?category_lvl2=Эстетика_арт_и_дизайн&page_size=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total_items"] == 16  # 2 темы по 8 стикеров
    assert len(data["items"]) == 16

    # Фильтр по слагу темы
    resp2 = client.get("/api/v1/stickers?folder_slug=Retro_Wave")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["meta"]["total_items"] == 8
    for item in data2["items"]:
        assert item["folder_slug"] == "Retro_Wave"

    # Поиск по названию
    resp3 = client.get("/api/v1/stickers?search=Закат")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["meta"]["total_items"] >= 1
    assert any("Закат" in item["title"] for item in data3["items"])


def test_get_sticker_detail(client: TestClient) -> None:
    """Проверка получения детальной информации о стикере."""
    list_resp = client.get("/api/v1/stickers?page=1&page_size=1")
    asset_id = list_resp.json()["items"][0]["asset_id"]

    detail_resp = client.get(f"/api/v1/stickers/{asset_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    assert detail["asset_id"] == asset_id
    assert detail["width_px"] > 0
    assert detail["height_px"] > 0
    assert detail["width_mm"] > 0
    assert detail["height_mm"] > 0
    assert detail["offset_mm"] == 2.5
    assert detail["perf_cut_mode"] == "die_cut"
    assert detail["full_image_url"].endswith("size=full")


def test_get_sticker_not_found(client: TestClient) -> None:
    """Проверка 404 для несуществующего стикера."""
    resp = client.get("/api/v1/stickers/non_existent_id_12345")
    assert resp.status_code == 404


def test_image_delivery_thumb_and_caching(client: TestClient, demo_catalog: DemoCatalogContext) -> None:
    """Проверка генерации миниатюры thumb (WebP, альфа, max 256px, кэширование)."""
    list_resp = client.get("/api/v1/stickers?page=1&page_size=1")
    asset_id = list_resp.json()["items"][0]["asset_id"]

    # 1. Первый запрос: генерация и сохранение в кэш
    img_resp = client.get(f"/api/v1/stickers/{asset_id}/image?size=thumb")
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"] == "image/webp"
    assert "public, max-age=86400" in img_resp.headers["cache-control"]
    etag = img_resp.headers.get("etag")
    assert etag is not None

    # Проверка геометрии и альфа-канала через Pillow
    img = Image.open(io.BytesIO(img_resp.content))
    assert img.format == "WEBP"
    assert img.mode == "RGBA"
    assert img.width <= 256 and img.height <= 256
    alpha = img.getchannel("A")
    min_a, _ = alpha.getextrema()
    assert min_a == 0, "Альфа-канал должен сохранять прозрачность фона"

    # Проверка, что файл сохранён в CACHE_DIR
    cached_path = app_config.CACHE_DIR / "thumb" / f"{asset_id}.webp"
    assert cached_path.exists()
    mtime1 = cached_path.stat().st_mtime_ns

    # 2. Второй запрос: должен браться из кэша
    img_resp2 = client.get(f"/api/v1/stickers/{asset_id}/image?size=thumb")
    assert img_resp2.status_code == 200
    mtime2 = cached_path.stat().st_mtime_ns
    assert mtime1 == mtime2


def test_image_delivery_preview_and_full(client: TestClient) -> None:
    """Проверка раздачи preview (1024px WebP) и full (оригинал PNG)."""
    list_resp = client.get("/api/v1/stickers?page=1&page_size=1")
    asset_id = list_resp.json()["items"][0]["asset_id"]

    # preview
    prev_resp = client.get(f"/api/v1/stickers/{asset_id}/image?size=preview")
    assert prev_resp.status_code == 200
    assert prev_resp.headers["content-type"] == "image/webp"
    prev_img = Image.open(io.BytesIO(prev_resp.content))
    assert prev_img.width <= 1024 and prev_img.height <= 1024

    # full
    full_resp = client.get(f"/api/v1/stickers/{asset_id}/image?size=full")
    assert full_resp.status_code == 200
    assert full_resp.headers["content-type"] == "image/png"

    # invalid size
    bad_resp = client.get(f"/api/v1/stickers/{asset_id}/image?size=huge")
    assert bad_resp.status_code == 400


def test_etag_304_not_modified(client: TestClient) -> None:
    """Проверка поддержки ETag и возврата 304 Not Modified."""
    list_resp = client.get("/api/v1/stickers?page=1&page_size=1")
    asset_id = list_resp.json()["items"][0]["asset_id"]

    resp = client.get(f"/api/v1/stickers/{asset_id}/image?size=thumb")
    etag = resp.headers["etag"]

    # Повторный запрос с If-None-Match
    resp_304 = client.get(
        f"/api/v1/stickers/{asset_id}/image?size=thumb",
        headers={"If-None-Match": etag},
    )
    assert resp_304.status_code == 304
    assert resp_304.content == b""


def test_path_traversal_protection(client: TestClient, demo_catalog: DemoCatalogContext) -> None:
    """Проверка защиты от path traversal при подделке относительных путей."""
    storage_root = demo_catalog.storage_root

    # Прямая проверка функции resolve_asset_path
    with pytest.raises(PathTraversalError):
        resolve_asset_path(storage_root, "../../etc", "passwd")

    with pytest.raises(PathTraversalError):
        resolve_asset_path(storage_root, "some/path/../../../", "secret.txt")

    # Вставка вредоносной записи в базу данных
    conn = sqlite3.connect(str(demo_catalog.db_path))
    try:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                topic_name, asset_type, title, source_name, source_url,
                license, phash_hex, width_px, height_px, width_mm, height_mm,
                offset_mm, perf_cut_mode, num_contours, eps_size_mb,
                rel_folder_path, eps_filename, png_filename, local_eps_path,
                local_png_path, local_preview_path, gdrive_folder_path, created_at
            ) VALUES (
                'malicious_01', 1, 'cat1', 'cat2', 'slug',
                'Hacker Topic', 'single_sticker', 'Exploit', 'src', '',
                'own', '0000000000000000', 100, 100, 50, 50,
                2.5, 'die_cut', 1, 0.1,
                '../../etc', 'exploit.eps', 'passwd', '',
                '', '', '', ?
            )
            """,
            (time.time(),),
        )
        conn.commit()
    finally:
        conn.close()

    # Вредоносный стикер должен быть исключён из публичной выдачи
    list_resp = client.get("/api/v1/stickers?page=1&page_size=100")
    assert list_resp.status_code == 200
    asset_ids = [item["asset_id"] for item in list_resp.json()["items"]]
    assert "malicious_01" not in asset_ids

    # Запрос по деталям или изображению должен вернуть 404
    assert client.get("/api/v1/stickers/malicious_01").status_code == 404
    assert client.get("/api/v1/stickers/malicious_01/image?size=thumb").status_code == 404


def test_categories_tree(client: TestClient) -> None:
    """Проверка эндпоинта иерархии категорий."""
    resp = client.get("/api/v1/categories")
    assert resp.status_code == 200
    trees = resp.json()

    assert len(trees) >= 1
    first_tree = trees[0]
    assert first_tree["category_lvl1"] == "03_Одиночные_Стикеры"
    assert len(first_tree["subcategories"]) >= 4

    subcat = first_tree["subcategories"][0]
    assert len(subcat["topics"]) >= 1
    assert subcat["topics"][0]["stickers_count"] == 8


def test_license_filter_behavior(
    client: TestClient,
    demo_catalog: DemoCatalogContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка работы фильтра лицензий (LICENSE_FILTER on/off)."""
    # Создаём макет с неизвестной лицензией и реальным файлом
    conn = sqlite3.connect(str(demo_catalog.db_path))
    try:
        # Берём путь существующего стикера
        row = conn.execute("SELECT rel_folder_path, png_filename, eps_filename FROM assets LIMIT 1").fetchone()
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                topic_name, asset_type, title, source_name, source_url,
                license, phash_hex, width_px, height_px, width_mm, height_mm,
                offset_mm, perf_cut_mode, num_contours, eps_size_mb,
                rel_folder_path, eps_filename, png_filename, local_eps_path,
                local_png_path, local_preview_path, gdrive_folder_path, created_at
            ) VALUES (
                'unknown_lic_01', 1, '03_Одиночные_Стикеры', 'Эстетика_арт_и_дизайн', 'Retro_Wave',
                'Ретро Вейв', 'single_sticker', 'Unknown License Sticker', 'web', '',
                'unknown', '1111111111111111', 600, 600, 70, 70,
                2.5, 'die_cut', 1, 0.1,
                ?, ?, ?, '',
                '', '', '', ?
            )
            """,
            (row[0], row[2], row[1], time.time() + 100),
        )
        conn.commit()
    finally:
        conn.close()

    # При LICENSE_FILTER=False макет доступен
    monkeypatch.setattr(app_config, "LICENSE_FILTER", False)
    resp_off = client.get("/api/v1/stickers?page=1&page_size=50")
    ids_off = [it["asset_id"] for it in resp_off.json()["items"]]
    assert "unknown_lic_01" in ids_off
    assert client.get("/api/v1/stickers/unknown_lic_01").status_code == 200

    # При LICENSE_FILTER=True макет исключён
    monkeypatch.setattr(app_config, "LICENSE_FILTER", True)
    resp_on = client.get("/api/v1/stickers?page=1&page_size=50")
    ids_on = [it["asset_id"] for it in resp_on.json()["items"]]
    assert "unknown_lic_01" not in ids_on
    assert client.get("/api/v1/stickers/unknown_lic_01").status_code == 404
