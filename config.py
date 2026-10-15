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

# Настройки безопасности парсера Sticker Harvester (Фаза 5)
# По умолчанию сборщик отключен (false) во избежание рисков нарушения авторских прав и условий площадок
HARVESTER_ENABLED: bool = os.getenv("HARVESTER_ENABLED", "false").lower() in ("true", "1", "yes")

# По умолчанию публичный веб-каталог закрыт (false): /harvester и /harvester_file/* требуют авторизации админа
PUBLIC_CATALOG: bool = os.getenv("PUBLIC_CATALOG", "false").lower() in ("true", "1", "yes")

# Путь к базе данных SQLite для пользователей, настроек и заданий (Фаза 6)
STORAGE_DB_PATH: Path = Path(os.getenv("STORAGE_DB_PATH", str(BASE_DIR / "bot_storage.db")))

# Настройки веб-админки и туннелей
WEB_HOST: str = os.getenv("ADMIN_WEB_HOST", "127.0.0.1")
WEB_PORT: int = int(os.getenv("ADMIN_WEB_PORT", "8090"))
ADMIN_WEB_PASSWORD: str = os.getenv("ADMIN_WEB_PASSWORD", "roland")
ADMIN_WEB_COOKIE_SECURE: bool = os.getenv("ADMIN_WEB_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
ENABLE_PUBLIC_TUNNEL: bool = os.getenv("ENABLE_PUBLIC_TUNNEL", "false").lower() in ("true", "1", "yes")

# Администраторы Telegram-бота
ADMIN_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "870592714").split(",")
    if x.strip().isdigit()
}

# Разрешение обычным пользователям отправлять задания напрямую в Hot Folder без подтверждения
ALLOW_USER_HOT_FOLDER: bool = os.getenv("ALLOW_USER_HOT_FOLDER", "false").lower() in ("true", "1", "yes")

# Директории рабочих файлов и Hot Folder
WORK_DIR: Path = BASE_DIR / "bot_workspace"
JOBS_DIR: Path = WORK_DIR / "jobs"
HOT_FOLDER_ROOT: Path = Path("/home/crybaby/Roland_HotFolder")
if not HOT_FOLDER_ROOT.parent.exists():
    HOT_FOLDER_ROOT = WORK_DIR / "Roland_HotFolder"
HOT_QUEUE_EPS_DIR: Path = HOT_FOLDER_ROOT / "Queue_EPS"
HOT_PREVIEWS_DIR: Path = HOT_FOLDER_ROOT / "Previews"

WORK_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HOT_QUEUE_EPS_DIR.mkdir(parents=True, exist_ok=True)
HOT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)


