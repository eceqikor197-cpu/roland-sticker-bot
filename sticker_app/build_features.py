"""
Команда построения векторов признаков макетов каталога.
python -m sticker_app.build_features [--dry-run] [--force] [--limit N]

Читает доступные макеты (usable_in_service = 1) из app.asset_meta и harvester.db,
строит словарь признаков FeatureVocabulary и сохраняет L2-нормированные векторы
в таблицу app.asset_features.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sticker_app.config as config
from sticker_app.db import get_app_db, init_db
from sticker_app.features import (
    FeatureExtractor,
    FeatureVocabulary,
    vector_to_blob,
)

logger = logging.getLogger("sticker_app.build_features")


def build_features(
    app_db_path: Optional[Path] = None,
    harvester_db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Вычисляет векторы признаков Уровня A для всех доступных макетов и сохраняет их в app.db.
    """
    a_path = (app_db_path or config.APP_DB_PATH).resolve()
    h_path = (harvester_db_path or config.HARVESTER_DB_PATH).resolve()

    if not h_path.exists():
        raise FileNotFoundError(f"harvester.db не найден: {h_path}")

    # Применяем миграции для создания таблиц asset_features и feature_vocabularies
    init_db(a_path)

    # Открываем harvester.db (mode=ro) и монтируем app.db
    h_uri = f"file:{h_path.as_posix()}?mode=ro"
    h_conn = sqlite3.connect(h_uri, uri=True, timeout=15.0)
    h_conn.row_factory = sqlite3.Row

    try:
        h_conn.execute(f"ATTACH DATABASE '{a_path.as_posix()}' AS app;")

        query = """
            SELECT
                a.asset_id,
                a.category_lvl1,
                a.category_lvl2,
                a.folder_slug,
                a.topic_name,
                a.title,
                m.tags_json,
                m.palette_json,
                m.aspect,
                m.alpha_coverage
            FROM assets a
            JOIN app.asset_meta m ON a.asset_id = m.asset_id
            WHERE m.usable_in_service = 1 AND m.takedown = 0
            ORDER BY a.asset_id ASC
        """
        if limit and limit > 0:
            query += f" LIMIT {int(limit)}"

        rows = h_conn.execute(query).fetchall()
    finally:
        h_conn.close()

    assets_data: List[Dict[str, Any]] = []
    for r in rows:
        tags = []
        if r["tags_json"]:
            try:
                tags = json.loads(r["tags_json"])
            except Exception:
                tags = []

        palette = []
        if r["palette_json"]:
            try:
                palette = json.loads(r["palette_json"])
            except Exception:
                palette = []

        assets_data.append(
            {
                "asset_id": str(r["asset_id"]),
                "category_lvl1": str(r["category_lvl1"] or ""),
                "category_lvl2": str(r["category_lvl2"] or ""),
                "folder_slug": str(r["folder_slug"] or ""),
                "topic_name": str(r["topic_name"] or ""),
                "title": str(r["title"] or ""),
                "tags": tags,
                "palette": palette,
                "aspect": float(r["aspect"] or 1.0),
                "alpha_coverage": float(r["alpha_coverage"] or 0.0),
            }
        )

    t0 = time.time()
    extractor = FeatureExtractor()

    if not assets_data:
        logger.warning("Нет доступных макетов с usable_in_service = 1 для построения признаков.")
        return {
            "processed": 0,
            "dimension": 0,
            "dry_run": dry_run,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    vocab: FeatureVocabulary = extractor.fit(assets_data)
    logger.info(
        "Словарь признаков построен: %d тегов, %d категорий, %d тем. Общая размерность: %d",
        len(vocab.tags),
        len(vocab.categories),
        len(vocab.topics),
        vocab.total_dim,
    )

    records: List[tuple[str, bytes, float]] = []
    now = time.time()

    for item in assets_data:
        vec = extractor.transform_one(item)
        blob = vector_to_blob(vec)
        records.append((item["asset_id"], blob, now))

    if not dry_run:
        app_conn = get_app_db(a_path)
        try:
            with app_conn:
                # Сохраняем словарь модели
                app_conn.execute(
                    """
                    INSERT OR REPLACE INTO feature_vocabularies (model_version, vocabulary_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (vocab.version, vocab.to_json(), now),
                )

                # Сохраняем векторы
                app_conn.executemany(
                    """
                    INSERT OR REPLACE INTO asset_features (asset_id, vector, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    records,
                )
        finally:
            app_conn.close()

    elapsed = round(time.time() - t0, 3)
    stats = {
        "processed": len(records),
        "dimension": vocab.total_dim,
        "tags_count": len(vocab.tags),
        "categories_count": len(vocab.categories),
        "topics_count": len(vocab.topics),
        "dry_run": dry_run,
        "elapsed_sec": elapsed,
    }

    if verbose:
        print(f"Построение признаков завершено за {elapsed}с:")
        print(f"  Макетов обработано: {len(records)}")
        print(f"  Размерность вектора: {vocab.total_dim}")
        print(f"  Тегов в словаре: {len(vocab.tags)}")
        print(f"  Тем: {len(vocab.topics)}, Категорий: {len(vocab.categories)}")

    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Построение векторов признаков каталога (S3)")
    parser.add_argument("--dry-run", action="store_true", help="Не записывать векторы в базу")
    parser.add_argument("--force", action="store_true", help="Пересчитать все векторы заново")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число макетов")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод в консоль")
    args = parser.parse_args()

    stats = build_features(
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
        verbose=args.verbose or True,
    )
    print(f"Результат: {stats}")


if __name__ == "__main__":
    main()
