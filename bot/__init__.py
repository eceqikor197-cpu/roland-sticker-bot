"""Пакет логики и обработчиков Telegram-бота Roland VP-540."""

from __future__ import annotations

from typing import Any

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
from bot.handlers_media import (
    ConversionLimiter,
    handle_media,
    register_media_handlers,
)
from bot.jobs import (
    backfill_existing_jobs_and_users,
    cleanup_old_jobs,
    copy_job_to_hot_folder,
    delete_user_data,
    format_privacy_text,
    format_user_trigger,
    get_admin_state,
    get_user_display,
    get_user_prefs,
    get_user_short_slug,
    inspect_eps_stats,
    is_user_banned,
    list_all_jobs,
    load_all_prefs,
    load_json_file,
    notify_admins_about_job,
    process_and_send_job,
    record_user_activity,
    run_cleanup_loop,
    save_all_prefs,
    save_json_file,
    send_or_edit_admin_feed_item,
    start_cleanup_scheduler,
    toggle_ban_user,
    update_admin_state,
    update_user_prefs,
)
from bot.keyboards import (
    build_admin_home_view,
    build_admin_users_view,
    build_job_keyboard,
    is_admin,
)
from harvester.bot_integration import register_harvester_bot_handlers


def register_all_handlers(bot: Any) -> None:
    """Регистрирует все хендлеры Telegram-бота (админские, callback, медиа, harvester)."""
    register_admin_handlers(bot)
    register_callback_handlers(bot)
    register_media_handlers(bot)
    register_harvester_bot_handlers(bot, is_admin, is_user_banned)

__all__ = [
    "ConversionLimiter",
    "backfill_existing_jobs_and_users",
    "build_admin_home_view",
    "build_admin_users_view",
    "build_job_keyboard",
    "cleanup_old_jobs",
    "copy_job_to_hot_folder",
    "delete_user_data",
    "format_privacy_text",
    "format_user_trigger",
    "get_admin_state",
    "get_user_display",
    "get_user_prefs",
    "get_user_short_slug",
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
    "is_user_banned",
    "list_all_jobs",
    "load_all_prefs",
    "load_json_file",
    "notify_admins_about_job",
    "parse_size_text",
    "process_and_send_job",
    "record_user_activity",
    "register_admin_handlers",
    "register_all_handlers",
    "register_callback_handlers",
    "register_media_handlers",
    "run_cleanup_loop",
    "save_all_prefs",
    "save_json_file",
    "send_or_edit_admin_feed_item",
    "start_cleanup_scheduler",
    "toggle_ban_user",
    "update_admin_state",
    "update_user_prefs",
]
