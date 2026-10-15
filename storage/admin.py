"""Управление настройками администратора (Live-шпион, блокировки) в SQLite."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from config import LIVE_SPY_DEFAULT
from storage.database import _db_write_lock, get_storage_conn


def get_admin_state() -> Dict[str, Any]:
    """Возвращает текущие настройки администратора."""
    defaults: Dict[str, Any] = {
        "live_spy_enabled": LIVE_SPY_DEFAULT,
        "banned_users": [],
    }
    conn = get_storage_conn()
    rows = conn.execute("SELECT key, value_json FROM admin_state").fetchall()
    for r in rows:
        k = r["key"]
        try:
            val = json.loads(r["value_json"])
            defaults[k] = val
        except Exception:
            pass
    return defaults


def update_admin_state(**kwargs: Any) -> Dict[str, Any]:
    """Обновляет одну или несколько настроек администратора."""
    now = time.time()
    conn = get_storage_conn()

    with _db_write_lock, conn:
        for k, v in kwargs.items():
            val_json = json.dumps(v, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO admin_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (k, val_json, now),
            )
    return get_admin_state()


def is_user_banned(user_id: int, admin_ids: Optional[List[int]] = None) -> bool:
    """Проверяет, заблокирован ли пользователь (администраторы никогда не блокируются)."""
    uid = int(user_id)
    if admin_ids and uid in admin_ids:
        return False
    state = get_admin_state()
    banned = [int(x) for x in state.get("banned_users", [])]
    return uid in banned


def toggle_ban_user(target_uid: int) -> bool:
    """Переключает статус блокировки пользователя."""
    uid = int(target_uid)
    state = get_admin_state()
    banned = {int(x) for x in state.get("banned_users", [])}
    if uid in banned:
        banned.remove(uid)
        now_banned = False
    else:
        banned.add(uid)
        now_banned = True
    update_admin_state(banned_users=sorted(list(banned)))
    return now_banned
