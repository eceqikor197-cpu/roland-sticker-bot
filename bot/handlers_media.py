"""Модуль обработки входящих медиафайлов (фото и документов) Telegram-бота.

Включает:
- ConversionLimiter: ограничение параллельных конвертаций и частоты запросов
- handle_media: приём фото/документов, проверка лимитов, сохранение и запуск конвертации
- register_media_handlers: регистрация обработчика медиафайлов в боте
"""

from __future__ import annotations

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

from PIL.Image import DecompressionBombError
from telebot.types import Message

from bot.jobs import (
    process_and_send_job,
    save_json_file,
)
from bot.keyboards import is_admin
from config import (
    ADMIN_IDS,
    JOBS_DIR,
)
from harvester.bot_integration import (
    check_and_consume_waiting_upload,
    register_manual_asset,
)
from harvester.database import LICENSE_LABELS
from jpeg2eps import SUPPORTED_EXTENSIONS
from storage import (
    get_user_prefs as storage_get_user_prefs,
    is_user_banned as storage_is_user_banned,
    record_user_activity as storage_record_user_activity,
    save_job as storage_save_job,
    update_user_prefs as storage_update_user_prefs,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "15"))
DEFAULT_MAX_CONVERSIONS_PER_MINUTE = int(os.getenv("MAX_CONVERSIONS_PER_MINUTE", "6"))
DEFAULT_MAX_CONCURRENT_CONVERSIONS = int(os.getenv("MAX_CONCURRENT_CONVERSIONS", "2"))


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


default_conversion_limiter = ConversionLimiter(
    max_per_minute=DEFAULT_MAX_CONVERSIONS_PER_MINUTE,
    max_concurrent_global=DEFAULT_MAX_CONCURRENT_CONVERSIONS,
)


def _get_bot() -> Any:
    import telegram_bot

    return telegram_bot.bot


def _get_jobs_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "JOBS_DIR", JOBS_DIR))
    except Exception:
        return JOBS_DIR


def _get_max_upload_mb() -> float:
    try:
        import telegram_bot

        return float(getattr(telegram_bot, "MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
    except Exception:
        return DEFAULT_MAX_UPLOAD_MB


def _get_limiter() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "conversion_limiter", default_conversion_limiter)
    except Exception:
        return default_conversion_limiter


def _get_process_and_send_job() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "process_and_send_job", process_and_send_job)
    except Exception:
        return process_and_send_job


def _is_user_banned(user_id: int) -> bool:
    try:
        import telegram_bot

        return bool(telegram_bot.is_user_banned(user_id))
    except Exception:
        if is_admin(user_id):
            return False
        return storage_is_user_banned(user_id, admin_ids=list(ADMIN_IDS))


def _record_user_activity(user_id: int, **kwargs: Any) -> Any:
    try:
        import telegram_bot

        return telegram_bot.record_user_activity(user_id, **kwargs)
    except Exception:
        return storage_record_user_activity(user_id, **kwargs)


def _get_user_prefs(user_id: int) -> Dict[str, Any]:
    try:
        import telegram_bot

        return dict(telegram_bot.get_user_prefs(user_id))
    except Exception:
        return storage_get_user_prefs(user_id)


def _update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    try:
        import telegram_bot

        return dict(telegram_bot.update_user_prefs(user_id, **kwargs))
    except Exception:
        return storage_update_user_prefs(user_id, **kwargs)


def handle_media(message: Message) -> None:
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    max_upload_mb = _get_max_upload_mb()
    limiter = _get_limiter()
    process_fn = _get_process_and_send_job()

    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id

    if _is_user_banned(user_id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    was_compressed_photo = False
    orig_filename = "sticker.jpg"

    if message.content_type == "photo":
        was_compressed_photo = True
        photo_obj = message.photo[-1]
        if photo_obj.file_size and photo_obj.file_size > max_upload_mb * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {max_upload_mb:g} МБ.")
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
                f"⚠️ Формат <code>{html.escape(ext)}</code> не поддерживается.\n"
                "Отправьте изображение: JPG, PNG, WebP, BMP или TIFF.",
            )
            return
        if doc.file_size and doc.file_size > max_upload_mb * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {max_upload_mb:g} МБ.")
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
            if len(downloaded) > max_upload_mb * 1024 * 1024:
                bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {max_upload_mb:g} МБ.")
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
                "Файл сохранён в базе /harvester и готов к отправке на печать.",
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

    can_convert, limit_reason = limiter.check_and_acquire_user_slot(user_id)
    if not can_convert:
        if limit_reason == "concurrent":
            bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
        else:
            bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
        return

    # При загрузке нового макета сбрасываем тираж на 1 шт, пропорции на 1:1 и открываем главное меню
    curr_p = _get_user_prefs(user_id)
    new_off = curr_p.get("offset_mm", 2.5)
    if new_off <= 0.0:
        new_off = 2.5
    _update_user_prefs(
        user_id,
        nesting_qty=1,
        keep_background=False,
        offset_mm=new_off,
        stretch_x=1.0,
        stretch_y=1.0,
        fit_a4_sheet=False,
        menu_tab="main",
    )

    status_msg = bot.reply_to(
        message,
        "⏳ <b>Обрабатываю макет на сервере crybaby...</b>\n"
        "Выделяю стикеры, строю контуры Roland VP-540 и примеряю на лист А4.",
    )

    job_id = uuid.uuid4().hex[:10]
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    _record_user_activity(
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
        if len(downloaded) > max_upload_mb * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {max_upload_mb:g} МБ.")
            limiter.release_user_slot(user_id, record_completed=False)
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

        with limiter.run_conversion_slot(user_id, on_queued=on_queue_media):
            process_fn(
                chat_id=chat_id,
                user_id=user_id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=was_compressed_photo,
                action_title="Новая конвертация",
            )
    except DecompressionBombError:
        limiter.release_user_slot(user_id, record_completed=False)
        logger.warning("DecompressionBombError in handle_media for user %s", user_id)
        bot.send_message(
            chat_id,
            "⚠️ <b>Файл слишком большой или повреждён (превышен лимит пикселей).</b>\n"
            "Пожалуйста, уменьшите разрешение изображения.",
        )
    except Exception as exc:
        limiter.release_user_slot(user_id, record_completed=False)
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


def register_media_handlers(bot: Any) -> None:
    """Регистрирует обработчики входящих медиафайлов в боте."""
    bot.register_message_handler(handle_media, content_types=["photo", "document"])
