"""
Модуль чтения каталога harvester.db (только для чтения).
Строго соблюдает правила безопасности:
- Режим read-only (sqlite3 URI mode=ro).
- Защита от выхода за пределы хранилища (Path Traversal Protection).
- Отсутствие внутренних путей файловой системы в публичных ответах.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sticker_app.config as config
from sticker_app.models import (
    CategoryLevel2,
    CategoryTree,
    PaginationMeta,
    StickerDetail,
    StickerListResponse,
    StickerSummary,
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


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Открывает соединение с harvester.db строго в режиме read-only.
    """
    path = (db_path or config.HARVESTER_DB_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Файл базы harvester.db не найден: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_summary(row: sqlite3.Row, api_prefix: str = config.API_PREFIX) -> StickerSummary:
    """Преобразует строку assets в публичный StickerSummary."""
    asset_id = str(row["asset_id"])
    w_px = int(row["width_px"]) if row["width_px"] else 0
    h_px = int(row["height_px"]) if row["height_px"] else 0
    aspect = round(w_px / h_px, 4) if h_px > 0 else 1.0

    license_val = str(row["license"]) if row["license"] else "unknown"
    usable = license_val in ALLOWED_LICENSES or not config.LICENSE_FILTER

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
        aspect_ratio=aspect,
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
) -> StickerListResponse:
    """
    Получение списка стикеров с фильтрацией, пагинацией и проверкой наличия файлов.
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
        where_clauses.append("asset_type = ?")
        params.append(asset_type)

    # 2. Фильтры категорий и тем
    if filters.get("category_lvl1"):
        where_clauses.append("category_lvl1 = ?")
        params.append(filters["category_lvl1"])

    if filters.get("category_lvl2"):
        where_clauses.append("category_lvl2 = ?")
        params.append(filters["category_lvl2"])

    if filters.get("folder_slug"):
        where_clauses.append("folder_slug = ?")
        params.append(filters["folder_slug"])

    if filters.get("topic_name"):
        where_clauses.append("topic_name = ?")
        params.append(filters["topic_name"])

    # 3. Поиск по тексту в названии или теме
    search = filters.get("search")
    if search:
        search_pattern = f"%{search.strip()}%"
        where_clauses.append("(title LIKE ? OR topic_name LIKE ?)")
        params.extend([search_pattern, search_pattern])

    # 4. Лицензионный фильтр
    use_license_filter = filters.get("license_filter", config.LICENSE_FILTER)
    if use_license_filter:
        where_clauses.append("license IN ('own', 'licensed', 'cc0')")

    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = f"WHERE {where_sql}"

    conn = get_db_connection(db_path)
    try:
        # Подсчёт общего количества
        count_query = f"SELECT COUNT(*) FROM assets {where_sql}"
        cursor = conn.execute(count_query, params)
        total_items = cursor.fetchone()[0]

        total_pages = math.ceil(total_items / page_size) if total_items > 0 else 0
        offset = (page - 1) * page_size

        # Запрос выборки
        select_query = f"""
            SELECT
                asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                topic_name, asset_type, title, source_name, source_url,
                license, phash_hex, width_px, height_px, width_mm, height_mm,
                offset_mm, perf_cut_mode, num_contours, rel_folder_path,
                eps_filename, png_filename, parent_asset_id, created_at
            FROM assets
            {where_sql}
            ORDER BY created_at DESC
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
) -> Optional[StickerDetail]:
    """
    Получение детальной информации о стикере по его ID.
    Возвращает None, если стикер не найден или файл отсутствует на диске.
    """
    storage = (storage_root or config.STORAGE_ROOT).resolve()
    conn = get_db_connection(db_path)
    try:
        query = """
            SELECT
                asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                topic_name, asset_type, title, source_name, source_url,
                license, phash_hex, width_px, height_px, width_mm, height_mm,
                offset_mm, perf_cut_mode, num_contours, rel_folder_path,
                eps_filename, png_filename, parent_asset_id, created_at
            FROM assets
            WHERE asset_id = ?
        """
        row = conn.execute(query, (asset_id,)).fetchone()
        if not row:
            return None

        # Проверка лицензионного фильтра
        if config.LICENSE_FILTER and str(row["license"]) not in ALLOWED_LICENSES:
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
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Возвращает безопасный абсолютный путь к файлу макета (png или eps).
    Возвращает (path, content_type) или (None, None).
    """
    storage = (storage_root or config.STORAGE_ROOT).resolve()
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT rel_folder_path, png_filename, eps_filename, license FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if not row:
            return None, None

        if config.LICENSE_FILTER and str(row["license"]) not in ALLOWED_LICENSES:
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
) -> List[CategoryTree]:
    """
    Возвращает дерево категорий и подкатегорий с темами.
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses: List[str] = []
        params: List[Any] = []

        if asset_type:
            where_clauses.append("asset_type = ?")
            params.append(asset_type)

        if config.LICENSE_FILTER:
            where_clauses.append("license IN ('own', 'licensed', 'cc0')")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT
                category_lvl1,
                category_lvl2,
                folder_slug,
                topic_name,
                topic_id,
                COUNT(*) as count
            FROM assets
            {where_sql}
            GROUP BY category_lvl1, category_lvl2, folder_slug, topic_name, topic_id
            ORDER BY category_lvl1, category_lvl2, topic_name
        """
        rows = conn.execute(query, params).fetchall()

        # Группировка: cat1 -> cat2 -> list[TopicInfo]
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
) -> List[TopicInfo]:
    """
    Возвращает плоский список тем с фильтрацией по категории.
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses = ["t.is_enabled = 1"]
        params: List[Any] = []

        if category_lvl1:
            where_clauses.append("t.category_lvl1 = ?")
            params.append(category_lvl1)
        if category_lvl2:
            where_clauses.append("t.category_lvl2 = ?")
            params.append(category_lvl2)

        where_sql = " AND ".join(where_clauses)
        query = f"""
            SELECT
                t.id as topic_id,
                t.topic_name,
                t.folder_slug,
                t.category_lvl1,
                t.category_lvl2,
                COUNT(a.asset_id) as stickers_count
            FROM topics t
            LEFT JOIN assets a ON a.topic_id = t.id AND a.asset_type = 'single_sticker'
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
