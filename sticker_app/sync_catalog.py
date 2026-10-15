"""
Команда синхронизации и обогащения каталога макетов.
python -m sticker_app.sync_catalog [--dry-run] [--limit N]

Читает макеты из harvester.db (mode=ro), извлекает:
- нормализованные теги (RU/EN, без стоп-слов, макс 12)
- геометрические пропорции (aspect ratio, is_square_ok)
- альфа-покрытие (alpha_coverage на миниатюре 256px)
- палитру (5 доминирующих HEX-цветов)
- статус usable_in_service с учетом лицензии и takedown

Сохраняет данные в app.db (asset_meta), не перезаписывая ручные лицензии!
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sticker_app.config as config
from sticker_app.catalog_reader import PathTraversalError, resolve_asset_path
from sticker_app.db import init_db
from sticker_app.raster_meta import analyze_raster
from sticker_app.tag_extractor import extract_tags

logger = logging.getLogger("sticker_app.sync_catalog")

ALLOWED_LICENSES = {"own", "licensed", "cc0"}
VALID_LICENSES = {"own", "licensed", "cc0", "unknown"}


def sync_catalog(
    harvester_db_path: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Синхронизирует и обогащает макеты из harvester.db в app.db.
    Операция полностью идемпотентна.
    """
    h_path = (harvester_db_path or config.HARVESTER_DB_PATH).resolve()
    a_path = (app_db_path or config.APP_DB_PATH).resolve()
    storage = (storage_root or config.STORAGE_ROOT).resolve()

    if not h_path.exists():
        raise FileNotFoundError(f"harvester.db не найден: {h_path}")

    # Инициализируем app.db и накатываем миграции
    init_db(a_path)

    # Загружаем существующие записи из asset_meta (для сохранения ручных лицензий и статуса takedown)
    existing_meta: Dict[str, Tuple[str, int]] = {}
    app_conn = sqlite3.connect(a_path.as_posix(), timeout=15.0)
    app_conn.row_factory = sqlite3.Row
    try:
        rows = app_conn.execute("SELECT asset_id, license, takedown FROM asset_meta").fetchall()
        for r in rows:
            existing_meta[str(r["asset_id"])] = (str(r["license"]), int(r["takedown"]))
    finally:
        if dry_run:
            app_conn.close()

    # Открываем harvester.db строго в mode=ro
    h_uri = f"file:{h_path.as_posix()}?mode=ro"
    h_conn = sqlite3.connect(h_uri, uri=True, timeout=15.0)
    h_conn.row_factory = sqlite3.Row

    samples_list: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "processed": 0,
        "usable": 0,
        "takedown": 0,
        "samples": samples_list,
        "dry_run": dry_run,
    }

    try:
        query = """
            SELECT
                a.asset_id,
                a.category_lvl1,
                a.category_lvl2,
                a.folder_slug,
                a.topic_name,
                a.title,
                a.license AS harvester_license,
                a.width_px,
                a.height_px,
                a.rel_folder_path,
                a.png_filename,
                t.search_queries_json
            FROM assets a
            LEFT JOIN topics t ON a.topic_id = t.id
            ORDER BY a.created_at DESC
        """
        if limit and limit > 0:
            query += f" LIMIT {int(limit)}"

        assets = h_conn.execute(query).fetchall()
        now = time.time()

        records_to_upsert: List[Tuple[Any, ...]] = []

        for row in assets:
            asset_id = str(row["asset_id"])

            # 1. Лицензия и takedown:
            # Если лицензия уже была задана в asset_meta, сохраняем её!
            if asset_id in existing_meta:
                license_val, takedown_val = existing_meta[asset_id]
            else:
                raw_lic = str(row["harvester_license"] or "unknown").strip().lower()
                license_val = raw_lic if raw_lic in VALID_LICENSES else "unknown"
                takedown_val = 0

            # 2. usable_in_service:
            # 1, если takedown = 0 И (LICENSE_FILTER == False ИЛИ license IN ('own', 'licensed', 'cc0')).
            # При takedown = 1 всегда 0.
            if takedown_val == 1:
                usable_in_service = 0
            elif not config.LICENSE_FILTER:
                usable_in_service = 1
            elif license_val in ALLOWED_LICENSES:
                usable_in_service = 1
            else:
                usable_in_service = 0

            # 3. Теги:
            tags = extract_tags(
                category_lvl1=str(row["category_lvl1"] or ""),
                category_lvl2=str(row["category_lvl2"] or ""),
                topic_name=str(row["topic_name"] or ""),
                folder_slug=str(row["folder_slug"] or ""),
                title=str(row["title"] or ""),
                search_queries=row["search_queries_json"],
                max_tags=12,
            )
            tags_json = json.dumps(tags, ensure_ascii=False)

            # 4. Растровая геометрия и палитра:
            rel_folder = str(row["rel_folder_path"] or "")
            png_name = str(row["png_filename"] or "")
            w_px = int(row["width_px"]) if row["width_px"] else 0
            h_px = int(row["height_px"]) if row["height_px"] else 0

            resolved_png: Optional[Path] = None
            try:
                resolved_png = resolve_asset_path(storage, rel_folder, png_name)
            except PathTraversalError:
                resolved_png = None

            raster_data = analyze_raster(
                png_path=resolved_png,
                width_px=w_px,
                height_px=h_px,
            )
            palette_json = json.dumps(raster_data.palette, ensure_ascii=False)

            record = (
                asset_id,
                license_val,
                usable_in_service,
                takedown_val,
                tags_json,
                palette_json,
                raster_data.aspect,
                raster_data.alpha_coverage,
                raster_data.is_square_ok,
                now,
            )
            records_to_upsert.append(record)

            stats["processed"] += 1
            if usable_in_service == 1:
                stats["usable"] += 1
            if takedown_val == 1:
                stats["takedown"] += 1

            if len(stats["samples"]) < 10:
                stats["samples"].append(
                    {
                        "asset_id": asset_id,
                        "title": str(row["title"] or ""),
                        "topic_name": str(row["topic_name"] or ""),
                        "license": license_val,
                        "usable": usable_in_service,
                        "tags": tags,
                        "palette": raster_data.palette,
                        "aspect": raster_data.aspect,
                        "alpha_coverage": raster_data.alpha_coverage,
                    }
                )

        if not dry_run:
            upsert_query = """
                INSERT INTO asset_meta (
                    asset_id, license, usable_in_service, takedown,
                    tags_json, palette_json, aspect, alpha_coverage,
                    is_square_ok, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    usable_in_service = excluded.usable_in_service,
                    tags_json = excluded.tags_json,
                    palette_json = excluded.palette_json,
                    aspect = excluded.aspect,
                    alpha_coverage = excluded.alpha_coverage,
                    is_square_ok = excluded.is_square_ok,
                    updated_at = excluded.updated_at;
            """
            with app_conn:
                app_conn.executemany(upsert_query, records_to_upsert)

        return stats
    finally:
        h_conn.close()
        if not dry_run:
            app_conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Синхронизация и обогащение метаданных каталога (app.db)")
    parser.add_argument("--dry-run", action="store_true", help="Не записывать изменения в app.db")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить количество обрабатываемых макетов")
    parser.add_argument("--harvester-db", type=Path, default=None, help="Путь к harvester.db")
    parser.add_argument("--app-db", type=Path, default=None, help="Путь к app.db")
    parser.add_argument("--storage-root", type=Path, default=None, help="Путь к корневому хранилищу")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")

    args = parser.parse_args()

    print("=== Синхронизация каталога макетов в app.db ===")
    stats = sync_catalog(
        harvester_db_path=args.harvester_db,
        app_db_path=args.app_db,
        storage_root=args.storage_root,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print(f"Обработано макетов: {stats['processed']}")
    print(f"Доступно в сервисе (usable): {stats['usable']}")
    print(f"В takedown: {stats['takedown']}")
    if stats["dry_run"]:
        print("[DRY-RUN] Изменения НЕ были записаны в базу.")
    else:
        print("Данные успешно сохранены в app.db.")

    print("\n--- Примеры обогащенных макетов (10 образцов) ---")
    for i, s in enumerate(stats["samples"], start=1):
        print(f"\n[{i}] ID: {s['asset_id']} | Тема: {s['topic_name']} | {s['title']}")
        print(f"    Лицензия: {s['license']} | Доступен: {s['usable']} | Aspect: {s['aspect']} | Alpha: {s['alpha_coverage']}")
        print(f"    Теги: {', '.join(s['tags'])}")
        print(f"    Палитра: {', '.join(s['palette'])}")


if __name__ == "__main__":
    main()
