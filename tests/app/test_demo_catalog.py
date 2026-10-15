"""
Тесты для генерации демо-каталога и скрипта инспекции (Этап S0).
"""

from __future__ import annotations

import sqlite3

import pytest
from PIL import Image

from scripts.inspect_catalog import inspect_catalog, open_db_readonly
from tests.app.conftest import DemoCatalogContext


def test_demo_catalog_structure(demo_catalog: DemoCatalogContext) -> None:
    """Проверка структуры и наполнения сгенерированного демо-каталога."""
    assert demo_catalog.db_path.exists()
    assert demo_catalog.storage_root.exists()

    conn = sqlite3.connect(str(demo_catalog.db_path))
    try:
        # Проверяем темы
        topics = conn.execute("SELECT * FROM topics").fetchall()
        assert len(topics) == 5

        # Проверяем макеты
        assets = conn.execute("SELECT * FROM assets").fetchall()
        assert len(assets) == 40

        # Все макеты должны иметь asset_type='single_sticker' и license='own'
        for r in assets:
            row_dict = dict(zip([col[0] for col in conn.execute("SELECT * FROM assets LIMIT 1").description], r))
            assert row_dict["asset_type"] == "single_sticker"
            assert row_dict["license"] == "own"
            assert row_dict["category_lvl1"] == "03_Одиночные_Стикеры"
            assert row_dict["width_mm"] > 0
            assert row_dict["height_mm"] > 0
            assert row_dict["width_px"] > 0
            assert row_dict["height_px"] > 0

            # Проверяем существование файла PNG
            png_file = demo_catalog.storage_root / row_dict["rel_folder_path"] / row_dict["png_filename"]
            assert png_file.exists(), f"Файл не найден: {png_file}"

            # Проверяем существование файла EPS
            eps_file = demo_catalog.storage_root / row_dict["rel_folder_path"] / row_dict["eps_filename"]
            assert eps_file.exists(), f"Файл не найден: {eps_file}"
    finally:
        conn.close()


def test_db_read_only_protection(demo_catalog: DemoCatalogContext) -> None:
    """Проверка, что open_db_readonly блокирует запись в базу."""
    conn = open_db_readonly(demo_catalog.db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO harvester_state(key, value, updated_at) VALUES ('test', '1', 0)")
    finally:
        conn.close()


def test_inspect_catalog_function(demo_catalog: DemoCatalogContext) -> None:
    """Проверка сбора метрик скриптом inspect_catalog."""
    conn = open_db_readonly(demo_catalog.db_path)
    try:
        data = inspect_catalog(conn, demo_catalog.storage_root, sample_alpha_size=50)
    finally:
        conn.close()

    assert data["total_assets"] == 40
    assert data["by_type"]["single_sticker"] == 40
    assert data["existing_png_count"] == 40
    assert data["existing_eps_count"] == 40
    assert data["alpha_checked"] > 0
    assert data["alpha_share"] == 1.0
    assert len(data["top_topics"]) == 5
    assert data["width_stats"]["min"] > 0
    assert data["width_stats"]["max"] >= data["width_stats"]["min"]


def test_png_transparency(demo_catalog: DemoCatalogContext) -> None:
    """Проверка, что сгенерированные PNG содержат честную прозрачность (альфа < 255)."""
    conn = open_db_readonly(demo_catalog.db_path)
    try:
        rows = conn.execute("SELECT rel_folder_path, png_filename FROM assets LIMIT 10").fetchall()
    finally:
        conn.close()

    for r in rows:
        p = demo_catalog.storage_root / r[0] / r[1]
        with Image.open(p) as img:
            assert img.mode == "RGBA"
            alpha = img.getchannel("A")
            min_a, max_a = alpha.getextrema()
            assert min_a == 0, "Фон стикера должен быть прозрачным (alpha=0)"
