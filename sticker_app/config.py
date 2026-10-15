"""
Конфигурация бэкенда веб-сервиса стикербомбинга (sticker_app).
Все параметры загружаются из переменных окружения (или .env).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent

# Загрузка .env при наличии
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

# Пути к базам данных и хранилищам
STORAGE_ROOT = Path(
    os.getenv("STORAGE_ROOT", BASE_DIR / "bot_workspace" / "harvester" / "staging")
).resolve()

HARVESTER_DB_PATH = Path(
    os.getenv("HARVESTER_DB_PATH", BASE_DIR / "bot_workspace" / "harvester" / "harvester.db")
).resolve()

APP_DB_PATH = Path(
    os.getenv("APP_DB_PATH", BASE_DIR / "bot_workspace" / "app.db")
).resolve()

CACHE_DIR = Path(
    os.getenv("CACHE_DIR", BASE_DIR / "bot_workspace" / "cache")
).resolve()

# Фильтр лицензий: по умолчанию off (разрешает все макеты без takedown)
# 'on' включает ограничение: только макеты с license IN ('own', 'licensed', 'cc0')
LICENSE_FILTER: bool = os.getenv("LICENSE_FILTER", "off").strip().lower() in (
    "1",
    "true",
    "on",
    "yes",
)

# API
API_PREFIX: str = "/api/v1"

# CORS origins (без '*')
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
CORS_ORIGINS: List[str] = [x.strip() for x in _cors_env.split(",") if x.strip()]


def get_harvester_db_ro_uri() -> str:
    """Возвращает безопасный sqlite3 URI для открытия harvester.db строго read-only."""
    return f"file:{HARVESTER_DB_PATH.as_posix()}?mode=ro"
