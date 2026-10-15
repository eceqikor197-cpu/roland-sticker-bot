"""Базовое подключение и инициализация схемы SQLite (WAL mode) для storage/."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from config import STORAGE_DB_PATH

_thread_local = threading.local()
_db_write_lock = threading.RLock()

# Текущий путь к базе (может переопределяться в тестах через set_custom_db_path)
_custom_db_path: Optional[Path] = None


def set_custom_db_path(path: Optional[Path]) -> None:
    """Устанавливает пользовательский путь к БД (для изоляции в тестах)."""
    global _custom_db_path
    _custom_db_path = path
    # Сбрасываем кэш соединений текущего потока
    if hasattr(_thread_local, "conn"):
        try:
            _thread_local.conn.close()
        except Exception:
            pass
        delattr(_thread_local, "conn")


def get_db_path() -> Path:
    """Возвращает актуальный путь к файлу базы данных."""
    return _custom_db_path or STORAGE_DB_PATH


def get_storage_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Возвращает подключение SQLite для текущего потока (одно соединение на поток)."""
    target_path = Path(db_path) if db_path is not None else get_db_path()

    # Проверяем наличие активного соединения в текущем потоке
    conn = getattr(_thread_local, "conn", None)
    cached_path = getattr(_thread_local, "db_path", None)

    if conn is not None and cached_path == target_path:
        try:
            # Проверка живого состояния соединения
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            conn = None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target_path),
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    _thread_local.conn = conn
    _thread_local.db_path = target_path
    return conn


def init_storage(db_path: Optional[Path] = None) -> None:
    """Инициализирует структуру таблиц и индексов в SQLite БД."""
    target_path = Path(db_path) if db_path is not None else get_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with _db_write_lock:
        conn = get_storage_conn(target_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    jobs_count INTEGER NOT NULL DEFAULT 0,
                    last_action TEXT NOT NULL DEFAULT '',
                    last_job_id TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC);

                CREATE TABLE IF NOT EXISTS prefs (
                    user_id INTEGER PRIMARY KEY,
                    offset_mm REAL NOT NULL DEFAULT 2.5,
                    size_mm REAL NOT NULL DEFAULT 100.0,
                    keep_background INTEGER NOT NULL DEFAULT 0,
                    vectorize INTEGER NOT NULL DEFAULT 0,
                    perf_cut_mode TEXT NOT NULL DEFAULT 'none',
                    perf_cut_passes INTEGER NOT NULL DEFAULT 2,
                    nesting_qty INTEGER NOT NULL DEFAULT 1,
                    roll_width_mm REAL NOT NULL DEFAULT 1370.0,
                    stretch_x REAL NOT NULL DEFAULT 1.0,
                    stretch_y REAL NOT NULL DEFAULT 1.0,
                    fit_a4_sheet INTEGER NOT NULL DEFAULT 0,
                    menu_tab TEXT NOT NULL DEFAULT 'main',
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    orig_stem TEXT NOT NULL DEFAULT '',
                    input_filename TEXT NOT NULL DEFAULT '',
                    eps_filename TEXT NOT NULL DEFAULT '',
                    offset_mm REAL NOT NULL DEFAULT 2.5,
                    size_mm REAL NOT NULL DEFAULT 100.0,
                    keep_background INTEGER NOT NULL DEFAULT 0,
                    vectorize INTEGER NOT NULL DEFAULT 0,
                    perf_cut_mode TEXT NOT NULL DEFAULT 'none',
                    perf_cut_passes INTEGER NOT NULL DEFAULT 2,
                    nesting_qty INTEGER NOT NULL DEFAULT 1,
                    roll_width_mm REAL NOT NULL DEFAULT 1370.0,
                    stretch_x REAL NOT NULL DEFAULT 1.0,
                    stretch_y REAL NOT NULL DEFAULT 1.0,
                    fit_a4_sheet INTEGER NOT NULL DEFAULT 0,
                    menu_tab TEXT NOT NULL DEFAULT 'main',
                    num_contours INTEGER NOT NULL DEFAULT 1,
                    perf_contours INTEGER NOT NULL DEFAULT 0,
                    width_mm REAL NOT NULL DEFAULT 100.0,
                    height_mm REAL NOT NULL DEFAULT 100.0,
                    in_hot_folder INTEGER NOT NULL DEFAULT 0,
                    hot_filename TEXT NOT NULL DEFAULT '',
                    hot_folder_request_status TEXT NOT NULL DEFAULT '',
                    hot_folder_requested_at REAL NOT NULL DEFAULT 0.0,
                    pricing_json TEXT NOT NULL DEFAULT '{}',
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at DESC);

                CREATE TABLE IF NOT EXISTS admin_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
