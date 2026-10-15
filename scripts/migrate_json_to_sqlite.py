#!/usr/bin/env python3
"""Одноразовый скрипт миграции данных из JSON-файлов в SQLite (Фаза 6).

Читает старые JSON-файлы:
- users_db.json
- user_prefs.json
- admin_state.json
- jobs/*/meta.json

И переносит их в SQLite базу данных (WAL mode).
Ничего не удаляет с диска. Поддерживает режим --dry-run.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Добавляем корень проекта в sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import BASE_DIR, STORAGE_DB_PATH
from storage.database import init_storage, set_custom_db_path
from storage.jobs import save_job
from storage.prefs import update_user_prefs
from storage.users import record_user_activity, update_user_jobs_count
from storage.admin import update_admin_state

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate_json_to_sqlite")


def _read_json_file(file_path: Path) -> Any:
    """Безопасно читает JSON-файл."""
    if not file_path.exists() or not file_path.is_file():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Ошибка при чтении %s: %s", file_path, exc)
        return None


def find_json_source_files(base_dir: Path) -> Tuple[Optional[Path], Optional[Path], Optional[Path], Path]:
    """Ищет файлы users_db.json, user_prefs.json, admin_state.json и директорию jobs."""
    # Проверяем возможные места расположения: bot_workspace, jobs, base_dir
    possible_job_dirs = [
        base_dir / "bot_workspace" / "jobs",
        base_dir / "jobs",
        base_dir / "bot_workspace",
        base_dir,
    ]
    jobs_dir = base_dir
    for jd in possible_job_dirs:
        if jd.exists() and jd.is_dir():
            jobs_dir = jd
            break

    search_dirs = [
        base_dir / "bot_workspace",
        base_dir / "jobs",
        jobs_dir,
        base_dir,
    ]

    def find_one(name: str) -> Optional[Path]:
        for d in search_dirs:
            candidate = d / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    users_file = find_one("users_db.json")
    prefs_file = find_one("user_prefs.json")
    admin_file = find_one("admin_state.json")
    return users_file, prefs_file, admin_file, jobs_dir


def migrate(
    source_dir: Path,
    db_path: Path,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Выполняет миграцию данных из JSON в SQLite."""
    users_file, prefs_file, admin_file, jobs_dir = find_json_source_files(source_dir)

    stats = {
        "users": 0,
        "prefs": 0,
        "admin_keys": 0,
        "jobs": 0,
        "errors": 0,
    }

    logger.info("Источник JSON: %s", source_dir)
    logger.info("Целевая БД SQLite: %s", db_path)
    if dry_run:
        logger.info("⚡ Включён режим DRY-RUN: запись в базу данных выполняться не будет.")
    else:
        set_custom_db_path(db_path)
        init_storage(db_path)

    # 1. Миграция пользователей (users_db.json)
    if users_file:
        logger.info("Чтение пользователей из %s...", users_file)
        data = _read_json_file(users_file)
        if isinstance(data, dict):
            for k, u in data.items():
                if not isinstance(u, dict):
                    continue
                try:
                    uid = int(u.get("user_id") or k)
                    if not dry_run:
                        record_user_activity(
                            user_id=uid,
                            first_name=u.get("first_name"),
                            last_name=u.get("last_name"),
                            username=u.get("username"),
                            action=u.get("last_action", ""),
                            last_job_id=u.get("last_job_id"),
                        )
                        # Восстанавливаем first_seen и jobs_count из исходных данных
                        if "jobs_count" in u:
                            update_user_jobs_count(uid, int(u["jobs_count"]))
                        if "first_seen" in u:
                            from storage.database import get_storage_conn
                            conn = get_storage_conn()
                            with conn:
                                conn.execute(
                                    "UPDATE users SET first_seen = ? WHERE user_id = ?",
                                    (float(u["first_seen"]), uid),
                                )
                    stats["users"] += 1
                except Exception as exc:
                    logger.warning("Ошибка миграции пользователя %s: %s", k, exc)
                    stats["errors"] += 1
    else:
        logger.info("users_db.json не найден — пропуск.")

    # 2. Миграция настроек (user_prefs.json)
    if prefs_file:
        logger.info("Чтение настроек из %s...", prefs_file)
        data = _read_json_file(prefs_file)
        if isinstance(data, dict):
            for k, p in data.items():
                if not isinstance(p, dict) or not str(k).isdigit():
                    continue
                try:
                    uid = int(k)
                    if not dry_run:
                        update_user_prefs(uid, **p)
                    stats["prefs"] += 1
                except Exception as exc:
                    logger.warning("Ошибка миграции настроек пользователя %s: %s", k, exc)
                    stats["errors"] += 1
    else:
        logger.info("user_prefs.json не найден — пропуск.")

    # 3. Миграция состояния админки (admin_state.json)
    if admin_file:
        logger.info("Чтение настроек админки из %s...", admin_file)
        data = _read_json_file(admin_file)
        if isinstance(data, dict):
            for k, v in data.items():
                try:
                    if not dry_run:
                        update_admin_state(**{k: v})
                    stats["admin_keys"] += 1
                except Exception as exc:
                    logger.warning("Ошибка миграции admin_state %s: %s", k, exc)
                    stats["errors"] += 1
    else:
        logger.info("admin_state.json не найден — пропуск.")

    # 4. Миграция заданий (jobs/*/meta.json)
    if jobs_dir.exists():
        logger.info("Поиск заданий в %s...", jobs_dir)
        meta_files: List[Path] = []
        for child in jobs_dir.iterdir():
            if child.is_dir():
                mf = child / "meta.json"
                if mf.exists() and mf.is_file():
                    meta_files.append(mf)

        logger.info("Найдено файлов meta.json: %d шт.", len(meta_files))
        for mf in meta_files:
            meta = _read_json_file(mf)
            if not isinstance(meta, dict) or "job_id" not in meta or "user_id" not in meta:
                continue
            try:
                if not dry_run:
                    save_job(meta)
                stats["jobs"] += 1
            except Exception as exc:
                logger.warning("Ошибка миграции задания из %s: %s", mf, exc)
                stats["errors"] += 1
    else:
        logger.info("Каталог заданий %s не найден — пропуск.", jobs_dir)

    logger.info("========================================")
    logger.info("Результаты миграции:")
    logger.info("  Пользователей: %d", stats["users"])
    logger.info("  Настроек:      %d", stats["prefs"])
    logger.info("  Ключей админа: %d", stats["admin_keys"])
    logger.info("  Заданий:       %d", stats["jobs"])
    logger.info("  Ошибок:        %d", stats["errors"])
    logger.info("========================================")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция данных roland-sticker-bot из JSON в SQLite.")
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=BASE_DIR,
        help="Путь к исходной директории с JSON-файлами (по умолчанию корень проекта).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=STORAGE_DB_PATH,
        help="Путь к целевой базе SQLite (по умолчанию STORAGE_DB_PATH из config.py).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверить и подсчитать переносимые записи без изменения базы данных.",
    )
    args = parser.parse_args()
    migrate(source_dir=args.json_dir, db_path=args.db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
