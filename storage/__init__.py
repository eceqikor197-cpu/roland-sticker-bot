"""Пакет storage — SQLite хранилище (WAL mode) для roland-sticker-bot."""

from __future__ import annotations

from storage.admin import (
    get_admin_state,
    is_user_banned,
    toggle_ban_user,
    update_admin_state,
)
from storage.database import (
    get_db_path,
    get_storage_conn,
    init_storage,
    set_custom_db_path,
)
from storage.jobs import (
    count_user_jobs,
    delete_job,
    delete_user_jobs,
    get_job,
    list_all_jobs,
    save_job,
    update_job,
)
from storage.prefs import (
    DEFAULT_PREFS,
    delete_user_prefs,
    get_user_prefs,
    load_all_prefs,
    update_user_prefs,
)
from storage.users import (
    delete_user_record,
    format_user_trigger,
    get_all_users,
    get_user,
    get_user_display,
    get_user_short_slug,
    record_user_activity,
    update_user_jobs_count,
)

__all__ = [
    "DEFAULT_PREFS",
    "count_user_jobs",
    "delete_job",
    "delete_user_jobs",
    "delete_user_prefs",
    "delete_user_record",
    "format_user_trigger",
    "get_admin_state",
    "get_all_users",
    "get_db_path",
    "get_job",
    "get_storage_conn",
    "get_user",
    "get_user_display",
    "get_user_prefs",
    "get_user_short_slug",
    "init_storage",
    "is_user_banned",
    "list_all_jobs",
    "load_all_prefs",
    "record_user_activity",
    "save_job",
    "set_custom_db_path",
    "toggle_ban_user",
    "update_admin_state",
    "update_job",
    "update_user_jobs_count",
    "update_user_prefs",
]
