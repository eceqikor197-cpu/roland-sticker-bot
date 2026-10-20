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

# Токен Telegram-бота
PLOTTER_BOT_TOKEN: str = os.getenv("PLOTTER_BOT_TOKEN", "").strip()

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
ADMIN_WEB_PASSWORD: str = os.getenv("ADMIN_WEB_PASSWORD", "")
ADMIN_WEB_COOKIE_SECURE: bool = os.getenv("ADMIN_WEB_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
ENABLE_PUBLIC_TUNNEL: bool = os.getenv("ENABLE_PUBLIC_TUNNEL", "false").lower() in ("true", "1", "yes")

# Сетевой хост/IP для подключения к Hot Folder по Samba
HOT_FOLDER_SMB_HOST: str = os.getenv("HOT_FOLDER_SMB_HOST", "127.0.0.1")

# Администраторы Telegram-бота
ADMIN_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# Разрешение обычным пользователям отправлять задания напрямую в Hot Folder без подтверждения
ALLOW_USER_HOT_FOLDER: bool = os.getenv("ALLOW_USER_HOT_FOLDER", "false").lower() in ("true", "1", "yes")

# Мини-приложение «Редактор наклейки» (Telegram Mini App, пакет miniapp/).
# MINIAPP_URL — публичный HTTPS-адрес сервера мини-апки (Tailscale Funnel или Cloudflare Tunnel).
# Пока он не задан, бот работает по-старому, через кнопки.
_miniapp_url = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
MINIAPP_URL: str = _miniapp_url if _miniapp_url.startswith("https://") else ""
MINIAPP_HOST: str = os.getenv("MINIAPP_HOST", "127.0.0.1")
MINIAPP_PORT: int = int(os.getenv("MINIAPP_PORT", "8096"))
# Ставить ли кнопку меню бота (слева от поля ввода), которая открывает список макетов
MINIAPP_MENU_BUTTON: bool = os.getenv("MINIAPP_MENU_BUTTON", "true").lower() in ("true", "1", "yes")

# Директории рабочих файлов и Hot Folder
WORK_DIR: Path = BASE_DIR / "bot_workspace"
JOBS_DIR: Path = WORK_DIR / "jobs"

_custom_hot = os.getenv("HOT_FOLDER_ROOT")
if _custom_hot:
    HOT_FOLDER_ROOT = Path(_custom_hot)
else:
    HOT_FOLDER_ROOT = WORK_DIR / "Roland_HotFolder"

HOT_QUEUE_EPS_DIR: Path = HOT_FOLDER_ROOT / "Queue_EPS"
HOT_PREVIEWS_DIR: Path = HOT_FOLDER_ROOT / "Previews"

WORK_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HOT_QUEUE_EPS_DIR.mkdir(parents=True, exist_ok=True)
HOT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)


def validate_config(raise_on_error: bool = False) -> list[str]:
    """Проверяет наличие обязательных настроек в конфигурации."""
    errors = []
    if not PLOTTER_BOT_TOKEN:
        errors.append("Переменная окружения PLOTTER_BOT_TOKEN не задана.")
    if not ADMIN_IDS:
        errors.append("Переменная окружения ADMIN_IDS не задана (список Telegram ID через запятую).")
    if not ADMIN_WEB_PASSWORD:
        errors.append("Переменная окружения ADMIN_WEB_PASSWORD не задана.")
    if errors and raise_on_error:
        raise ValueError("Ошибка конфигурации:\n" + "\n".join(f"- {e}" for e in errors))
    return errors
