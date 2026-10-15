"""Модуль центральной конфигурации roland-sticker-bot.

Содержит глобальные параметры сервиса, загружаемые из переменных окружения и .env.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Загрузка переменных окружения из .env при наличии
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

# Настройки приватности и хранения данных (Фаза 4)
# По умолчанию Live-шпион выключен ради приватности пользователей
LIVE_SPY_DEFAULT: bool = os.getenv("LIVE_SPY_DEFAULT", "false").lower() in ("true", "1", "yes")

# Время хранения заданий и файлов на сервере в часах (по умолчанию 168 часов = 7 дней)
JOB_RETENTION_HOURS: float = float(os.getenv("JOB_RETENTION_HOURS", "168.0"))
