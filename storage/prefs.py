"""Управление настройками пользователя по умолчанию в SQLite."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from storage.database import _db_write_lock, get_storage_conn

DEFAULT_PREFS: Dict[str, Any] = {
    "offset_mm": 2.5,
    "size_mm": 100.0,
    "keep_background": False,
    "vectorize": False,
    "perf_cut_mode": "none",
    "perf_cut_passes": 2,
    "nesting_qty": 1,
    "roll_width_mm": 1370.0,
    "stretch_x": 1.0,
    "stretch_y": 1.0,
    "fit_a4_sheet": False,
    "menu_tab": "main",
}

KNOWN_PREF_COLS = {
    "offset_mm",
    "size_mm",
    "keep_background",
    "vectorize",
    "perf_cut_mode",
    "perf_cut_passes",
    "nesting_qty",
    "roll_width_mm",
    "stretch_x",
    "stretch_y",
    "fit_a4_sheet",
    "menu_tab",
}


def _row_to_prefs(row: Any) -> Dict[str, Any]:
    """Преобразует строку SQLite в словарь настроек."""
    res = dict(DEFAULT_PREFS)
    if row is None:
        return res

    row_dict = dict(row)
    for col in KNOWN_PREF_COLS:
        if col in row_dict and row_dict[col] is not None:
            if col in ("keep_background", "vectorize", "fit_a4_sheet"):
                res[col] = bool(row_dict[col])
            elif col in ("offset_mm", "size_mm", "roll_width_mm", "stretch_x", "stretch_y"):
                res[col] = float(row_dict[col])
            elif col in ("perf_cut_passes", "nesting_qty"):
                res[col] = int(row_dict[col])
            else:
                res[col] = row_dict[col]

    extra_str = row_dict.get("extra_json")
    if extra_str:
        try:
            extra = json.loads(extra_str)
            if isinstance(extra, dict):
                res.update(extra)
        except Exception:
            pass

    # Всегда используем растровый режим (vtracer отключён ради стабильности)
    res["vectorize"] = False
    if float(res.get("roll_width_mm", 1370.0)) == 1000.0:
        res["roll_width_mm"] = 1370.0
    return res


def get_user_prefs(user_id: int) -> Dict[str, Any]:
    """Возвращает настройки по умолчанию пользователя."""
    conn = get_storage_conn()
    row = conn.execute("SELECT * FROM prefs WHERE user_id = ?", (int(user_id),)).fetchone()
    return _row_to_prefs(row)


def update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    """Обновляет настройки по умолчанию пользователя."""
    uid = int(user_id)
    now = time.time()
    conn = get_storage_conn()

    with _db_write_lock, conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, first_seen, last_seen, updated_at) VALUES (?, ?, ?, ?, ?)",
            (uid, f"User {uid}", now, now, now),
        )
        row = conn.execute("SELECT * FROM prefs WHERE user_id = ?", (uid,)).fetchone()
        current = _row_to_prefs(row)
        current.update(kwargs)

        known_vals: Dict[str, Any] = {}
        extra_vals: Dict[str, Any] = {}

        for k, v in current.items():
            if k in KNOWN_PREF_COLS:
                if k in ("keep_background", "vectorize", "fit_a4_sheet"):
                    known_vals[k] = 1 if v else 0
                else:
                    known_vals[k] = v
            else:
                extra_vals[k] = v

        extra_json = json.dumps(extra_vals, ensure_ascii=False)

        conn.execute(
            """
            INSERT INTO prefs (
                user_id, offset_mm, size_mm, keep_background, vectorize,
                perf_cut_mode, perf_cut_passes, nesting_qty, roll_width_mm,
                stretch_x, stretch_y, fit_a4_sheet, menu_tab, extra_json, updated_at
            ) VALUES (
                :user_id, :offset_mm, :size_mm, :keep_background, :vectorize,
                :perf_cut_mode, :perf_cut_passes, :nesting_qty, :roll_width_mm,
                :stretch_x, :stretch_y, :fit_a4_sheet, :menu_tab, :extra_json, :updated_at
            )
            ON CONFLICT(user_id) DO UPDATE SET
                offset_mm = excluded.offset_mm,
                size_mm = excluded.size_mm,
                keep_background = excluded.keep_background,
                vectorize = excluded.vectorize,
                perf_cut_mode = excluded.perf_cut_mode,
                perf_cut_passes = excluded.perf_cut_passes,
                nesting_qty = excluded.nesting_qty,
                roll_width_mm = excluded.roll_width_mm,
                stretch_x = excluded.stretch_x,
                stretch_y = excluded.stretch_y,
                fit_a4_sheet = excluded.fit_a4_sheet,
                menu_tab = excluded.menu_tab,
                extra_json = excluded.extra_json,
                updated_at = excluded.updated_at
            """,
            {
                "user_id": uid,
                "offset_mm": float(known_vals.get("offset_mm", 2.5)),
                "size_mm": float(known_vals.get("size_mm", 100.0)),
                "keep_background": int(known_vals.get("keep_background", 0)),
                "vectorize": int(known_vals.get("vectorize", 0)),
                "perf_cut_mode": str(known_vals.get("perf_cut_mode", "none")),
                "perf_cut_passes": int(known_vals.get("perf_cut_passes", 2)),
                "nesting_qty": int(known_vals.get("nesting_qty", 1)),
                "roll_width_mm": float(known_vals.get("roll_width_mm", 1370.0)),
                "stretch_x": float(known_vals.get("stretch_x", 1.0)),
                "stretch_y": float(known_vals.get("stretch_y", 1.0)),
                "fit_a4_sheet": int(known_vals.get("fit_a4_sheet", 0)),
                "menu_tab": str(known_vals.get("menu_tab", "main")),
                "extra_json": extra_json,
                "updated_at": now,
            },
        )
    return current


def load_all_prefs() -> Dict[str, Dict[str, Any]]:
    """Возвращает настройки всех пользователей {str(uid): {...}} для обратной совместимости."""
    conn = get_storage_conn()
    rows = conn.execute("SELECT * FROM prefs").fetchall()
    return {str(r["user_id"]): _row_to_prefs(r) for r in rows}


def delete_user_prefs(user_id: int) -> bool:
    """Удаляет настройки пользователя."""
    conn = get_storage_conn()
    with _db_write_lock, conn:
        cursor = conn.execute("DELETE FROM prefs WHERE user_id = ?", (int(user_id),))
        return cursor.rowcount > 0
