#!/usr/bin/env python3
"""Точка входа Telegram-бота Roland VP-540."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import telebot
from telebot.types import BotCommand

from bot import (
    ConversionLimiter, backfill_existing_jobs_and_users, build_admin_home_view,
    build_admin_users_view, build_job_keyboard, cleanup_old_jobs, copy_job_to_hot_folder,
    delete_user_data, format_privacy_text, format_user_trigger, get_admin_state,
    get_user_display, get_user_prefs, get_user_short_slug, handle_admin_callback,
    handle_admin_command, handle_custom_size_text, handle_delete_my_data,
    handle_hot_folder_request_callback, handle_job_callback, handle_media,
    handle_privacy, handle_spy_command, handle_start, handle_user_callback,
    inspect_eps_stats, is_admin, is_user_banned, list_all_jobs, load_all_prefs,
    load_json_file, notify_admins_about_job, parse_size_text, process_and_send_job,
    record_user_activity, register_admin_handlers, register_all_handlers,
    register_callback_handlers, register_media_handlers, run_cleanup_loop,
    save_all_prefs, save_json_file, send_or_edit_admin_feed_item,
    start_cleanup_scheduler, toggle_ban_user, update_admin_state, update_user_prefs,
)
from config import (
    ADMIN_IDS, ADMIN_WEB_COOKIE_SECURE, ADMIN_WEB_PASSWORD, ALLOW_USER_HOT_FOLDER,
    ENABLE_PUBLIC_TUNNEL, HOT_FOLDER_ROOT, HOT_PREVIEWS_DIR, HOT_QUEUE_EPS_DIR,
    JOB_RETENTION_HOURS, JOBS_DIR, LIVE_SPY_DEFAULT, MAX_SIZE_MM, MAX_STRETCH, MIN_SIZE_MM,
    MIN_STRETCH, PUBLIC_CATALOG, STORAGE_DB_PATH, WEB_HOST, WEB_PORT, WORK_DIR, validate_config,
)
from harvester.config import BOT_TOKEN
from jpeg2eps import convert_image_to_plotter_eps
from storage import init_storage
from web import (
    AdminWebHandler, LoginRateLimiter, PERF_MODE_LABELS, WebSessionStore,
    get_or_create_admin_web_key, get_public_web_url, login_rate_limiter,
    start_cloudflared_tunnel_supervisor, start_web_admin_server, web_session_store,
)

logger = logging.getLogger("telegram_bot")
init_storage(STORAGE_DB_PATH)

PREFS_FILE: Path = WORK_DIR / "user_prefs.json"
USERS_FILE: Path = WORK_DIR / "users_db.json"
ADMIN_STATE_FILE: Path = WORK_DIR / "admin_state.json"
db_lock = threading.RLock()

MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "15"))
MAX_CONVERSIONS_PER_MINUTE = int(os.getenv("MAX_CONVERSIONS_PER_MINUTE", "6"))
MAX_CONCURRENT_CONVERSIONS = int(os.getenv("MAX_CONCURRENT_CONVERSIONS", "2"))

conversion_limiter = ConversionLimiter(
    max_per_minute=MAX_CONVERSIONS_PER_MINUTE,
    max_concurrent_global=MAX_CONCURRENT_CONVERSIONS,
)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
register_all_handlers(bot)

__all__ = [
    "ADMIN_IDS", "ADMIN_STATE_FILE", "ADMIN_WEB_COOKIE_SECURE", "ADMIN_WEB_PASSWORD",
    "ALLOW_USER_HOT_FOLDER", "AdminWebHandler", "ConversionLimiter", "HOT_FOLDER_ROOT",
    "HOT_PREVIEWS_DIR", "HOT_QUEUE_EPS_DIR", "JOBS_DIR", "JOB_RETENTION_HOURS",
    "LIVE_SPY_DEFAULT", "LoginRateLimiter", "MAX_CONCURRENT_CONVERSIONS",
    "MAX_CONVERSIONS_PER_MINUTE", "MAX_SIZE_MM", "MAX_STRETCH", "MAX_UPLOAD_MB",
    "MIN_SIZE_MM", "MIN_STRETCH", "PERF_MODE_LABELS", "PREFS_FILE", "PUBLIC_CATALOG",
    "USERS_FILE", "WEB_HOST", "WEB_PORT", "WORK_DIR", "WebSessionStore",
    "backfill_existing_jobs_and_users", "bot", "build_admin_home_view",
    "build_admin_users_view", "build_job_keyboard", "cleanup_old_jobs",
    "conversion_limiter", "convert_image_to_plotter_eps", "copy_job_to_hot_folder",
    "db_lock", "delete_user_data", "format_privacy_text", "format_user_trigger",
    "get_admin_state", "get_or_create_admin_web_key", "get_public_web_url",
    "get_user_display", "get_user_prefs", "get_user_short_slug", "handle_admin_callback",
    "handle_admin_command", "handle_custom_size_text", "handle_delete_my_data",
    "handle_hot_folder_request_callback", "handle_job_callback", "handle_media",
    "handle_privacy", "handle_spy_command", "handle_start", "handle_user_callback",
    "inspect_eps_stats", "is_admin", "is_user_banned", "list_all_jobs", "load_all_prefs",
    "load_json_file", "login_rate_limiter", "notify_admins_about_job", "parse_size_text",
    "process_and_send_job", "record_user_activity", "register_admin_handlers",
    "register_all_handlers", "register_callback_handlers", "register_media_handlers",
    "run_cleanup_loop", "save_all_prefs", "save_json_file", "send_or_edit_admin_feed_item",
    "start_cleanup_scheduler", "start_cloudflared_tunnel_supervisor",
    "start_web_admin_server", "toggle_ban_user", "update_admin_state",
    "update_user_prefs", "web_session_store",
]


def main() -> None:
    cfg_errors = validate_config()
    if cfg_errors:
        for err in cfg_errors:
            logger.warning("Конфигурация: %s", err)

    print("🔄 Синхронизация истории работ и пользователей...")
    try:
        get_or_create_admin_web_key()
        backfill_existing_jobs_and_users()
        bot.set_my_commands([
            BotCommand("start", "Инструкция, размеры и текущие настройки"),
            BotCommand("privacy", "🔒 Конфиденциальность и хранение данных"),
            BotCommand("delete_my_data", "🗑 Удалить все мои макеты и данные"),
            BotCommand("harvester", "🚜 Автономный парсер базы 24/7 (Google Drive 5 ТБ)"),
            BotCommand("parse", "🔥 Заказать срочный сбор стикерпака по теме"),
            BotCommand("upload_asset", "📤 Загрузить авторский макет в базу с лицензией (Админ)"),
            BotCommand("admin", "👑 Админ-панель (чужие работы, Hot Folder, статистика)"),
            BotCommand("spy", "🔔 Включение/выключение Live-шпиона (Админ)"),
            BotCommand("help", "Как отправлять макеты и собирать тиражи на рулон"),
        ])
    except Exception as exc:
        print(f"⚠️ Предупреждение при инициализации: {exc}")

    start_cleanup_scheduler(interval_seconds=3600.0)
    threading.Thread(target=start_web_admin_server, daemon=True).start()

    if ENABLE_PUBLIC_TUNNEL:
        threading.Thread(target=start_cloudflared_tunnel_supervisor, daemon=True).start()
    else:
        logger.info(
            "Публичный туннель выключен (ENABLE_PUBLIC_TUNNEL=false). "
            "Для безопасного удалённого доступа используйте Tailscale или Cloudflare Access."
        )

    print("🚀 Запуск Plotter EPS Telegram Bot (Roland VP-540 Edition)...")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    main()
