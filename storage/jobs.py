"""Управление заданиями и индивидуальными параметрами макетов в SQLite."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from storage.database import _db_write_lock, get_storage_conn

JOB_CORE_COLS = {
    "job_id",
    "user_id",
    "orig_stem",
    "input_filename",
    "eps_filename",
    "offset_mm",
    "size_mm",
    "keep_background",
    "perf_cut_mode",
    "perf_cut_passes",
    "nesting_qty",
    "roll_width_mm",
    "stretch_x",
    "stretch_y",
    "fit_a4_sheet",
    "menu_tab",
    "num_contours",
    "perf_contours",
    "width_mm",
    "height_mm",
    "in_hot_folder",
    "hot_filename",
    "hot_folder_request_status",
    "hot_folder_requested_at",
    "created_at",
    "updated_at",
}


def _row_to_job(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    res = dict(row)
    # Приводим типы
    res["user_id"] = int(res["user_id"])
    res["offset_mm"] = float(res.get("offset_mm", 2.5))
    res["size_mm"] = float(res.get("size_mm", 100.0))
    res["keep_background"] = bool(res.get("keep_background", 0))
    res["perf_cut_mode"] = str(res.get("perf_cut_mode", "none"))
    res["perf_cut_passes"] = int(res.get("perf_cut_passes", 2))
    res["nesting_qty"] = int(res.get("nesting_qty", 1))
    res["roll_width_mm"] = float(res.get("roll_width_mm", 1370.0))
    res["stretch_x"] = float(res.get("stretch_x", 1.0))
    res["stretch_y"] = float(res.get("stretch_y", 1.0))
    res["fit_a4_sheet"] = bool(res.get("fit_a4_sheet", 0))
    res["menu_tab"] = str(res.get("menu_tab", "main"))
    res["num_contours"] = int(res.get("num_contours", 1))
    res["perf_contours"] = int(res.get("perf_contours", 0))
    res["width_mm"] = float(res.get("width_mm", 100.0))
    res["height_mm"] = float(res.get("height_mm", 100.0))
    res["in_hot_folder"] = bool(res.get("in_hot_folder", 0))
    res["hot_filename"] = str(res.get("hot_filename", ""))
    res["hot_folder_request_status"] = str(res.get("hot_folder_request_status", ""))
    res["hot_folder_requested_at"] = float(res.get("hot_folder_requested_at", 0.0))
    res["created_at"] = float(res.get("created_at", 0.0))
    res["updated_at"] = float(res.get("updated_at", res.get("created_at", 0.0)))

    # Десериализация pricing_json
    p_json = res.pop("pricing_json", None)
    if p_json:
        try:
            res["pricing"] = json.loads(p_json)
        except Exception:
            res["pricing"] = {}
    else:
        res["pricing"] = {}

    # Десериализация extra_json
    e_json = res.pop("extra_json", None)
    if e_json:
        try:
            extra = json.loads(e_json)
            if isinstance(extra, dict):
                for ek, ev in extra.items():
                    if ek not in res:
                        res[ek] = ev
        except Exception:
            pass

    return res


def save_job(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Сохраняет или обновляет запись задания."""
    now = time.time()
    conn = get_storage_conn()
    job_id = str(meta["job_id"]).strip()
    user_id = int(meta["user_id"])
    created_at = float(meta.get("created_at") or now)
    updated_at = float(meta.get("updated_at") or now)

    pricing_dict = meta.get("pricing") or {}
    pricing_json = json.dumps(pricing_dict, ensure_ascii=False) if isinstance(pricing_dict, dict) else "{}"

    extra_dict: Dict[str, Any] = {}
    for k, v in meta.items():
        if k not in JOB_CORE_COLS and k != "pricing":
            extra_dict[k] = v
    extra_json = json.dumps(extra_dict, ensure_ascii=False)

    payload = {
        "job_id": job_id,
        "user_id": user_id,
        "orig_stem": str(meta.get("orig_stem", "sticker")),
        "input_filename": str(meta.get("input_filename", "input.jpg")),
        "eps_filename": str(meta.get("eps_filename", f"{meta.get('orig_stem', 'sticker')}.eps")),
        "offset_mm": float(meta.get("offset_mm", 2.5)),
        "size_mm": float(meta.get("size_mm", 100.0)),
        "keep_background": 1 if meta.get("keep_background") else 0,
        "perf_cut_mode": str(meta.get("perf_cut_mode", "none")),
        "perf_cut_passes": int(meta.get("perf_cut_passes", 2)),
        "nesting_qty": int(meta.get("nesting_qty", 1)),
        "roll_width_mm": float(meta.get("roll_width_mm", 1370.0)),
        "stretch_x": float(meta.get("stretch_x", 1.0)),
        "stretch_y": float(meta.get("stretch_y", 1.0)),
        "fit_a4_sheet": 1 if meta.get("fit_a4_sheet") else 0,
        "menu_tab": str(meta.get("menu_tab", "main")),
        "num_contours": int(meta.get("num_contours", 1)),
        "perf_contours": int(meta.get("perf_contours", 0)),
        "width_mm": float(meta.get("width_mm", 100.0)),
        "height_mm": float(meta.get("height_mm", 100.0)),
        "in_hot_folder": 1 if meta.get("in_hot_folder") else 0,
        "hot_filename": str(meta.get("hot_filename", "")),
        "hot_folder_request_status": str(meta.get("hot_folder_request_status", "")),
        "hot_folder_requested_at": float(meta.get("hot_folder_requested_at", 0.0)),
        "pricing_json": pricing_json,
        "extra_json": extra_json,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    with _db_write_lock, conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, first_seen, last_seen, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, f"User {user_id}", created_at, updated_at, updated_at),
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, user_id, orig_stem, input_filename, eps_filename,
                offset_mm, size_mm, keep_background, perf_cut_mode,
                perf_cut_passes, nesting_qty, roll_width_mm, stretch_x, stretch_y,
                fit_a4_sheet, menu_tab, num_contours, perf_contours, width_mm,
                height_mm, in_hot_folder, hot_filename, hot_folder_request_status,
                hot_folder_requested_at, pricing_json, extra_json, created_at, updated_at
            ) VALUES (
                :job_id, :user_id, :orig_stem, :input_filename, :eps_filename,
                :offset_mm, :size_mm, :keep_background, :perf_cut_mode,
                :perf_cut_passes, :nesting_qty, :roll_width_mm, :stretch_x, :stretch_y,
                :fit_a4_sheet, :menu_tab, :num_contours, :perf_contours, :width_mm,
                :height_mm, :in_hot_folder, :hot_filename, :hot_folder_request_status,
                :hot_folder_requested_at, :pricing_json, :extra_json, :created_at, :updated_at
            )
            ON CONFLICT(job_id) DO UPDATE SET
                orig_stem = excluded.orig_stem,
                input_filename = excluded.input_filename,
                eps_filename = excluded.eps_filename,
                offset_mm = excluded.offset_mm,
                size_mm = excluded.size_mm,
                keep_background = excluded.keep_background,
                perf_cut_mode = excluded.perf_cut_mode,
                perf_cut_passes = excluded.perf_cut_passes,
                nesting_qty = excluded.nesting_qty,
                roll_width_mm = excluded.roll_width_mm,
                stretch_x = excluded.stretch_x,
                stretch_y = excluded.stretch_y,
                fit_a4_sheet = excluded.fit_a4_sheet,
                menu_tab = excluded.menu_tab,
                num_contours = excluded.num_contours,
                perf_contours = excluded.perf_contours,
                width_mm = excluded.width_mm,
                height_mm = excluded.height_mm,
                in_hot_folder = excluded.in_hot_folder,
                hot_filename = excluded.hot_filename,
                hot_folder_request_status = excluded.hot_folder_request_status,
                hot_folder_requested_at = excluded.hot_folder_requested_at,
                pricing_json = excluded.pricing_json,
                extra_json = excluded.extra_json,
                updated_at = excluded.updated_at
            """,
            payload,
        )

    saved = get_job(job_id)
    return saved if saved is not None else meta


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает информацию о задании по job_id."""
    conn = get_storage_conn()
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    return _row_to_job(row)


def update_job(job_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Обновляет отдельные поля существующего задания."""
    current = get_job(job_id)
    if current is None:
        return None
    current.update(kwargs)
    current["updated_at"] = time.time()
    return save_job(current)


def delete_job(job_id: str) -> bool:
    """Удаляет запись задания из БД."""
    conn = get_storage_conn()
    with _db_write_lock, conn:
        cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (str(job_id),))
        return cursor.rowcount > 0


def delete_user_jobs(user_id: int) -> int:
    """Удаляет все записи заданий указанного пользователя."""
    conn = get_storage_conn()
    with _db_write_lock, conn:
        cursor = conn.execute("DELETE FROM jobs WHERE user_id = ?", (int(user_id),))
        return cursor.rowcount


def count_user_jobs(user_id: int) -> int:
    """Возвращает количество заданий пользователя."""
    conn = get_storage_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE user_id = ?", (int(user_id),)).fetchone()
    return int(row["cnt"]) if row else 0


def list_all_jobs(filter_user_id: Optional[int] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает список всех заданий, отсортированных по новизне."""
    conn = get_storage_conn()
    if filter_user_id is not None:
        sql = "SELECT * FROM jobs WHERE user_id = ? ORDER BY updated_at DESC"
        params: List[Any] = [int(filter_user_id)]
    else:
        sql = "SELECT * FROM jobs ORDER BY updated_at DESC"
        params = []

    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    res: List[Dict[str, Any]] = []
    for r in rows:
        j = _row_to_job(r)
        if j:
            res.append(j)
    return res
