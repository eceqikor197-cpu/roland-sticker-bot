"""
Модуль управления базой данных сервиса app.db.
Обеспечивает подключение в режиме WAL и выполнение нумерованных SQL-миграций.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

import sticker_app.config as config

logger = logging.getLogger("sticker_app.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def get_app_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Открывает соединение с собственной базой данных app.db в режиме WAL.
    Создает родительский каталог базы при необходимости.
    """
    path = (db_path or config.APP_DB_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path.as_posix(), timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def run_migrations(
    db_path: Optional[Path] = None,
    migrations_dir: Optional[Path] = None,
) -> List[str]:
    """
    Применяет все непримененные SQL-миграции из каталога migrations.
    Возвращает список имен примененных миграций.
    """
    mig_dir = (migrations_dir or MIGRATIONS_DIR).resolve()
    if not mig_dir.exists():
        logger.warning("Каталог миграций не найден: %s", mig_dir)
        return []

    conn = get_app_db(db_path)
    applied: List[str] = []
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at REAL NOT NULL
                );
                """
            )
            existing_versions = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }

            migration_files = sorted(mig_dir.glob("*.sql"))
            for mig_file in migration_files:
                version = mig_file.name
                if version in existing_versions:
                    continue

                logger.info("Применение миграции: %s", version)
                sql_content = mig_file.read_text(encoding="utf-8")
                conn.executescript(sql_content)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, time.time()),
                )
                applied.append(version)

        return applied
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Инициализирует базу данных app.db и применяет все миграции."""
    run_migrations(db_path=db_path)
