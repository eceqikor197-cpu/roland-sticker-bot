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
