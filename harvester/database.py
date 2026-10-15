#!/usr/bin/env python3
"""
Хранилище метаданных SQLite (в режиме WAL) для автономного парсера 24/7.
Обеспечивает:
- Очередь тем (topics) с приоритетами и авто-расширением
- Дедупликацию по URL и перцептивному хэшу (pHash 64-bit, расстояние Хэмминга)
- Реестр готовых макетов (.eps + .png + превью + пути в Google Drive)
- Живое состояние воркера (harvester_state) для управления из Telegram-бота и веб-панели :8090
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from harvester.config import DB_PATH, HARVESTER_ENABLED, PHASH_HAMMING_THRESHOLD

ALLOWED_LICENSES = ("own", "licensed", "cc0", "unknown")

LICENSE_LABELS = {
    "own": "Собственный макет",
    "licensed": "Лицензированный макет",
    "cc0": "Общественное достояние (CC0)",
    "unknown": "Лицензия не подтверждена",
}

_db_lock = threading.RLock()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def slugify_folder(name: str) -> str:
    """Формирует чистое имя папки без пробелов и спецсимволов (сохраняя кириллицу и латиницу)."""
    s = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", name.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:64] or "Тема"


def url_to_hash(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8", errors="ignore")).hexdigest()


def _migrate_assets_table(conn: sqlite3.Connection) -> None:
    """Безопасная миграция таблицы assets: проверка наличия колонок и добавление при отсутствии."""
    cursor = conn.execute("PRAGMA table_info(assets)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "source_url" not in existing_columns:
        conn.execute("ALTER TABLE assets ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
    if "license" not in existing_columns:
        conn.execute("ALTER TABLE assets ADD COLUMN license TEXT NOT NULL DEFAULT 'unknown'")
    if "uploaded_by" not in existing_columns:
        conn.execute("ALTER TABLE assets ADD COLUMN uploaded_by TEXT NOT NULL DEFAULT ''")


def init_db() -> None:
    with _db_lock:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS harvester_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_lvl1 TEXT NOT NULL,
                    category_lvl2 TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    folder_slug TEXT NOT NULL,
                    search_queries_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 10,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    harvest_cycles INTEGER NOT NULL DEFAULT 0,
                    items_count INTEGER NOT NULL DEFAULT 0,
                    packs_count INTEGER NOT NULL DEFAULT 0,
                    last_harvested_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    UNIQUE(category_lvl1, category_lvl2, folder_slug)
                );

                CREATE TABLE IF NOT EXISTS seen_urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS phashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phash_hex TEXT NOT NULL,
                    phash_int INTEGER NOT NULL,
                    seg0 INTEGER NOT NULL DEFAULT 0,
                    seg1 INTEGER NOT NULL DEFAULT 0,
                    seg2 INTEGER NOT NULL DEFAULT 0,
                    seg3 INTEGER NOT NULL DEFAULT 0,
                    asset_id TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_phashes_hex ON phashes(phash_hex);
                CREATE INDEX IF NOT EXISTS idx_phashes_seg0 ON phashes(seg0);
                CREATE INDEX IF NOT EXISTS idx_phashes_seg1 ON phashes(seg1);
                CREATE INDEX IF NOT EXISTS idx_phashes_seg2 ON phashes(seg2);
                CREATE INDEX IF NOT EXISTS idx_phashes_seg3 ON phashes(seg3);

                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    topic_id INTEGER NOT NULL,
                    category_lvl1 TEXT NOT NULL,
                    category_lvl2 TEXT NOT NULL,
                    folder_slug TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    license TEXT NOT NULL DEFAULT 'unknown',
                    uploaded_by TEXT NOT NULL DEFAULT '',
                    phash_hex TEXT NOT NULL,
                    width_px INTEGER NOT NULL DEFAULT 0,
                    height_px INTEGER NOT NULL DEFAULT 0,
                    width_mm REAL NOT NULL DEFAULT 100.0,
                    height_mm REAL NOT NULL DEFAULT 100.0,
                    offset_mm REAL NOT NULL DEFAULT 2.5,
                    perf_cut_mode TEXT NOT NULL DEFAULT 'none',
                    num_contours INTEGER NOT NULL DEFAULT 1,
                    eps_size_mb REAL NOT NULL DEFAULT 0.0,
                    rel_folder_path TEXT NOT NULL,
                    eps_filename TEXT NOT NULL,
                    png_filename TEXT NOT NULL,
                    local_eps_path TEXT NOT NULL,
                    local_png_path TEXT NOT NULL,
                    local_preview_path TEXT NOT NULL,
                    gdrive_folder_path TEXT NOT NULL,
                    synced_to_gdrive INTEGER NOT NULL DEFAULT 0,
                    in_hot_folder INTEGER NOT NULL DEFAULT 0,
                    parent_asset_id TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assets_cat ON assets(category_lvl1, category_lvl2, folder_slug);
                CREATE INDEX IF NOT EXISTS idx_assets_synced ON assets(synced_to_gdrive);
                CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC);
                """
            )
            _migrate_assets_table(conn)
            conn.commit()
        finally:
            conn.close()

    # Устанавливаем дефолтные значения состояния, если их ещё нет
    defaults = {
        "enabled": bool(HARVESTER_ENABLED),
        "status_text": "Готов к работе",
        "current_topic": "—",
        "current_category": "—",
        "current_source": "—",
        "total_downloaded": 0,
        "total_rejected_mockup": 0,
        "total_rejected_lowres": 0,
        "total_rejected_dup": 0,
        "total_eps_generated": 0,
        "total_packs_split": 0,
        "total_synced_gdrive": 0,
        "total_synced_mb": 0.0,
        "last_sync_at": 0.0,
        "last_heartbeat": time.time(),
        "last_error": "",
        "archive_chat_id": "",
    }
    for k, v in defaults.items():
        if get_state(k, None) is None:
            set_state(k, v)


def get_state(key: str, default: Any = None) -> Any:
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM harvester_state WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except Exception:
                return row["value"]
        finally:
            conn.close()


def set_state(key: str, value: Any) -> None:
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO harvester_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def incr_state(key: str, delta: float = 1) -> float:
    with _db_lock:
        curr = get_state(key, 0)
        try:
            new_val = (float(curr) if isinstance(delta, float) else int(curr)) + delta
        except Exception:
            new_val = delta
        set_state(key, new_val)
        return new_val


def get_all_state() -> Dict[str, Any]:
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT key, value FROM harvester_state").fetchall()
            out: Dict[str, Any] = {}
            for r in rows:
                try:
                    out[r["key"]] = json.loads(r["value"])
                except Exception:
                    out[r["key"]] = r["value"]
            return out
        finally:
            conn.close()


def upsert_topic(
    category_lvl1: str,
    category_lvl2: str,
    topic_name: str,
    search_queries: List[str],
    priority: int = 10,
) -> int:
    folder_slug = slugify_folder(topic_name)
    now = time.time()
    queries_json = json.dumps(list(dict.fromkeys(search_queries)), ensure_ascii=False)
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO topics(
                    category_lvl1, category_lvl2, topic_name, folder_slug,
                    search_queries_json, priority, is_enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(category_lvl1, category_lvl2, folder_slug) DO UPDATE SET
                    priority = MAX(topics.priority, excluded.priority),
                    is_enabled = 1
                """,
                (
                    category_lvl1,
                    category_lvl2,
                    topic_name.strip(),
                    folder_slug,
                    queries_json,
                    int(priority),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT id FROM topics
                WHERE category_lvl1 = ? AND category_lvl2 = ? AND folder_slug = ?
                """,
                (category_lvl1, category_lvl2, folder_slug),
            ).fetchone()
            return int(row["id"]) if row else 0
        finally:
            conn.close()


def pick_next_topic() -> Optional[Dict[str, Any]]:
    """
    Выбирает следующую тему для парсинга:
    1. Сначала темы с максимальным приоритетом (например, заказанные пользователем из бота priority >= 100)
    2. Затем темы с наименьшим количеством циклов обхода и самым старым временем обхода,
       чередуя основные категории (Стикерпаки / Авто / Одиночные / Баннеры).
    """
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM topics
                WHERE is_enabled = 1
                ORDER BY
                    CASE WHEN priority >= 100 THEN 0 ELSE 1 END ASC,
                    priority DESC,
                    harvest_cycles ASC,
                    last_harvested_at ASC,
                    RANDOM()
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["search_queries"] = json.loads(d.get("search_queries_json", "[]"))
            except Exception:
                d["search_queries"] = [d["topic_name"]]
            return d
        finally:
            conn.close()


def mark_topic_harvested(topic_id: int, added_items: int = 0, added_packs: int = 0) -> None:
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE topics
                SET harvest_cycles = harvest_cycles + 1,
                    items_count = items_count + ?,
                    packs_count = packs_count + ?,
                    priority = CASE WHEN priority >= 100 THEN 20 ELSE priority END,
                    last_harvested_at = ?
                WHERE id = ?
                """,
                (int(added_items), int(added_packs), time.time(), int(topic_id)),
            )
            conn.commit()
        finally:
            conn.close()


def is_url_seen(url: str) -> bool:
    uh = url_to_hash(url)
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM seen_urls WHERE url_hash = ?", (uh,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def record_seen_url(url: str, status: str, reason: str = "") -> None:
    uh = url_to_hash(url)
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO seen_urls(url_hash, url, status, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uh, url.strip(), status, reason[:200], time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def _hex_to_signed_int64(phash_hex: str) -> int:
    val = int(phash_hex, 16) & ((1 << 64) - 1)
    if val >= (1 << 63):
        val -= 1 << 64
    return val


def _int64_to_segments(signed_int: int) -> Tuple[int, int, int, int]:
    """Разбивает 64-бит хэш на 4 сегмента по 16 бит для индексированного поиска кандидатов."""
    unsigned = signed_int & ((1 << 64) - 1)
    return (
        (unsigned >> 48) & 0xFFFF,
        (unsigned >> 32) & 0xFFFF,
        (unsigned >> 16) & 0xFFFF,
        unsigned & 0xFFFF,
    )


def _hamming_distance_64(a_signed: int, b_signed: int) -> int:
    xor_val = (a_signed ^ b_signed) & ((1 << 64) - 1)
    return xor_val.bit_count()


def is_phash_duplicate(phash_hex: str, threshold: int = PHASH_HAMMING_THRESHOLD) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, есть ли уже в базе картинка с таким же или почти идентичным перцептивным хэшем.
    Использует 4×16-бит сегментный индекс для быстрого поиска кандидатов (вместо O(N) перебора).
    Возвращает (is_dup, matched_phash_hex).
    """
    if not phash_hex:
        return False, None
    target_int = _hex_to_signed_int64(phash_hex)
    seg0, seg1, seg2, seg3 = _int64_to_segments(target_int)
    with _db_lock:
        conn = get_conn()
        try:
            # 1. Точное совпадение — мгновенно по индексу
            exact = conn.execute(
                "SELECT phash_hex FROM phashes WHERE phash_hex = ? LIMIT 1",
                (phash_hex,),
            ).fetchone()
            if exact:
                return True, exact["phash_hex"]

            if threshold <= 0:
                return False, None

            # 2. Поиск кандидатов по сегментному индексу:
            # Если Hamming distance <= threshold, то хотя бы в одном из 4 сегментов
            # должно быть точное совпадение (pigeonhole principle для threshold <= 15).
            candidates = conn.execute(
                """
                SELECT DISTINCT phash_hex, phash_int FROM phashes
                WHERE seg0 = ? OR seg1 = ? OR seg2 = ? OR seg3 = ?
                """,
                (seg0, seg1, seg2, seg3),
            ).fetchall()
            for r in candidates:
                if _hamming_distance_64(target_int, int(r["phash_int"])) <= threshold:
                    return True, r["phash_hex"]
            return False, None
        finally:
            conn.close()


def record_phash(phash_hex: str, status: str = "accepted", asset_id: str = "") -> None:
    if not phash_hex:
        return
    p_int = _hex_to_signed_int64(phash_hex)
    seg0, seg1, seg2, seg3 = _int64_to_segments(p_int)
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO phashes(phash_hex, phash_int, seg0, seg1, seg2, seg3, asset_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (phash_hex, p_int, seg0, seg1, seg2, seg3, asset_id, status, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def register_asset(asset: Dict[str, Any]) -> None:
    lic = str(asset.get("license", "unknown")).lower().strip()
    if lic not in ALLOWED_LICENSES:
        lic = "unknown"
    uploaded_by = str(asset.get("uploaded_by", "")).strip()
    source_url = str(asset.get("source_url", "")).strip()

    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO assets(
                    asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                    topic_name, asset_type, title, source_name, source_url, license, uploaded_by, phash_hex,
                    width_px, height_px, width_mm, height_mm, offset_mm, perf_cut_mode,
                    num_contours, eps_size_mb, rel_folder_path, eps_filename, png_filename,
                    local_eps_path, local_png_path, local_preview_path, gdrive_folder_path,
                    synced_to_gdrive, in_hot_folder, parent_asset_id, created_at
                )
                VALUES (
                    :asset_id, :topic_id, :category_lvl1, :category_lvl2, :folder_slug,
                    :topic_name, :asset_type, :title, :source_name, :source_url, :license, :uploaded_by, :phash_hex,
                    :width_px, :height_px, :width_mm, :height_mm, :offset_mm, :perf_cut_mode,
                    :num_contours, :eps_size_mb, :rel_folder_path, :eps_filename, :png_filename,
                    :local_eps_path, :local_png_path, :local_preview_path, :gdrive_folder_path,
                    :synced_to_gdrive, :in_hot_folder, :parent_asset_id, :created_at
                )
                """,
                {
                    "asset_id": asset["asset_id"],
                    "topic_id": int(asset.get("topic_id", 0)),
                    "category_lvl1": asset["category_lvl1"],
                    "category_lvl2": asset["category_lvl2"],
                    "folder_slug": asset["folder_slug"],
                    "topic_name": asset.get("topic_name", asset["folder_slug"]),
                    "asset_type": asset["asset_type"],
                    "title": asset.get("title", asset["eps_filename"]),
                    "source_name": asset.get("source_name", "web"),
                    "source_url": source_url,
                    "license": lic,
                    "uploaded_by": uploaded_by,
                    "phash_hex": asset.get("phash_hex", ""),
                    "width_px": int(asset.get("width_px", 0)),
                    "height_px": int(asset.get("height_px", 0)),
                    "width_mm": float(asset.get("width_mm", 100.0)),
                    "height_mm": float(asset.get("height_mm", 100.0)),
                    "offset_mm": float(asset.get("offset_mm", 2.5)),
                    "perf_cut_mode": asset.get("perf_cut_mode", "none"),
                    "num_contours": int(asset.get("num_contours", 1)),
                    "eps_size_mb": float(asset.get("eps_size_mb", 0.0)),
                    "rel_folder_path": asset["rel_folder_path"],
                    "eps_filename": asset["eps_filename"],
                    "png_filename": asset.get("png_filename", ""),
                    "local_eps_path": str(asset.get("local_eps_path", "")),
                    "local_png_path": str(asset.get("local_png_path", "")),
                    "local_preview_path": str(asset.get("local_preview_path", "")),
                    "gdrive_folder_path": asset.get("gdrive_folder_path", ""),
                    "synced_to_gdrive": int(asset.get("synced_to_gdrive", 0)),
                    "in_hot_folder": int(asset.get("in_hot_folder", 0)),
                    "parent_asset_id": asset.get("parent_asset_id", ""),
                    "created_at": float(asset.get("created_at", time.time())),
                },
            )
            conn.commit()
        finally:
            conn.close()


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_assets(
    category_lvl1: Optional[str] = None,
    category_lvl2: Optional[str] = None,
    folder_slug: Optional[str] = None,
    asset_type: Optional[str] = None,
    search_query: Optional[str] = None,
    limit: int = 60,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = []
    params: List[Any] = []
    if category_lvl1 and category_lvl1 != "all":
        clauses.append("category_lvl1 = ?")
        params.append(category_lvl1)
    if category_lvl2 and category_lvl2 != "all":
        clauses.append("category_lvl2 = ?")
        params.append(category_lvl2)
    if folder_slug and folder_slug != "all":
        clauses.append("folder_slug = ?")
        params.append(folder_slug)
    if asset_type and asset_type != "all":
        clauses.append("asset_type = ?")
        params.append(asset_type)
    if search_query:
        q_like = f"%{search_query.strip()}%"
        clauses.append("(title LIKE ? OR topic_name LIKE ? OR folder_slug LIKE ? OR category_lvl2 LIKE ?)")
        params.extend([q_like, q_like, q_like, q_like])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT * FROM assets
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_unsynced_assets(limit: int = 100) -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM assets WHERE synced_to_gdrive = 0 ORDER BY created_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def mark_assets_synced(asset_ids: List[str]) -> None:
    if not asset_ids:
        return
    with _db_lock:
        conn = get_conn()
        try:
            placeholders = ",".join("?" for _ in asset_ids)
            conn.execute(
                f"UPDATE assets SET synced_to_gdrive = 1 WHERE asset_id IN ({placeholders})",
                asset_ids,
            )
            conn.commit()
        finally:
            conn.close()


def mark_asset_in_hot_folder(asset_id: str) -> None:
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE assets SET in_hot_folder = 1 WHERE asset_id = ?",
                (asset_id,),
            )
            conn.commit()
        finally:
            conn.close()


def get_catalog_summary() -> Dict[str, Any]:
    """Сводная статистика по категориям, папкам и объёму базы для Telegram-бота и веб-панели."""
    with _db_lock:
        conn = get_conn()
        try:
            tot = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_assets,
                    SUM(CASE WHEN asset_type IN ('pack_sheet', 'auto_pack_sheet') THEN 1 ELSE 0 END) AS total_packs,
                    SUM(CASE WHEN asset_type = 'single_sticker' THEN 1 ELSE 0 END) AS total_singles,
                    SUM(CASE WHEN asset_type = 'car_decal' THEN 1 ELSE 0 END) AS total_car_decals,
                    SUM(CASE WHEN asset_type = 'banner' THEN 1 ELSE 0 END) AS total_banners,
                    SUM(CASE WHEN synced_to_gdrive = 1 THEN 1 ELSE 0 END) AS synced_count,
                    COALESCE(SUM(eps_size_mb), 0.0) AS total_eps_mb,
                    COALESCE(SUM(num_contours), 0) AS total_contours
                FROM assets
                """
            ).fetchone()

            cats = conn.execute(
                """
                SELECT
                    category_lvl1,
                    category_lvl2,
                    COUNT(DISTINCT folder_slug) AS topics_with_files,
                    COUNT(*) AS files_count,
                    ROUND(COALESCE(SUM(eps_size_mb), 0.0), 1) AS size_mb
                FROM assets
                GROUP BY category_lvl1, category_lvl2
                ORDER BY category_lvl1 ASC, files_count DESC
                """
            ).fetchall()

            topics_stat = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_topics,
                    SUM(CASE WHEN harvest_cycles > 0 THEN 1 ELSE 0 END) AS harvested_topics,
                    SUM(CASE WHEN priority >= 100 THEN 1 ELSE 0 END) AS urgent_topics
                FROM topics
                """
            ).fetchone()

            recent_folders = conn.execute(
                """
                SELECT
                    category_lvl1, category_lvl2, folder_slug, topic_name,
                    COUNT(*) AS assets_cnt,
                    MAX(created_at) AS last_ts
                FROM assets
                GROUP BY category_lvl1, category_lvl2, folder_slug
                ORDER BY last_ts DESC
                LIMIT 20
                """
            ).fetchall()

            return {
                "totals": dict(tot) if tot else {},
                "categories": [dict(c) for c in cats],
                "topics_stat": dict(topics_stat) if topics_stat else {},
                "recent_folders": [dict(f) for f in recent_folders],
            }
        finally:
            conn.close()


def get_folder_tree() -> List[Dict[str, Any]]:
    """
    Возвращает полное иерархическое дерево папок с готовыми макетами:
    [
      {
        "category_lvl1": "01_Стикерпаки",
        "files_count": 1325,
        "folders_count": 108,
        "size_mb": 412.5,
        "subcategories": [
          {
            "category_lvl2": "Сериалы_и_Кино",
            "files_count": 500,
            "folders_count": 42,
            "size_mb": 160.2,
            "folders": [
              {
                "folder_slug": "Слово_Пацана",
                "topic_name": "Слово Пацана",
                "files_count": 14,
                "packs_count": 4,
                "singles_count": 10,
                "size_mb": 18.4,
                "last_ts": 1728720000.0,
              },
              ...
            ]
          },
          ...
        ]
      },
      ...
    ]
    """
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    category_lvl1,
                    category_lvl2,
                    folder_slug,
                    MAX(topic_name) AS topic_name,
                    COUNT(*) AS files_count,
                    SUM(CASE WHEN asset_type IN ('pack_sheet', 'auto_pack_sheet') THEN 1 ELSE 0 END) AS packs_count,
                    SUM(CASE WHEN asset_type NOT IN ('pack_sheet', 'auto_pack_sheet') THEN 1 ELSE 0 END) AS singles_count,
                    ROUND(COALESCE(SUM(eps_size_mb), 0.0), 1) AS size_mb,
                    MAX(created_at) AS last_ts
                FROM assets
                GROUP BY category_lvl1, category_lvl2, folder_slug
                ORDER BY category_lvl1 ASC, category_lvl2 ASC, files_count DESC, folder_slug ASC
                """
            ).fetchall()
        finally:
            conn.close()

    c1_map: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        c1 = r["category_lvl1"]
        c2 = r["category_lvl2"]
        f_cnt = int(r["files_count"] or 0)
        p_cnt = int(r["packs_count"] or 0)
        s_cnt = int(r["singles_count"] or 0)
        sz = float(r["size_mb"] or 0.0)

        if c1 not in c1_map:
            c1_map[c1] = {
                "category_lvl1": c1,
                "files_count": 0,
                "folders_count": 0,
                "size_mb": 0.0,
                "_c2_map": {},
            }
        c1_entry = c1_map[c1]
        c1_entry["files_count"] += f_cnt
        c1_entry["folders_count"] += 1
        c1_entry["size_mb"] = round(c1_entry["size_mb"] + sz, 1)

        c2_map = c1_entry["_c2_map"]
        if c2 not in c2_map:
            c2_map[c2] = {
                "category_lvl2": c2,
                "files_count": 0,
                "folders_count": 0,
                "size_mb": 0.0,
                "folders": [],
            }
        c2_entry = c2_map[c2]
        c2_entry["files_count"] += f_cnt
        c2_entry["folders_count"] += 1
        c2_entry["size_mb"] = round(c2_entry["size_mb"] + sz, 1)
        c2_entry["folders"].append(
            {
                "folder_slug": r["folder_slug"],
                "topic_name": (r["topic_name"] or r["folder_slug"]).replace("_", " "),
                "files_count": f_cnt,
                "packs_count": p_cnt,
                "singles_count": s_cnt,
                "size_mb": sz,
                "last_ts": float(r["last_ts"] or 0.0),
            }
        )

    result: List[Dict[str, Any]] = []
    for c1_key in sorted(c1_map.keys()):
        c1_entry = c1_map[c1_key]
        c2_list = sorted(
            c1_entry.pop("_c2_map").values(),
            key=lambda x: (-x["files_count"], x["category_lvl2"]),
        )
        c1_entry["subcategories"] = c2_list
        result.append(c1_entry)
    return result

