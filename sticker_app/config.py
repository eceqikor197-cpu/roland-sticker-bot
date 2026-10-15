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
_demo_harv = BASE_DIR / "bot_workspace" / "demo_catalog" / "harvester.db"
_default_harv = BASE_DIR / "bot_workspace" / "harvester" / "harvester.db"
_default_staging = BASE_DIR / "bot_workspace" / "harvester" / "staging"

_use_demo = False
if "HARVESTER_DB_PATH" not in os.environ and _demo_harv.exists():
    try:
        has_staging_files = any(_default_staging.iterdir()) if _default_staging.exists() else False
    except Exception:
        has_staging_files = False
    if not has_staging_files:
        _use_demo = True

if _use_demo:
    _default_storage = BASE_DIR / "bot_workspace" / "demo_catalog" / "staging"
    _default_harvester_db = BASE_DIR / "bot_workspace" / "demo_catalog" / "harvester.db"
    _default_app_db = BASE_DIR / "bot_workspace" / "demo_catalog" / "app.db"
else:
    _default_storage = BASE_DIR / "bot_workspace" / "harvester" / "staging"
    _default_harvester_db = BASE_DIR / "bot_workspace" / "harvester" / "harvester.db"
    _default_app_db = BASE_DIR / "bot_workspace" / "app.db"

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", _default_storage)).resolve()
HARVESTER_DB_PATH = Path(os.getenv("HARVESTER_DB_PATH", _default_harvester_db)).resolve()
APP_DB_PATH = Path(os.getenv("APP_DB_PATH", _default_app_db)).resolve()

CACHE_DIR = Path(
    os.getenv("CACHE_DIR", BASE_DIR / "bot_workspace" / "cache")
).resolve()

OBJECTS_DIR = Path(
    os.getenv("OBJECTS_DIR", BASE_DIR / "objects")
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
