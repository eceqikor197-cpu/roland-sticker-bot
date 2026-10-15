"""
Тесты этапа S3: построение признаков Уровня A, веса, сериализация и CLI build_features.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sticker_app.build_features import build_features
from sticker_app.db import get_app_db, run_migrations
from sticker_app.features import (
    COLOR_TOTAL_BINS,
    GEOMETRY_DIM,
    FeatureExtractor,
    FeatureVocabulary,
    blob_to_vector,
    extract_geometry_features,
    get_embedding_provider,
    hex_to_hsv,
    palette_to_color_hist,
    vector_to_blob,
)
from sticker_app.sync_catalog import sync_catalog
from tests.app.conftest import DemoCatalogContext


def test_migration_002_schema(tmp_path: Path) -> None:
    """Проверка создания таблиц asset_features и feature_vocabularies при миграциях."""
    app_db = tmp_path / "app.db"
    applied = run_migrations(db_path=app_db)
    assert "001_initial.sql" in applied
    assert "002_features.sql" in applied

    conn = get_app_db(app_db)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "asset_features" in tables
    assert "feature_vocabularies" in tables
    conn.close()


def test_hex_to_hsv_and_palette_hist() -> None:
    """Проверка конвертации HEX в HSV и корзин гистограммы."""
    # Чистый красный: H=0.0, S=1.0, V=1.0
    hsv_red = hex_to_hsv("#FF0000")
    assert hsv_red is not None
    assert abs(hsv_red[0] - 0.0) < 1e-4
    assert abs(hsv_red[1] - 1.0) < 1e-4

    # Чистый черный (V=0)
    hsv_black = hex_to_hsv("#000000")
    assert hsv_black is not None
    assert hsv_black[2] == 0.0

    # Проверка гистограммы
    palette = ["#FF0000", "#00FF00", "#0000FF", "#000000", "#FFFFFF"]
    hist = palette_to_color_hist(palette)
    assert len(hist) == COLOR_TOTAL_BINS
    assert abs(np.linalg.norm(hist) - 1.0) < 1e-4

    # Пустая палитра не падает
    empty_hist = palette_to_color_hist([])
    assert len(empty_hist) == COLOR_TOTAL_BINS
    assert np.all(empty_hist == 0.0)


def test_geometry_features() -> None:
    """Проверка извлечения и нормализации геометрических признаков."""
    geom = extract_geometry_features(aspect=1.5, alpha_coverage=0.75)
    assert len(geom) == GEOMETRY_DIM
    assert abs(np.linalg.norm(geom) - 1.0) < 1e-4

    # Граничные значения
    geom_clamped = extract_geometry_features(aspect=100.0, alpha_coverage=-1.0)
    assert geom_clamped[0] == pytest.approx(1.0, rel=1e-3)  # aspect clamped to 5.0 -> 5.0/5.0 = 1.0
    assert geom_clamped[1] == 0.0  # alpha clamped to 0.0


def test_feature_extractor_properties() -> None:
    """Проверка свойств экстрактора: размерности, единичная норма, детерминизм."""
    sample_assets = [
        {
            "asset_id": "a1",
            "category_lvl1": "03_Одиночные_Стикеры",
            "category_lvl2": "Ретро",
            "folder_slug": "retro_wave",
            "topic_name": "Ретро Вейв",
            "tags": ["неон", "закат", "синтвейв", "80s"],
            "palette": ["#FFBE0B", "#FF007F"],
            "aspect": 1.0,
            "alpha_coverage": 0.6,
        },
        {
            "asset_id": "a2",
            "category_lvl1": "03_Одиночные_Стикеры",
            "category_lvl2": "Кибер",
            "folder_slug": "cyberpunk",
            "topic_name": "Киберпанк Неон",
            "tags": ["неон", "кибер", "глитч", "sci-fi"],
            "palette": ["#00FFFF", "#FF0055"],
            "aspect": 1.2,
            "alpha_coverage": 0.8,
        },
        {
            "asset_id": "a3",
            "category_lvl1": "03_Одиночные_Стикеры",
            "category_lvl2": "Животные",
            "folder_slug": "cats",
            "topic_name": "Котики",
            "tags": ["кот", "лапка", "милый", "животные"],
            "palette": ["#FFFFFF", "#888888"],
            "aspect": 0.9,
            "alpha_coverage": 0.5,
        },
    ]

    extractor = FeatureExtractor()
    vocab = extractor.fit(sample_assets)

    assert vocab.total_dim > 0
    assert vocab.dim_color == COLOR_TOTAL_BINS
    assert vocab.dim_geom == GEOMETRY_DIM
    assert len(vocab.tags) > 0

    # Сериализация словаря
    vocab_json = vocab.to_json()
    loaded_vocab = FeatureVocabulary.from_json(vocab_json)
    assert loaded_vocab.total_dim == vocab.total_dim
    assert loaded_vocab.tags == vocab.tags

    # Проверка L2-нормы
    v1 = extractor.transform_one(sample_assets[0])
    v2 = extractor.transform_one(sample_assets[1])
    v3 = extractor.transform_one(sample_assets[2])

    assert v1.dtype == np.float32
    assert abs(np.linalg.norm(v1) - 1.0) < 1e-4
    assert abs(np.linalg.norm(v2) - 1.0) < 1e-4
    assert abs(np.linalg.norm(v3) - 1.0) < 1e-4

    # Детерминированность: повторное преобразование идентично
    v1_again = extractor.transform_one(sample_assets[0])
    np.testing.assert_array_equal(v1, v1_again)

    # Сериализация векторов в blob
    blob = vector_to_blob(v1)
    restored = blob_to_vector(blob)
    np.testing.assert_array_almost_equal(v1, restored)

    # Интуиция сходства:
    # Ретро Вейв и Киберпанк оба имеют тег 'неон' и яркие неоновые цвета
    # Их сходство должно быть выше, чем у Ретро Вейв и Котиков
    sim_retro_cyber = float(np.dot(v1, v2))
    sim_retro_cats = float(np.dot(v1, v3))
    assert sim_retro_cyber > sim_retro_cats


def test_build_features_on_demo_catalog(demo_catalog: DemoCatalogContext) -> None:
    """Проверка работы CLI build_features на синтетическом каталоге."""
    # 1. Синхронизируем метаданные в app.db
    sync_stats = sync_catalog(
        harvester_db_path=demo_catalog.db_path,
        storage_root=demo_catalog.storage_root,
        dry_run=False,
    )
    assert sync_stats["processed"] > 0

    # 2. Строим векторы признаков
    feat_stats = build_features(
        harvester_db_path=demo_catalog.db_path,
        dry_run=False,
        verbose=False,
    )
    assert feat_stats["processed"] == sync_stats["processed"]
    assert feat_stats["dimension"] > 0
    assert feat_stats["tags_count"] > 0

    # 3. Проверяем наличие записей в app.db
    conn = get_app_db()
    rows = conn.execute("SELECT asset_id, vector FROM asset_features").fetchall()
    assert len(rows) == sync_stats["processed"]

    # Проверяем, что вектор валидный и имеет единичную норму
    first_vec = blob_to_vector(rows[0]["vector"])
    assert len(first_vec) == feat_stats["dimension"]
    assert abs(np.linalg.norm(first_vec) - 1.0) < 1e-3

    # Проверяем словарь
    vocab_row = conn.execute("SELECT vocabulary_json FROM feature_vocabularies WHERE model_version='v1'").fetchone()
    assert vocab_row is not None
    loaded = FeatureVocabulary.from_json(vocab_row["vocabulary_json"])
    assert loaded.total_dim == feat_stats["dimension"]
    conn.close()


def test_embedding_provider_level_b_fallback() -> None:
    """Проверка отката провайдера при запросе CLIP без установленных библиотек."""
    extractor = FeatureExtractor()
    provider = get_embedding_provider(extractor, provider_name="clip")
    # Должен возвратить LevelAEmbeddingProvider без падений
    assert provider is not None
    assert provider.get_dimension() == 0
