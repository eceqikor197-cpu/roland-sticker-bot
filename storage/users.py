"""Управление пользователями и историей активности в SQLite."""

from __future__ import annotations

import html
import re
import time
from typing import Any, Dict, Optional

from storage.database import _db_write_lock, get_storage_conn


def record_user_activity(
    user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    action: str = "",
    last_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Регистрирует или обновляет активность пользователя."""
    uid = int(user_id)
    now = time.time()
    conn = get_storage_conn()

    with _db_write_lock, conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            fn = first_name or f"User {uid}"
            ln = last_name or ""
            un = username or ""
            conn.execute(
                """
                INSERT INTO users (
                    user_id, first_name, last_name, username,
                    first_seen, last_seen, jobs_count, last_action,
                    last_job_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (uid, fn, ln, un, now, now, action, last_job_id or "", now),
            )
            return {
                "user_id": uid,
                "first_name": fn,
                "last_name": ln,
                "username": un,
                "first_seen": now,
                "last_seen": now,
                "jobs_count": 0,
                "last_action": action,
                "last_job_id": last_job_id or "",
            }

        curr = dict(row)
        fn = first_name if first_name is not None else curr["first_name"]
        ln = last_name if last_name is not None else curr["last_name"]
        un = username if username is not None else curr["username"]
        act = action if action else curr["last_action"]
        ljid = last_job_id if last_job_id else curr["last_job_id"]

        conn.execute(
            """
            UPDATE users SET
                first_name = ?,
                last_name = ?,
                username = ?,
                last_seen = ?,
                last_action = ?,
                last_job_id = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (fn, ln, un, now, act, ljid, now, uid),
        )
        curr.update({
            "first_name": fn,
            "last_name": ln,
            "username": un,
            "last_seen": now,
            "last_action": act,
            "last_job_id": ljid,
        })
        return curr


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает информацию о пользователе или None."""
    conn = get_storage_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
    return dict(row) if row else None


def get_all_users() -> Dict[str, Dict[str, Any]]:
    """Возвращает словарь всех пользователей {str(uid): {...}} для обратной совместимости."""
    conn = get_storage_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY last_seen DESC").fetchall()
    return {str(r["user_id"]): dict(r) for r in rows}


def format_user_trigger(user_info: Dict[str, Any]) -> str:
    """Форматирует отображаемое имя пользователя с никнеймом или ID."""
    uid = user_info.get("user_id", "")
    fn = (user_info.get("first_name") or "").strip()
    ln = (user_info.get("last_name") or "").strip()
    un = (user_info.get("username") or "").strip()
    full = f"{fn} {ln}".strip() or f"ID {uid}"
    if un:
        return f"{html.escape(full)} (@{html.escape(un)})"
    return f"{html.escape(full)} (<code>{uid}</code>)"


def get_user_display(user_id: int) -> str:
    """Возвращает форматированную строку пользователя."""
    info = get_user(user_id)
    if info:
        return format_user_trigger(info)
    return f"ID <code>{user_id}</code>"


def get_user_short_slug(user_id: int) -> str:
    """Возвращает короткий безопасный слаг имени пользователя (до 16 символов)."""
    info = get_user(user_id) or {}
    raw = info.get("username") or info.get("first_name") or str(user_id)
    slug = re.sub(r"[^\w\-А-Яа-яЁё]+", "", str(raw)).strip()
    return slug[:16] or str(user_id)


def update_user_jobs_count(user_id: int, count: int) -> None:
    """Обновляет количество выполненных заданий пользователя."""
    conn = get_storage_conn()
    now = time.time()
    with _db_write_lock, conn:
        conn.execute(
            "UPDATE users SET jobs_count = ?, updated_at = ? WHERE user_id = ?",
            (int(count), now, int(user_id)),
        )


def delete_user_record(user_id: int) -> bool:
    """Удаляет запись пользователя из базы данных."""
    conn = get_storage_conn()
    with _db_write_lock, conn:
        cursor = conn.execute("DELETE FROM users WHERE user_id = ?", (int(user_id),))
        return cursor.rowcount > 0
