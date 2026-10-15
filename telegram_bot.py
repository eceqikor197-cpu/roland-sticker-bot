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

import contextlib
import html
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telegram_bot")

import telebot
from telebot.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from PIL.Image import DecompressionBombError

from jpeg2eps import (
    SUPPORTED_EXTENSIONS,
    convert_image_to_plotter_eps,
)
from harvester.bot_integration import (
    check_and_consume_waiting_upload,
    register_harvester_bot_handlers,
    register_manual_asset,
)
from harvester.database import (
    LICENSE_LABELS,
)
from storage import (
    format_user_trigger as storage_format_user_trigger,
    get_admin_state as storage_get_admin_state,
    get_job as storage_get_job,
    get_user_display as storage_get_user_display,
    get_user_prefs as storage_get_user_prefs,
    get_user_short_slug as storage_get_user_short_slug,
    init_storage,
    is_user_banned as storage_is_user_banned,
    load_all_prefs as storage_load_all_prefs,
    record_user_activity as storage_record_user_activity,
    save_job as storage_save_job,
    toggle_ban_user as storage_toggle_ban_user,
    update_admin_state as storage_update_admin_state,
    update_job as storage_update_job,
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


class ConversionLimiter:
    """Ограничивает частоту (<= 6/мин) и параллельность (1 на пользователя, N глобально)."""

    def __init__(
        self,
        max_per_minute: int = 6,
        max_concurrent_global: int = 2,
    ):
        self.max_per_minute = max_per_minute
        self.max_concurrent_global = max_concurrent_global
        self._history: Dict[int, List[float]] = {}
        self._active_users: set[int] = set()
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrent_global)

    def check_and_acquire_user_slot(self, user_id: int) -> Tuple[bool, str]:
        if is_admin(user_id):
            return True, "ok"
        now = time.time()
        with self._lock:
            if user_id in self._active_users:
                return False, "concurrent"
            user_times = [t for t in self._history.get(user_id, []) if now - t < 60.0]
            self._history[user_id] = user_times
            if len(user_times) >= self.max_per_minute:
                return False, "rate_limit"
            self._active_users.add(user_id)
            return True, "ok"

    def release_user_slot(self, user_id: int, record_completed: bool = True) -> None:
        if is_admin(user_id):
            return
        now = time.time()
        with self._lock:
            self._active_users.discard(user_id)
            if record_completed:
                if user_id not in self._history:
                    self._history[user_id] = []
                self._history[user_id].append(now)

    @contextlib.contextmanager
    def run_conversion_slot(self, user_id: int, on_queued: Optional[Any] = None):
        acquired_global = False
        try:
            acquired_now = self._semaphore.acquire(blocking=False)
            if not acquired_now:
                if on_queued:
                    try:
                        on_queued()
                    except Exception:
                        pass
                self._semaphore.acquire(blocking=True)
            acquired_global = True
            yield
        finally:
            if acquired_global:
                self._semaphore.release()
            self.release_user_slot(user_id)


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



# ==============================================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ ХЕНДЛЕРЫ
# ==============================================================================


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("user:"))
def handle_user_callback(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "⛔ Ваш доступ ограничен.", show_alert=True)
        return

    if action == "privacy":
        bot.answer_callback_query(call.id)
        text = format_privacy_text()
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🗑 Удалить мои данные", callback_data="user:delete_my_data"),
        )
        if call.message.content_type == "text":
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
        return

    if action == "delete_my_data":
        res = delete_user_data(user_id)
        cnt = res.get("deleted_jobs", 0)
        jobs_info = f"<b>{cnt} шт.</b>" if cnt > 0 else "нет активных файлов"
        bot.answer_callback_query(call.id, "Ваши данные удалены")
        text = (
            "🗑 <b>Все ваши персональные данные успешно удалены:</b>\n\n"
            f"• Макеты и задания на сервере: {jobs_info}\n"
            "• Профиль пользователя и история действий удалены\n"
            "• Пользовательские настройки сброшены\n\n"
            "Ваши файлы больше не хранятся на сервере."
        )
        if call.message.content_type == "text":
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id)
        else:
            bot.send_message(chat_id, text)
        return


register_harvester_bot_handlers(bot, is_admin, is_user_banned)


@bot.message_handler(content_types=["photo", "document"])
def handle_media(message: Message) -> None:
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id

    if is_user_banned(user_id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    was_compressed_photo = False
    orig_filename = "sticker.jpg"

    if message.content_type == "photo":
        was_compressed_photo = True
        photo_obj = message.photo[-1]
        if photo_obj.file_size and photo_obj.file_size > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            return
        file_id = photo_obj.file_id
        orig_filename = f"photo_{int(time.time())}.jpg"
    elif message.content_type == "document":
        doc = message.document
        orig_filename = doc.file_name or f"file_{int(time.time())}.png"
        ext = Path(orig_filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            bot.reply_to(
                message,
                f"⚠️ Формат <code>{html.escape(ext)}</code> не поддерживается.\nОтправьте изображение: JPG, PNG, WebP, BMP или TIFF.",
            )
            return
        if doc.file_size and doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            return
        file_id = doc.file_id
    else:
        return

    # Проверяем, не ожидает ли админ ручную загрузку авторского макета в Harvester
    upload_info = check_and_consume_waiting_upload(user_id) if is_admin(user_id) else None
    if upload_info:
        status_msg = bot.reply_to(message, "⏳ <b>Добавляю макет в каталог Harvester...</b>", parse_mode="HTML")
        tmp_path = None
        try:
            file_info = bot.get_file(file_id)
            downloaded = bot.download_file(file_info.file_path)
            if len(downloaded) > MAX_UPLOAD_MB * 1024 * 1024:
                bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
                return

            ext = Path(orig_filename).suffix.lower() or ".png"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                tmp_file.write(downloaded)
                tmp_path = Path(tmp_file.name)

            title = (message.caption or Path(orig_filename).stem or "Макет").strip()
            lic = upload_info.get("license", "unknown")
            topic = upload_info.get("topic", "Авторские макеты")

            asset = register_manual_asset(
                image_path=tmp_path,
                title=title,
                license_type=lic,
                uploaded_by=user_id,
                topic_name=topic,
            )
            lic_label = LICENSE_LABELS.get(asset.get("license", "unknown"), asset.get("license", "unknown"))
            bot.edit_message_text(
                f"✅ <b>Макет успешно добавлен в каталог Harvester!</b>\n\n"
                f"🏷 <b>Название:</b> {html.escape(asset.get('title', ''))}\n"
                f"📁 <b>Тематика:</b> {html.escape(asset.get('topic_name', ''))}\n"
                f"⚖️ <b>Лицензия:</b> <code>{html.escape(lic_label)}</code>\n"
                f"📏 <b>Размер:</b> {asset.get('width_mm', 0):.1f} × {asset.get('height_mm', 0):.1f} мм\n"
                f"🆔 <code>{html.escape(asset.get('asset_id', ''))}</code>\n\n"
                f"Файл сохранён в базе /harvester и готов к отправке на печать.",
                chat_id=chat_id,
                message_id=status_msg.message_id,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.exception("Failed to register manual asset: %s", exc)
            bot.edit_message_text(
                f"⚠️ Ошибка при добавлении макета: {html.escape(str(exc)[:200])}",
                chat_id=chat_id,
                message_id=status_msg.message_id,
            )
        finally:
            if tmp_path and tmp_path.exists():
                with contextlib.suppress(Exception):
                    tmp_path.unlink()
        return

    can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user_id)
    if not can_convert:
        if limit_reason == "concurrent":
            bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
        else:
            bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
        return

    # При загрузке нового макета сбрасываем тираж на 1 шт, пропорции на 1:1 и открываем главное меню
    curr_p = get_user_prefs(user_id)
    new_off = curr_p.get("offset_mm", 2.5)
    if new_off <= 0.0:
        new_off = 2.5
    update_user_prefs(
        user_id,
        nesting_qty=1,
        keep_background=False,
        vectorize=False,
        offset_mm=new_off,
        stretch_x=1.0,
        stretch_y=1.0,
        fit_a4_sheet=False,
        menu_tab="main",
    )

    status_msg = bot.reply_to(
        message,
        "⏳ <b>Обрабатываю макет на сервере crybaby...</b>\nВыделяю стикеры, строю контуры Roland VP-540 и примеряю на лист А4.",
    )

    job_id = uuid.uuid4().hex[:10]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    record_user_activity(
        user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="Загрузка изображения",
        last_job_id=job_id,
    )

    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        if len(downloaded) > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            conversion_limiter.release_user_slot(user_id, record_completed=False)
            return

        ext = Path(orig_filename).suffix.lower() or ".jpg"
        safe_stem = (
            re.sub(r"[^\w\-. ()А-Яа-яЁё]+", "_", Path(orig_filename).stem).strip()
            or "sticker"
        )
        input_filename = f"input{ext}"
        input_path = job_dir / input_filename
        input_path.write_bytes(downloaded)

        meta = {
            "job_id": job_id,
            "user_id": user_id,
            "orig_stem": safe_stem,
            "input_filename": input_filename,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        save_json_file(job_dir / "meta.json", meta)
        storage_save_job(meta)

        def on_queue_media() -> None:
            try:
                bot.edit_message_text(
                    "⏳ <b>Сервер занят.</b> Ваш макет в очереди на обработку...",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        with conversion_limiter.run_conversion_slot(user_id, on_queued=on_queue_media):
            process_and_send_job(
                chat_id=chat_id,
                user_id=user_id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=was_compressed_photo,
                action_title="Новая конвертация",
            )
    except DecompressionBombError:
        conversion_limiter.release_user_slot(user_id, record_completed=False)
        logger.warning("DecompressionBombError in handle_media for user %s", user_id)
        bot.send_message(
            chat_id,
            "⚠️ <b>Файл слишком большой или повреждён (превышен лимит пикселей).</b>\n"
            "Пожалуйста, уменьшите разрешение изображения.",
        )
    except Exception as exc:
        conversion_limiter.release_user_slot(user_id, record_completed=False)
        logger.exception("Error in handle_media for user %s", user_id)
        if is_admin(user_id):
            bot.send_message(
                chat_id,
                f"❌ <b>Ошибка при обработке файла:</b>\n<code>{html.escape(str(exc))}</code>",
            )
        else:
            bot.send_message(
                chat_id,
                "❌ <b>Не удалось обработать файл.</b>",
            )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("job:"))
def handle_job_callback(call: CallbackQuery) -> None:
    job_id = ""
    user_id = 0
    try:
        user = call.from_user
        user_id = user.id
        if is_user_banned(user_id):
            bot.answer_callback_query(call.id, "⛔ Доступ ограничен", show_alert=True)
            return

        parts = call.data.split(":")
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "Неверная команда")
            return

        _, job_id, param, val_str = parts
        chat_id = call.message.chat.id
        job_dir = JOBS_DIR / job_id
        meta_file = job_dir / "meta.json"

        if not job_dir.exists() or not meta_file.exists():
            bot.answer_callback_query(call.id, "⚠️ Исходный файл этой задачи уже очищен из кэша.")
            return

        meta = storage_get_job(job_id) or load_json_file(meta_file, {})
        job_owner_id = int(meta.get("user_id", 0))
        if not is_admin(user_id) and job_owner_id != user_id:
            bot.answer_callback_query(call.id, "⛔ Это не ваш макет", show_alert=True)
            return

        if param == "noop":
            bot.answer_callback_query(
                call.id,
                "💡 Вы также можете написать любой размер в чат (например: 15 см, 1.2 м или 20x10 см)",
            )
            return

        # Мгновенное переключение вложенных подменю прямо под фотографией без пересчёта файла
        if param == "nav":
            meta["menu_tab"] = val_str
            save_json_file(meta_file, meta)
            storage_update_job(job_id, menu_tab=val_str)
            update_user_prefs(user_id, menu_tab=val_str)
            a4_q = int((meta.get("pricing") or {}).get("a4_fit_qty", 1))
            new_kb = build_job_keyboard(
                job_id,
                meta,
                in_hot_folder=bool(meta.get("in_hot_folder")),
                a4_fit_qty=a4_q,
                menu_tab=val_str,
                hot_request_status=meta.get("hot_folder_request_status"),
            )
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=new_kb,
                )
            except Exception:
                pass
            bot.answer_callback_query(call.id)
            return

        if param == "hot":
            if is_admin(user_id) or ALLOW_USER_HOT_FOLDER:
                dest = copy_job_to_hot_folder(job_id)
                if dest is None:
                    bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🚀 Отправлено в Hot Folder плоттера!")
                bot.send_message(
                    chat_id,
                    f"🚀 <b>Макет отправлен на ПК с плоттером (Hot Folder)!</b>\n"
                    f"📄 Файл: <code>{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Windows: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Mac: <code>smb://192.168.0.108/Roland_HotFolder/Queue_EPS/{html.escape(dest.name)}</code>",
                )
                return
            else:
                if meta.get("hot_folder_request_status") == "pending":
                    bot.answer_callback_query(
                        call.id, "Заявка уже ожидает подтверждения администратором", show_alert=True
                    )
                    return

                meta["hot_folder_request_status"] = "pending"
                meta["hot_folder_requested_at"] = time.time()
                save_json_file(meta_file, meta)
                storage_update_job(
                    job_id,
                    hot_folder_request_status="pending",
                    hot_folder_requested_at=meta["hot_folder_requested_at"],
                )

                bot.answer_callback_query(call.id, "Заявка отправлена на согласование администратору!")
                bot.send_message(
                    chat_id,
                    "⏳ <b>Заявка на печать отправлена администратору!</b>\n"
                    "После подтверждения макет будет автоматически отправлен на плоттер.",
                )

                kb = InlineKeyboardMarkup()
                kb.row(
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"hreq:{job_id}:approve"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"hreq:{job_id}:reject"),
                )
                user_name = user.username and f"@{user.username}" or f"{user.first_name} ({user_id})"
                eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
                w_mm = meta.get("width_mm", "?")
                h_mm = meta.get("height_mm", "?")
                qty = meta.get("nesting_qty", 1)
                admin_text = (
                    f"📥 <b>Заявка на отправку в Hot Folder</b>\n"
                    f"👤 От: <b>{html.escape(str(user_name))}</b> (ID: <code>{user_id}</code>)\n"
                    f"📄 Файл: <code>{html.escape(eps_name)}</code>\n"
                    f"📐 Размер: <b>{w_mm} × {h_mm} мм</b> | Тираж: <b>{qty} шт.</b>\n"
                    f"🆔 Задача: <code>{job_id}</code>"
                )
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(admin_id, admin_text, reply_markup=kb)
                    except Exception as e:
                        logger.warning("Failed to notify admin %s: %s", admin_id, e)
                return

        curr_layout = dict(get_user_prefs(user_id))
        curr_layout.update({k: v for k, v in meta.items() if v is not None})
        action_desc = "Пересчёт параметров"
        if param == "off":
            offset_val = float(val_str)
            meta["offset_mm"] = offset_val
            meta["menu_tab"] = "cut"
            storage_update_job(job_id, offset_mm=offset_val, menu_tab="cut")
            update_user_prefs(user_id, offset_mm=offset_val, menu_tab="cut")
            action_desc = f"Кант {val_str} мм"
            bot.answer_callback_query(call.id, f"Отступ изменён на {val_str} мм. Пересчитываю...")
        elif param == "sz":
            val_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, float(val_str)))
            meta["size_mm"] = val_sz
            meta["fit_a4_sheet"] = False
            meta["menu_tab"] = "size"
            storage_update_job(job_id, size_mm=val_sz, fit_a4_sheet=0, menu_tab="size")
            update_user_prefs(user_id, size_mm=val_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {val_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер изменён на {val_sz/10.0:g} см. Пересчитываю...")
        elif param == "dsz":
            delta_mm = float(val_str)
            new_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, round(float(curr_layout.get("size_mm", 100.0)) + delta_mm, 1)))
            meta["size_mm"] = new_sz
            meta["fit_a4_sheet"] = False
            meta["menu_tab"] = "size"
            storage_update_job(job_id, size_mm=new_sz, fit_a4_sheet=0, menu_tab="size")
            update_user_prefs(user_id, size_mm=new_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {new_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер: {new_sz/10.0:g} см. Пересчитываю...")
        elif param == "stx":
            mult = float(val_str)
            new_sx = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_layout.get("stretch_x", 1.0)) * mult, 4)))
            meta["stretch_x"] = new_sx
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_x=new_sx, menu_tab="stretch")
            update_user_prefs(user_id, stretch_x=new_sx, menu_tab="stretch")
            action_desc = f"Ширина ×{new_sx:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб ширины: ×{new_sx:.3g}. Пересчитываю...")
        elif param == "sty":
            mult = float(val_str)
            new_sy = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_layout.get("stretch_y", 1.0)) * mult, 4)))
            meta["stretch_y"] = new_sy
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_y=new_sy, menu_tab="stretch")
            update_user_prefs(user_id, stretch_y=new_sy, menu_tab="stretch")
            action_desc = f"Высота ×{new_sy:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб высоты: ×{new_sy:.3g}. Пересчитываю...")
        elif param == "rst":
            meta["stretch_x"] = 1.0
            meta["stretch_y"] = 1.0
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_x=1.0, stretch_y=1.0, menu_tab="stretch")
            update_user_prefs(user_id, stretch_x=1.0, stretch_y=1.0, menu_tab="stretch")
            action_desc = "Сброс пропорций 1:1"
            bot.answer_callback_query(call.id, "Пропорции сброшены 1:1. Пересчитываю...")
        elif param == "a4":
            use_a4 = (val_str == "1")
            meta["fit_a4_sheet"] = use_a4
            meta["nesting_qty"] = 1
            storage_update_job(job_id, fit_a4_sheet=int(use_a4), nesting_qty=1)
            update_user_prefs(user_id, fit_a4_sheet=use_a4, nesting_qty=1)
            action_desc = "Раскладка на лист А4 (210×297 мм)" if use_a4 else "Режим 1 наклейки"
            bot.answer_callback_query(call.id, f"{action_desc}...")
        elif param == "perf":
            meta["perf_cut_mode"] = val_str
            meta["menu_tab"] = "cut"
            storage_update_job(job_id, perf_cut_mode=val_str, menu_tab="cut")
            update_user_prefs(user_id, perf_cut_mode=val_str, menu_tab="cut")
            action_desc = PERF_MODE_LABELS.get(val_str, val_str)
            bot.answer_callback_query(call.id, f"Режим резки: {action_desc}. Пересчитываю...")
        elif param == "ppass":
            p_cnt = max(1, min(5, int(val_str)))
            new_pmode = curr_layout.get("perf_cut_mode", "none")
            if new_pmode == "none" and not curr_layout.get("fit_a4_sheet", False):
                new_pmode = "die_cut"
            meta["perf_cut_passes"] = p_cnt
            meta["perf_cut_mode"] = new_pmode
            meta["menu_tab"] = "cut"
            storage_update_job(job_id, perf_cut_passes=p_cnt, perf_cut_mode=new_pmode, menu_tab="cut")
            update_user_prefs(
                user_id,
                perf_cut_passes=p_cnt,
                perf_cut_mode=new_pmode,
                menu_tab="cut",
            )
            action_desc = f"Сквозной рез PerfCut: {p_cnt} прохода(ов)"
            bot.answer_callback_query(call.id, f"{action_desc}. Пересчитываю...")
        elif param == "qty":
            q = int(val_str)
            meta["nesting_qty"] = q
            meta["fit_a4_sheet"] = False
            meta["menu_tab"] = "layout"
            storage_update_job(job_id, nesting_qty=q, fit_a4_sheet=0, menu_tab="layout")
            update_user_prefs(user_id, nesting_qty=q, fit_a4_sheet=False, menu_tab="layout")
            action_desc = f"Раскладка тиража {q} шт. на рулон"
            bot.answer_callback_query(call.id, f"Собираю раскладку {q} шт. на рулон...")
        elif param == "bg":
            use_bg = (val_str == "1")
            meta["keep_background"] = use_bg
            storage_update_job(job_id, keep_background=int(use_bg))
            update_user_prefs(user_id, keep_background=use_bg)
            action_desc = "Изменён режим фона"
            bot.answer_callback_query(call.id, "Режим фона изменён. Пересчитываю...")
        elif param == "vec":
            meta["vectorize"] = False
            storage_update_job(job_id, vectorize=0)
            update_user_prefs(user_id, vectorize=False)
            action_desc = "Оригинальное качество"
            bot.answer_callback_query(call.id, "Пересчитываю...")

        save_json_file(meta_file, meta)


        record_user_activity(
            user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            action=action_desc,
            last_job_id=job_id,
        )

        can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user_id)
        if not can_convert:
            if limit_reason == "concurrent":
                bot.answer_callback_query(
                    call.id, "⏳ Предыдущая конвертация ещё выполняется. Пожалуйста, дождитесь завершения.", show_alert=True
                )
            else:
                bot.answer_callback_query(
                    call.id, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите.", show_alert=True
                )
            return

        status_msg = bot.send_message(
            chat_id,
            f"🔄 <b>{action_desc}: пересчитываю EPS...</b>",
        )

        def on_queue_cb() -> None:
            try:
                bot.edit_message_text(
                    f"⏳ <b>Сервер занят ({action_desc}).</b> Макет в очереди на обработку...",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        with conversion_limiter.run_conversion_slot(user_id, on_queued=on_queue_cb):
            process_and_send_job(
                chat_id=chat_id,
                user_id=user_id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=False,
                action_title=action_desc,
            )
    except Exception as exc:
        logger.exception("Error in handle_job_callback for job %s, user %s", job_id, user_id)
        if is_admin(user_id):
            bot.answer_callback_query(call.id, f"Ошибка: {exc}", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ Не удалось обработать файл", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("hreq:"))
def handle_hot_folder_request_callback(call: CallbackQuery) -> None:
    admin_user = call.from_user
    if not is_admin(admin_user.id):
        bot.answer_callback_query(call.id, "⛔ Только для администраторов", show_alert=True)
        return

    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Неверная команда")
        return

    _, job_id, action = parts
    job_dir = JOBS_DIR / job_id
    meta_file = job_dir / "meta.json"
    if not meta_file.exists():
        bot.answer_callback_query(call.id, "⚠️ Задача не найдена", show_alert=True)
        return

    meta = load_json_file(meta_file, {})
    target_user_id = int(meta.get("user_id", 0))

    if action == "approve":
        dest = copy_job_to_hot_folder(job_id)
        if dest is None:
            bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
            return
        meta["hot_folder_request_status"] = "approved"
        meta["hot_folder_approved_by"] = admin_user.id
        save_json_file(meta_file, meta)
        storage_update_job(job_id, hot_folder_request_status="approved", hot_folder_approved_by=admin_user.id)

        bot.answer_callback_query(call.id, "✅ Заявка одобрена!")
        try:
            admin_name = admin_user.first_name or str(admin_user.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ <b>Заявка одобрена</b> администратором {html.escape(admin_name)}!\n"
                     f"Файл <code>{html.escape(dest.name)}</code> скопирован в Hot Folder.",
            )
        except Exception:
            pass

        if target_user_id:
            try:
                bot.send_message(
                    target_user_id,
                    f"🚀 <b>Администратор одобрил печать макета!</b>\n"
                    f"📄 Файл отправлен на плоттер: <code>{html.escape(dest.name)}</code>",
                )
            except Exception as e:
                logger.warning("Failed to notify user %s about approval: %s", target_user_id, e)

    elif action == "reject":
        meta["hot_folder_request_status"] = "rejected"
        meta["hot_folder_rejected_by"] = admin_user.id
        save_json_file(meta_file, meta)
        storage_update_job(job_id, hot_folder_request_status="rejected", hot_folder_rejected_by=admin_user.id)


        bot.answer_callback_query(call.id, "❌ Заявка отклонена")
        try:
            admin_name = admin_user.first_name or str(admin_user.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ <b>Заявка отклонена</b> администратором {html.escape(admin_name)}.",
            )
        except Exception:
            pass

        if target_user_id:
            try:
                bot.send_message(
                    target_user_id,
                    f"❌ <b>Администратор отклонил отправку макета {job_id} в Hot Folder.</b>",
                )
            except Exception as e:
                logger.warning("Failed to notify user %s about rejection: %s", target_user_id, e)





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
