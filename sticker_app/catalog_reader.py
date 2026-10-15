"""
Модуль чтения каталога harvester.db (только для чтения).
Интегрирован с метаданными из app.db (asset_meta).
Строго соблюдает правила безопасности:
- Режим read-only (sqlite3 URI mode=ro).
- Защита от выхода за пределы хранилища (Path Traversal Protection).
- Отсутствие внутренних путей файловой системы в публичных ответах.
- Публичные эндпоинты отдают только макеты с usable_in_service = 1 и takedown = 0.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sticker_app.config as config
from sticker_app.db import get_app_db
from sticker_app.models import (
    CategoryLevel2,
    CategoryTree,
    PaginationMeta,
    StickerDetail,
    StickerListResponse,
    StickerSummary,
    TagInfo,
    TopicInfo,
)

logger = logging.getLogger("sticker_app.catalog_reader")

ALLOWED_LICENSES = {"own", "licensed", "cc0"}


class PathTraversalError(ValueError):
    """Попытка выхода за пределы корневого каталога хранилища."""
    pass


def resolve_asset_path(storage_root: Path, rel_folder_path: str, filename: str) -> Path:
    """
    Безопасное построение абсолютного пути к файлу макета.
    Гарантирует, что итоговый путь строго находится внутри storage_root.
    """
    clean_folder = Path(rel_folder_path.lstrip("/\\"))
    target = (storage_root / clean_folder / filename).resolve()
    resolved_root = storage_root.resolve()

    if not target.is_relative_to(resolved_root):
        raise PathTraversalError(f"Path traversal detected: {rel_folder_path} / {filename}")
    return target


def get_db_connection(
    db_path: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> sqlite3.Connection:
    """
    Открывает соединение с harvester.db строго в режиме read-only.
    При наличии app.db монтирует её через ATTACH DATABASE.
    """
    path = (db_path or config.HARVESTER_DB_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Файл базы harvester.db не найден: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row

    # Подключаем app.db если файл существует
    a_path = (app_db_path or config.APP_DB_PATH).resolve()
    if a_path.exists():
        try:
            conn.execute(f"ATTACH DATABASE '{a_path.as_posix()}' AS app;")
        except Exception as exc:
            logger.warning("Не удалось выполнить ATTACH DATABASE app.db: %s", exc)

    return conn


def _is_app_meta_available(conn: sqlite3.Connection) -> bool:
    """Проверяет, смонтирована ли app.db и создана ли таблица asset_meta."""
    try:
        databases = [r["name"] for r in conn.execute("PRAGMA database_list").fetchall()]
        if "app" not in databases:
            return False
        row = conn.execute(
            "SELECT 1 FROM app.sqlite_master WHERE type='table' AND name='asset_meta'"
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _row_to_summary(row: sqlite3.Row, api_prefix: str = config.API_PREFIX) -> StickerSummary:
    """Преобразует строку assets + asset_meta в публичный StickerSummary."""
    asset_id = str(row["asset_id"])
    w_px = int(row["width_px"]) if row["width_px"] else 0
    h_px = int(row["height_px"]) if row["height_px"] else 0

    if "aspect" in row.keys() and row["aspect"] is not None:
        aspect = round(float(row["aspect"]), 4)
    elif h_px > 0:
        aspect = round(w_px / h_px, 4)
    else:
        aspect = 1.0

    license_val = str(row["license"]) if row["license"] else "unknown"

    if "usable_in_service" in row.keys() and row["usable_in_service"] is not None:
        usable = bool(row["usable_in_service"])
    else:
        usable = license_val in ALLOWED_LICENSES or not config.LICENSE_FILTER

    tags: List[str] = []
    if "tags_json" in row.keys() and row["tags_json"]:
        try:
            tags = json.loads(row["tags_json"])
        except (json.JSONDecodeError, TypeError):
            tags = []

    palette: List[str] = []
    if "palette_json" in row.keys() and row["palette_json"]:
        try:
            palette = json.loads(row["palette_json"])
        except (json.JSONDecodeError, TypeError):
            palette = []

    alpha_cov = (
        float(row["alpha_coverage"])
        if ("alpha_coverage" in row.keys() and row["alpha_coverage"] is not None)
        else 0.0
    )

    return StickerSummary(
        asset_id=asset_id,
        title=str(row["title"] or ""),
        topic_name=str(row["topic_name"] or ""),
        folder_slug=str(row["folder_slug"] or ""),
        category_lvl1=str(row["category_lvl1"] or ""),
        category_lvl2=str(row["category_lvl2"] or ""),
        asset_type=str(row["asset_type"] or "single_sticker"),
        width_mm=float(row["width_mm"]) if row["width_mm"] is not None else 100.0,
        height_mm=float(row["height_mm"]) if row["height_mm"] is not None else 100.0,
        aspect=aspect,
        aspect_ratio=aspect,
        alpha_coverage=alpha_cov,
        tags=tags,
        palette=palette,
        license=license_val,
        usable_in_service=usable,
        thumb_url=f"{api_prefix}/stickers/{asset_id}/image?size=thumb",
        preview_url=f"{api_prefix}/stickers/{asset_id}/image?size=preview",
    )


def _row_to_detail(row: sqlite3.Row, api_prefix: str = config.API_PREFIX) -> StickerDetail:
    """Преобразует строку assets в детальный StickerDetail."""
    summary = _row_to_summary(row, api_prefix)
    asset_id = summary.asset_id

    return StickerDetail(
        **summary.model_dump(),
        width_px=int(row["width_px"]) if row["width_px"] else 0,
        height_px=int(row["height_px"]) if row["height_px"] else 0,
        offset_mm=float(row["offset_mm"]) if row["offset_mm"] is not None else 2.5,
        perf_cut_mode=str(row["perf_cut_mode"] or "none"),
        num_contours=int(row["num_contours"]) if row["num_contours"] else 1,
        source_name=str(row["source_name"] or ""),
        created_at=float(row["created_at"]) if row["created_at"] else 0.0,
        parent_asset_id=str(row["parent_asset_id"] or ""),
        full_image_url=f"{api_prefix}/stickers/{asset_id}/image?size=full",
    )


def list_stickers(
    filters: Optional[Dict[str, Any]] = None,
    page: int = 1,
    page_size: int = 30,
    db_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> StickerListResponse:
    """
    Получение списка стикеров с фильтрацией, пагинацией и проверкой наличия файлов.
    Публичные запросы возвращают исключительно макеты с usable_in_service = 1.
    """
    filters = filters or {}
    storage = (storage_root or config.STORAGE_ROOT).resolve()
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    where_clauses: List[str] = []
    params: List[Any] = []

    # 1. Фильтр типа макета (по умолчанию single_sticker)
    asset_type = filters.get("asset_type", "single_sticker")
    if asset_type:
        where_clauses.append("a.asset_type = ?")
        params.append(asset_type)

    # 2. Фильтры категорий и тем
    if filters.get("category_lvl1"):
        where_clauses.append("a.category_lvl1 = ?")
        params.append(filters["category_lvl1"])

    if filters.get("category_lvl2"):
        where_clauses.append("a.category_lvl2 = ?")
        params.append(filters["category_lvl2"])

    if filters.get("folder_slug"):
        where_clauses.append("a.folder_slug = ?")
        params.append(filters["folder_slug"])

    if filters.get("topic_name"):
        where_clauses.append("a.topic_name = ?")
        params.append(filters["topic_name"])

    # 3. Поиск по тексту в названии, теме или тегах
    search = filters.get("search")
    if search:
        search_pattern = f"%{search.strip()}%"
        where_clauses.append("(a.title LIKE ? OR a.topic_name LIKE ?)")
        params.extend([search_pattern, search_pattern])

    # 4. Лицензионный фильтр
    use_license_filter = filters.get("license_filter", config.LICENSE_FILTER)

    conn = get_db_connection(db_path, app_db_path=app_db_path)
    try:
        has_meta = _is_app_meta_available(conn)

        # 5. Фильтр usable_in_service = 1 и takedown = 0
        if has_meta:
            where_clauses.append("COALESCE(m.takedown, 0) = 0")
            where_clauses.append(
                "COALESCE(m.usable_in_service, CASE WHEN (COALESCE(m.license, a.license) IN ('own','licensed','cc0') OR ?) THEN 1 ELSE 0 END) = 1"
            )
            params.append(0 if use_license_filter else 1)

            if use_license_filter:
                where_clauses.append("COALESCE(m.license, a.license) IN ('own', 'licensed', 'cc0')")

            join_clause = "LEFT JOIN app.asset_meta m ON a.asset_id = m.asset_id"
            select_fields = """
                a.asset_id, a.topic_id, a.category_lvl1, a.category_lvl2, a.folder_slug,
                a.topic_name, a.asset_type, a.title, a.source_name, a.source_url,
                COALESCE(m.license, a.license) AS license,
                a.phash_hex, a.width_px, a.height_px, a.width_mm, a.height_mm,
                a.offset_mm, a.perf_cut_mode, a.num_contours, a.rel_folder_path,
                a.eps_filename, a.png_filename, a.parent_asset_id, a.created_at,
                COALESCE(m.usable_in_service, 1) AS usable_in_service,
                COALESCE(m.tags_json, '[]') AS tags_json,
                COALESCE(m.palette_json, '[]') AS palette_json,
                COALESCE(m.aspect, CASE WHEN a.height_px > 0 THEN ROUND(1.0 * a.width_px / a.height_px, 4) ELSE 1.0 END) AS aspect,
                COALESCE(m.alpha_coverage, 0.0) AS alpha_coverage
            """
        else:
            if use_license_filter:
                where_clauses.append("a.license IN ('own', 'licensed', 'cc0')")
            join_clause = ""
            select_fields = """
                a.asset_id, a.topic_id, a.category_lvl1, a.category_lvl2, a.folder_slug,
                a.topic_name, a.asset_type, a.title, a.source_name, a.source_url,
                a.license, a.phash_hex, a.width_px, a.height_px, a.width_mm, a.height_mm,
                a.offset_mm, a.perf_cut_mode, a.num_contours, a.rel_folder_path,
                a.eps_filename, a.png_filename, a.parent_asset_id, a.created_at,
                1 AS usable_in_service,
                '[]' AS tags_json,
                '[]' AS palette_json,
                CASE WHEN a.height_px > 0 THEN ROUND(1.0 * a.width_px / a.height_px, 4) ELSE 1.0 END AS aspect,
                0.0 AS alpha_coverage
            """

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Подсчёт общего количества
        count_query = f"SELECT COUNT(*) FROM assets a {join_clause} {where_sql}"
        cursor = conn.execute(count_query, params)
        total_items = cursor.fetchone()[0]

        total_pages = math.ceil(total_items / page_size) if total_items > 0 else 0
        offset = (page - 1) * page_size

        # Запрос выборки
        select_query = f"""
            SELECT {select_fields}
            FROM assets a
            {join_clause}
            {where_sql}
            ORDER BY a.created_at DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(select_query, [*params, page_size, offset]).fetchall()

        items: List[StickerSummary] = []
        for r in rows:
            # Проверяем безопасность путей и наличие файла на диске
            rel_folder = str(r["rel_folder_path"] or "")
            png_name = str(r["png_filename"] or "")
            try:
                resolved_png = resolve_asset_path(storage, rel_folder, png_name)
                if not resolved_png.exists():
                    logger.warning("Макет %s отсутствует на диске: %s", r["asset_id"], resolved_png)
                    continue
            except PathTraversalError as pte:
                logger.error("Path traversal detected in asset %s: %s", r["asset_id"], pte)
                continue

            items.append(_row_to_summary(r))

        meta = PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        )

        return StickerListResponse(items=items, meta=meta)
    finally:
        conn.close()


def get_sticker(
    asset_id: str,
    db_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> Optional[StickerDetail]:
    """
    Получение детальной информации о стикере по его ID.
    Возвращает None, если стикер не найден, файл отсутствует, либо макет недоступен (takedown/license).
    """
    storage = (storage_root or config.STORAGE_ROOT).resolve()
    conn = get_db_connection(db_path, app_db_path=app_db_path)
    try:
        has_meta = _is_app_meta_available(conn)
        if has_meta:
            query = """
                SELECT
                    a.asset_id, a.topic_id, a.category_lvl1, a.category_lvl2, a.folder_slug,
                    a.topic_name, a.asset_type, a.title, a.source_name, a.source_url,
                    COALESCE(m.license, a.license) AS license,
                    a.phash_hex, a.width_px, a.height_px, a.width_mm, a.height_mm,
                    a.offset_mm, a.perf_cut_mode, a.num_contours, a.rel_folder_path,
                    a.eps_filename, a.png_filename, a.parent_asset_id, a.created_at,
                    COALESCE(m.usable_in_service, 1) AS usable_in_service,
                    COALESCE(m.takedown, 0) AS takedown,
                    COALESCE(m.tags_json, '[]') AS tags_json,
                    COALESCE(m.palette_json, '[]') AS palette_json,
                    COALESCE(m.aspect, CASE WHEN a.height_px > 0 THEN ROUND(1.0 * a.width_px / a.height_px, 4) ELSE 1.0 END) AS aspect,
                    COALESCE(m.alpha_coverage, 0.0) AS alpha_coverage
                FROM assets a
                LEFT JOIN app.asset_meta m ON a.asset_id = m.asset_id
                WHERE a.asset_id = ?
            """
        else:
            query = """
                SELECT
                    a.asset_id, a.topic_id, a.category_lvl1, a.category_lvl2, a.folder_slug,
                    a.topic_name, a.asset_type, a.title, a.source_name, a.source_url,
                    a.license, a.phash_hex, a.width_px, a.height_px, a.width_mm, a.height_mm,
                    a.offset_mm, a.perf_cut_mode, a.num_contours, a.rel_folder_path,
                    a.eps_filename, a.png_filename, a.parent_asset_id, a.created_at,
                    1 AS usable_in_service,
                    0 AS takedown,
                    '[]' AS tags_json,
                    '[]' AS palette_json,
                    CASE WHEN a.height_px > 0 THEN ROUND(1.0 * a.width_px / a.height_px, 4) ELSE 1.0 END AS aspect,
                    0.0 AS alpha_coverage
                FROM assets a
                WHERE a.asset_id = ?
            """

        row = conn.execute(query, (asset_id,)).fetchone()
        if not row:
            return None

        # Проверка takedown
        if row["takedown"] == 1:
            return None

        # Проверка лицензионного фильтра
        license_val = str(row["license"] or "unknown")
        if config.LICENSE_FILTER and license_val not in ALLOWED_LICENSES:
            return None

        # Проверка usable_in_service
        if int(row["usable_in_service"]) != 1:
            return None

        # Проверка безопасности пути и наличия на диске
        rel_folder = str(row["rel_folder_path"] or "")
        png_name = str(row["png_filename"] or "")
        try:
            resolved_png = resolve_asset_path(storage, rel_folder, png_name)
            if not resolved_png.exists():
                logger.warning("Файл макета %s не найден на диске: %s", asset_id, resolved_png)
                return None
        except PathTraversalError as pte:
            logger.error("Path traversal error in get_sticker(%s): %s", asset_id, pte)
            return None

        return _row_to_detail(row)
    finally:
        conn.close()


def get_asset_file_path(
    asset_id: str,
    file_type: str = "png",
    db_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Возвращает безопасный абсолютный путь к файлу макета (png или eps).
    Возвращает (path, content_type) или (None, None).
    """
    storage = (storage_root or config.STORAGE_ROOT).resolve()
    conn = get_db_connection(db_path, app_db_path=app_db_path)
    try:
        has_meta = _is_app_meta_available(conn)
        if has_meta:
            query = """
                SELECT
                    a.rel_folder_path, a.png_filename, a.eps_filename,
                    COALESCE(m.license, a.license) AS license,
                    COALESCE(m.takedown, 0) AS takedown,
                    COALESCE(m.usable_in_service, 1) AS usable_in_service
                FROM assets a
                LEFT JOIN app.asset_meta m ON a.asset_id = m.asset_id
                WHERE a.asset_id = ?
            """
        else:
            query = """
                SELECT rel_folder_path, png_filename, eps_filename, license, 0 AS takedown, 1 AS usable_in_service
                FROM assets WHERE asset_id = ?
            """

        row = conn.execute(query, (asset_id,)).fetchone()
        if not row:
            return None, None

        if row["takedown"] == 1:
            return None, None

        license_val = str(row["license"] or "unknown")
        if config.LICENSE_FILTER and license_val not in ALLOWED_LICENSES:
            return None, None

        if int(row["usable_in_service"]) != 1:
            return None, None

        rel_folder = str(row["rel_folder_path"] or "")
        if file_type == "eps":
            filename = str(row["eps_filename"] or "")
            content_type = "application/postscript"
        else:
            filename = str(row["png_filename"] or "")
            content_type = "image/png"

        try:
            target_path = resolve_asset_path(storage, rel_folder, filename)
            if not target_path.exists():
                return None, None
            return target_path, content_type
        except PathTraversalError:
            return None, None
    finally:
        conn.close()


def list_categories(
    asset_type: str = "single_sticker",
    db_path: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> List[CategoryTree]:
    """
    Возвращает дерево категорий и подкатегорий с темами.
    """
    conn = get_db_connection(db_path, app_db_path=app_db_path)
    try:
        has_meta = _is_app_meta_available(conn)
        where_clauses: List[str] = []
        params: List[Any] = []

        if asset_type:
            where_clauses.append("a.asset_type = ?")
            params.append(asset_type)

        if has_meta:
            where_clauses.append("COALESCE(m.takedown, 0) = 0")
            where_clauses.append("COALESCE(m.usable_in_service, 1) = 1")
            if config.LICENSE_FILTER:
                where_clauses.append("COALESCE(m.license, a.license) IN ('own', 'licensed', 'cc0')")
            join_clause = "LEFT JOIN app.asset_meta m ON a.asset_id = m.asset_id"
        else:
            if config.LICENSE_FILTER:
                where_clauses.append("a.license IN ('own', 'licensed', 'cc0')")
            join_clause = ""

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT
                a.category_lvl1,
                a.category_lvl2,
                a.folder_slug,
                a.topic_name,
                a.topic_id,
                COUNT(*) as count
            FROM assets a
            {join_clause}
            {where_sql}
            GROUP BY a.category_lvl1, a.category_lvl2, a.folder_slug, a.topic_name, a.topic_id
            ORDER BY a.category_lvl1, a.category_lvl2, a.topic_name
        """
        rows = conn.execute(query, params).fetchall()

        tree_dict: Dict[str, Dict[str, List[TopicInfo]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            c1 = str(r["category_lvl1"])
            c2 = str(r["category_lvl2"])
            topic = TopicInfo(
                topic_id=int(r["topic_id"]),
                topic_name=str(r["topic_name"]),
                folder_slug=str(r["folder_slug"]),
                category_lvl1=c1,
                category_lvl2=c2,
                stickers_count=int(r["count"]),
            )
            tree_dict[c1][c2].append(topic)

        result: List[CategoryTree] = []
        for c1, sub_cats in tree_dict.items():
            lvl2_list: List[CategoryLevel2] = []
            for c2, topics in sub_cats.items():
                lvl2_list.append(CategoryLevel2(category_lvl2=c2, topics=topics))
            result.append(CategoryTree(category_lvl1=c1, subcategories=lvl2_list))

        return result
    finally:
        conn.close()


def list_topics(
    category_lvl1: Optional[str] = None,
    category_lvl2: Optional[str] = None,
    db_path: Optional[Path] = None,
    app_db_path: Optional[Path] = None,
) -> List[TopicInfo]:
    """
    Возвращает плоский список тем с фильтрацией по категории.
    """
    conn = get_db_connection(db_path, app_db_path=app_db_path)
    try:
        has_meta = _is_app_meta_available(conn)
        where_clauses = ["t.is_enabled = 1"]
        params: List[Any] = []

        if category_lvl1:
            where_clauses.append("t.category_lvl1 = ?")
            params.append(category_lvl1)
        if category_lvl2:
            where_clauses.append("t.category_lvl2 = ?")
            params.append(category_lvl2)

        where_sql = " AND ".join(where_clauses)

        if has_meta:
            extra_join = "LEFT JOIN app.asset_meta m ON a.asset_id = m.asset_id"
            extra_cond = "AND COALESCE(m.takedown, 0) = 0 AND COALESCE(m.usable_in_service, 1) = 1"
            if config.LICENSE_FILTER:
                extra_cond += " AND COALESCE(m.license, a.license) IN ('own', 'licensed', 'cc0')"
        else:
            extra_join = ""
            extra_cond = "AND a.license IN ('own', 'licensed', 'cc0')" if config.LICENSE_FILTER else ""

        query = f"""
            SELECT
                t.id as topic_id,
                t.topic_name,
                t.folder_slug,
                t.category_lvl1,
                t.category_lvl2,
                COUNT(a.asset_id) as stickers_count
            FROM topics t
            LEFT JOIN assets a ON a.topic_id = t.id AND a.asset_type = 'single_sticker' {extra_cond}
            {extra_join}
            WHERE {where_sql}
            GROUP BY t.id, t.topic_name, t.folder_slug, t.category_lvl1, t.category_lvl2
            ORDER BY t.category_lvl1, t.category_lvl2, t.topic_name
        """
        rows = conn.execute(query, params).fetchall()
        return [
            TopicInfo(
                topic_id=int(r["topic_id"]),
                topic_name=str(r["topic_name"]),
                folder_slug=str(r["folder_slug"]),
                category_lvl1=str(r["category_lvl1"]),
                category_lvl2=str(r["category_lvl2"]),
                stickers_count=int(r["stickers_count"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_popular_tags(
    limit: int = 50,
    app_db_path: Optional[Path] = None,
) -> List[TagInfo]:
    """
    Возвращает список популярных тегов с частотой использования среди доступных стикеров.
    """
    a_path = (app_db_path or config.APP_DB_PATH).resolve()
    if not a_path.exists():
        return []

    conn = get_app_db(a_path)
    try:
        limit = max(1, min(200, limit))
        query = """
            SELECT j.value AS tag, COUNT(*) AS count
            FROM asset_meta m, json_each(m.tags_json) j
            WHERE m.usable_in_service = 1 AND m.takedown = 0
            GROUP BY tag
            ORDER BY count DESC, tag ASC
            LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        return [TagInfo(name=str(r["tag"]), count=int(r["count"])) for r in rows]
    except Exception as exc:
        logger.warning("Ошибка при получении популярных тегов: %s", exc)
        return []
    finally:
        conn.close()
