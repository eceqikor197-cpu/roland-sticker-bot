#!/usr/bin/env python3
r"""
Telegram-бот на сервере crybaby для конвертации JPEG/PNG стикеров и стикерпаков
в печатный формат .eps для плоттера-каттера Roland VP-540 (Roland VersaWorks).

Возможности:
1. 100% сохранение качества оригинала (Lossless Print & Cut) + векторный контур CutContour (100% Magenta).
2. Двойной контур Roland VersaWorks: CutContour (надсечка) + PerfCutContour (сквозная рамка стикерпака или вырубка Die-Cut).
3. Выбор точного размера (5см, 7см, 10см, 15см, А5, А4 или свой размер в мм/см), авто-раскладка тиража на рулон (Nesting 1/10/25/50/100 шт) и калькулятор расхода плёнки/себестоимости/цены.
4. Отправка готового .EPS в сетевую папку Hot Folder (\\192.168.0.108\Roland_HotFolder\Queue_EPS) в 1 клик.
5. Встроенная Админ-панель (/admin) + Live-шпион + Веб-админка (http://192.168.0.108:8090).
"""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("telegram_bot")

import telebot
from telebot.types import (
    BotCommand,
)


from jpeg2eps import (
    convert_image_to_plotter_eps,
)
from harvester.bot_integration import (
    register_harvester_bot_handlers,
)
from storage import (
    format_user_trigger as storage_format_user_trigger,
    get_admin_state as storage_get_admin_state,
    get_user_display as storage_get_user_display,
    get_user_prefs as storage_get_user_prefs,
    get_user_short_slug as storage_get_user_short_slug,
    init_storage,
    is_user_banned as storage_is_user_banned,
    load_all_prefs as storage_load_all_prefs,
    record_user_activity as storage_record_user_activity,
    toggle_ban_user as storage_toggle_ban_user,
    update_admin_state as storage_update_admin_state,
    update_user_prefs as storage_update_user_prefs,
)

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "bot_workspace"
JOBS_DIR = WORK_DIR / "jobs"
PREFS_FILE = WORK_DIR / "user_prefs.json"
USERS_FILE = WORK_DIR / "users_db.json"
ADMIN_STATE_FILE = WORK_DIR / "admin_state.json"

HOT_FOLDER_ROOT = Path("/home/crybaby/Roland_HotFolder")
if not HOT_FOLDER_ROOT.parent.exists():
    HOT_FOLDER_ROOT = WORK_DIR / "Roland_HotFolder"
HOT_QUEUE_EPS_DIR = HOT_FOLDER_ROOT / "Queue_EPS"
HOT_PREVIEWS_DIR = HOT_FOLDER_ROOT / "Previews"

WORK_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HOT_QUEUE_EPS_DIR.mkdir(parents=True, exist_ok=True)
HOT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

from config import (
    JOB_RETENTION_HOURS,
    LIVE_SPY_DEFAULT,
    PUBLIC_CATALOG,
    STORAGE_DB_PATH,
)
from harvester.config import BOT_TOKEN

init_storage(STORAGE_DB_PATH)

ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "870592714").split(",") if x.strip().isdigit()}


def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


ALLOW_USER_HOT_FOLDER = os.getenv("ALLOW_USER_HOT_FOLDER", "false").lower() in ("true", "1", "yes")
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "15"))
MAX_SIZE_MM = float(os.getenv("MAX_SIZE_MM", "1370.0"))
MIN_SIZE_MM = 5.0
MIN_STRETCH = 0.1
MAX_STRETCH = 10.0
MAX_CONVERSIONS_PER_MINUTE = int(os.getenv("MAX_CONVERSIONS_PER_MINUTE", "6"))
MAX_CONCURRENT_CONVERSIONS = int(os.getenv("MAX_CONCURRENT_CONVERSIONS", "2"))

from bot.handlers_media import (
    ConversionLimiter,
    handle_media,
    register_media_handlers,
)

conversion_limiter = ConversionLimiter(
    max_per_minute=MAX_CONVERSIONS_PER_MINUTE,
    max_concurrent_global=MAX_CONCURRENT_CONVERSIONS,
)

from config import (
    ADMIN_WEB_COOKIE_SECURE,
    ADMIN_WEB_PASSWORD,
    ENABLE_PUBLIC_TUNNEL,
    WEB_HOST,
    WEB_PORT,
)
from web import (
    AdminWebHandler,
    LoginRateLimiter,
    PERF_MODE_LABELS,
    WebSessionStore,
    get_or_create_admin_web_key,
    get_public_web_url,
    login_rate_limiter,
    start_cloudflared_tunnel_supervisor,
    start_web_admin_server,
    web_session_store,
)

from bot.jobs import (
    backfill_existing_jobs_and_users,
    cleanup_old_jobs,
    copy_job_to_hot_folder,
    delete_user_data,
    format_privacy_text,
    inspect_eps_stats,
    list_all_jobs,
    load_json_file,
    notify_admins_about_job,
    process_and_send_job,
    run_cleanup_loop,
    save_json_file,
    send_or_edit_admin_feed_item,
    start_cleanup_scheduler,
)
from bot.handlers_admin import (
    handle_admin_callback,
    handle_admin_command,
    handle_custom_size_text,
    handle_delete_my_data,
    handle_privacy,
    handle_spy_command,
    handle_start,
    parse_size_text,
    register_admin_handlers,
)
from bot.handlers_callbacks import (
    handle_hot_folder_request_callback,
    handle_job_callback,
    handle_user_callback,
    register_callback_handlers,
)
from bot.keyboards import (
    build_admin_home_view,
    build_admin_users_view,
    build_job_keyboard,
)

__all__ = [
    "ADMIN_IDS",
    "ADMIN_WEB_COOKIE_SECURE",
    "ADMIN_WEB_PASSWORD",
    "AdminWebHandler",
    "ConversionLimiter",
    "conversion_limiter",
    "JOB_RETENTION_HOURS",
    "LIVE_SPY_DEFAULT",
    "LoginRateLimiter",
    "MAX_CONCURRENT_CONVERSIONS",
    "MAX_CONVERSIONS_PER_MINUTE",
    "MAX_SIZE_MM",
    "MAX_STRETCH",
    "MAX_UPLOAD_MB",
    "MIN_SIZE_MM",
    "MIN_STRETCH",
    "PERF_MODE_LABELS",
    "PUBLIC_CATALOG",
    "WEB_HOST",
    "WEB_PORT",
    "WebSessionStore",
    "backfill_existing_jobs_and_users",
    "bot",
    "build_admin_home_view",
    "build_admin_users_view",
    "build_job_keyboard",
    "cleanup_old_jobs",
    "convert_image_to_plotter_eps",
    "copy_job_to_hot_folder",
    "delete_user_data",
    "format_privacy_text",
    "get_admin_state",
    "get_or_create_admin_web_key",
    "get_public_web_url",
    "handle_admin_callback",
    "handle_admin_command",
    "handle_custom_size_text",
    "handle_delete_my_data",
    "handle_hot_folder_request_callback",
    "handle_job_callback",
    "handle_media",
    "handle_privacy",
    "handle_spy_command",
    "handle_start",
    "handle_user_callback",
    "inspect_eps_stats",
    "is_admin",
    "list_all_jobs",
    "load_json_file",
    "login_rate_limiter",
    "notify_admins_about_job",
    "parse_size_text",
    "process_and_send_job",
    "register_admin_handlers",
    "register_callback_handlers",
    "register_media_handlers",
    "run_cleanup_loop",
    "save_json_file",
    "send_or_edit_admin_feed_item",
    "start_cleanup_scheduler",
    "start_cloudflared_tunnel_supervisor",
    "start_web_admin_server",
    "web_session_store",
]

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
db_lock = threading.RLock()
register_admin_handlers(bot)
register_callback_handlers(bot)
register_media_handlers(bot)


def get_admin_state() -> Dict[str, Any]:
    return storage_get_admin_state()


def update_admin_state(**kwargs: Any) -> Dict[str, Any]:
    state = storage_update_admin_state(**kwargs)
    try:
        with db_lock:
            save_json_file(ADMIN_STATE_FILE, state)
    except Exception:
        pass
    return state


def is_user_banned(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    return storage_is_user_banned(user_id, admin_ids=list(ADMIN_IDS))


def toggle_ban_user(target_uid: int) -> bool:
    now_banned = storage_toggle_ban_user(target_uid)
    try:
        with db_lock:
            save_json_file(ADMIN_STATE_FILE, storage_get_admin_state())
    except Exception:
        pass
    return now_banned


def record_user_activity(
    user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    action: str = "",
    last_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = storage_record_user_activity(
        user_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        action=action,
        last_job_id=last_job_id,
    )
    try:
        with db_lock:
            users = load_json_file(USERS_FILE, {})
            users[str(user_id)] = entry
            save_json_file(USERS_FILE, users)
    except Exception:
        pass
    return entry


def format_user_trigger(user_info: Dict[str, Any]) -> str:
    return storage_format_user_trigger(user_info)


def get_user_display(user_id: int) -> str:
    return storage_get_user_display(user_id)


def get_user_short_slug(user_id: int) -> str:
    return storage_get_user_short_slug(user_id)


def load_all_prefs() -> Dict[str, Any]:
    return storage_load_all_prefs()


def save_all_prefs(data: Dict[str, Any]) -> None:
    for uid_str, p in data.items():
        if uid_str.isdigit() and isinstance(p, dict):
            storage_update_user_prefs(int(uid_str), **p)
    try:
        with db_lock:
            save_json_file(PREFS_FILE, data)
    except Exception:
        pass


def get_user_prefs(user_id: int) -> Dict[str, Any]:
    return storage_get_user_prefs(user_id)


def update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    prefs = storage_update_user_prefs(user_id, **kwargs)
    try:
        with db_lock:
            all_prefs = load_json_file(PREFS_FILE, {})
            all_prefs[str(user_id)] = prefs
            save_json_file(PREFS_FILE, all_prefs)
    except Exception:
        pass
    return prefs



register_harvester_bot_handlers(bot, is_admin, is_user_banned)


if __name__ == "__main__":
    print("🔄 Синхронизация истории работ и пользователей...")
    try:
        get_or_create_admin_web_key()
        backfill_existing_jobs_and_users()
        bot.set_my_commands(
            [
                BotCommand("start", "Инструкция, размеры и текущие настройки"),
                BotCommand("privacy", "🔒 Конфиденциальность и хранение данных"),
                BotCommand("delete_my_data", "🗑 Удалить все мои макеты и данные"),
                BotCommand("harvester", "🚜 Автономный парсер базы 24/7 (Google Drive 5 ТБ)"),
                BotCommand("parse", "🔥 Заказать срочный сбор стикерпака по теме"),
                BotCommand("upload_asset", "📤 Загрузить авторский макет в базу с лицензией (Админ)"),
                BotCommand("admin", "👑 Админ-панель (чужие работы, Hot Folder, статистика)"),
                BotCommand("spy", "🔔 Включение/выключение Live-шпиона (Админ)"),
                BotCommand("help", "Как отправлять макеты и собирать тиражи на рулон"),
            ]
        )
    except Exception as exc:
        print(f"⚠️ Предупреждение при инициализации: {exc}")

    start_cleanup_scheduler(interval_seconds=3600.0)

    web_thread = threading.Thread(target=start_web_admin_server, daemon=True)
    web_thread.start()

    if ENABLE_PUBLIC_TUNNEL:
        tunnel_thread = threading.Thread(target=start_cloudflared_tunnel_supervisor, daemon=True)
        tunnel_thread.start()
    else:
        logger.info(
            "Публичный туннель выключен (ENABLE_PUBLIC_TUNNEL=false). "
            "Для безопасного удалённого доступа используйте Tailscale или Cloudflare Access."
        )

    print("🚀 Запуск Plotter EPS Telegram Bot (Roland VP-540 Edition)...")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
