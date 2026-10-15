"""Модуль обработки inline callback-запросов Telegram-бота.

Включает:
- handle_user_callback (user:*): действия пользователя (privacy, delete_my_data)
- handle_job_callback (job:*): настройка параметров макета (размер, отступ, резка, тираж, пропорции, Hot Folder)
- handle_hot_folder_request_callback (hreq:*): одобрение/отклонение администратором отправки в Hot Folder
- register_callback_handlers: регистрация обработчиков callback-запросов в боте
"""

from __future__ import annotations

import html
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot.jobs import (
    copy_job_to_hot_folder,
    delete_user_data,
    format_privacy_text,
    load_json_file,
    process_and_send_job,
    save_json_file,
)
from bot.keyboards import (
    build_job_keyboard,
    is_admin,
)
from config import (
    ADMIN_IDS,
    ALLOW_USER_HOT_FOLDER,
    HOT_FOLDER_SMB_HOST,
    JOBS_DIR,
)
from storage import (
    get_job as storage_get_job,
    get_user_prefs as storage_get_user_prefs,
    is_user_banned as storage_is_user_banned,
    record_user_activity as storage_record_user_activity,
    update_job as storage_update_job,
    update_user_prefs as storage_update_user_prefs,
)
from web.pages import PERF_MODE_LABELS

logger = logging.getLogger(__name__)

MIN_SIZE_MM = 5.0
MAX_SIZE_MM = 1370.0
MIN_STRETCH = 0.1
MAX_STRETCH = 10.0


def _get_bot() -> Any:
    import telegram_bot

    return telegram_bot.bot


def _get_jobs_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "JOBS_DIR", JOBS_DIR))
    except Exception:
        return JOBS_DIR


def _get_limiter() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "conversion_limiter", None)
    except Exception:
        return None


def _get_allow_user_hot_folder() -> bool:
    try:
        import telegram_bot

        return bool(getattr(telegram_bot, "ALLOW_USER_HOT_FOLDER", ALLOW_USER_HOT_FOLDER))
    except Exception:
        return ALLOW_USER_HOT_FOLDER


def _get_process_and_send_job() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "process_and_send_job", process_and_send_job)
    except Exception:
        return process_and_send_job


def _is_user_banned(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "is_user_banned", None)
        if callable(fn):
            return bool(fn(user_id))
    except Exception:
        pass
    return storage_is_user_banned(user_id, admin_ids=list(ADMIN_IDS))


def _get_user_prefs(user_id: int) -> Dict[str, Any]:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "get_user_prefs", None)
        if callable(fn):
            return fn(user_id)
    except Exception:
        pass
    return storage_get_user_prefs(user_id)


def _update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "update_user_prefs", None)
        if callable(fn):
            return fn(user_id, **kwargs)
    except Exception:
        pass
    return storage_update_user_prefs(user_id, **kwargs)


def _record_user_activity(
    user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    action: str = "",
    last_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "record_user_activity", None)
        if callable(fn):
            return fn(
                user_id,
                first_name=first_name,
                last_name=last_name,
                username=username,
                action=action,
                last_job_id=last_job_id,
            )
    except Exception:
        pass
    return storage_record_user_activity(
        user_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        action=action,
        last_job_id=last_job_id,
    )


def handle_user_callback(call: CallbackQuery) -> None:
    bot = _get_bot()
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if _is_user_banned(user_id):
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


def handle_job_callback(call: CallbackQuery) -> None:
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    limiter = _get_limiter()
    do_process = _get_process_and_send_job()
    allow_hot = _get_allow_user_hot_folder()

    job_id = ""
    user_id = 0
    try:
        user = call.from_user
        user_id = user.id
        if _is_user_banned(user_id):
            bot.answer_callback_query(call.id, "⛔ Доступ ограничен", show_alert=True)
            return

        parts = call.data.split(":")
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "Неверная команда")
            return

        _, job_id, param, val_str = parts
        chat_id = call.message.chat.id
        job_dir = jobs_dir / job_id
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

        if param == "nav":
            meta["menu_tab"] = val_str
            save_json_file(meta_file, meta)
            storage_update_job(job_id, menu_tab=val_str)
            _update_user_prefs(user_id, menu_tab=val_str)
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
            if is_admin(user_id) or allow_hot:
                dest = copy_job_to_hot_folder(job_id)
                if dest is None:
                    bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🚀 Отправлено в Hot Folder плоттера!")
                bot.send_message(
                    chat_id,
                    f"🚀 <b>Макет отправлен на ПК с плоттером (Hot Folder)!</b>\n"
                    f"📄 Файл: <code>{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Windows: <code>\\\\{HOT_FOLDER_SMB_HOST}\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Mac: <code>smb://{HOT_FOLDER_SMB_HOST}/Roland_HotFolder/Queue_EPS/{html.escape(dest.name)}</code>",
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

        curr_layout = dict(_get_user_prefs(user_id))
        curr_layout.update({k: v for k, v in meta.items() if v is not None})
        action_desc = "Пересчёт параметров"
        if param == "off":
            offset_val = float(val_str)
            meta["offset_mm"] = offset_val
            meta["menu_tab"] = "cut"
            storage_update_job(job_id, offset_mm=offset_val, menu_tab="cut")
            _update_user_prefs(user_id, offset_mm=offset_val, menu_tab="cut")
            action_desc = f"Кант {val_str} мм"
            bot.answer_callback_query(call.id, f"Отступ изменён на {val_str} мм. Пересчитываю...")
        elif param == "sz":
            val_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, float(val_str)))
            meta["size_mm"] = val_sz
            meta["fit_a4_sheet"] = False
            meta["menu_tab"] = "size"
            storage_update_job(job_id, size_mm=val_sz, fit_a4_sheet=0, menu_tab="size")
            _update_user_prefs(user_id, size_mm=val_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {val_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер изменён на {val_sz/10.0:g} см. Пересчитываю...")
        elif param == "dsz":
            delta_mm = float(val_str)
            new_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, round(float(curr_layout.get("size_mm", 100.0)) + delta_mm, 1)))
            meta["size_mm"] = new_sz
            meta["fit_a4_sheet"] = False
            meta["menu_tab"] = "size"
            storage_update_job(job_id, size_mm=new_sz, fit_a4_sheet=0, menu_tab="size")
            _update_user_prefs(user_id, size_mm=new_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {new_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер: {new_sz/10.0:g} см. Пересчитываю...")
        elif param == "stx":
            mult = float(val_str)
            new_sx = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_layout.get("stretch_x", 1.0)) * mult, 4)))
            meta["stretch_x"] = new_sx
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_x=new_sx, menu_tab="stretch")
            _update_user_prefs(user_id, stretch_x=new_sx, menu_tab="stretch")
            action_desc = f"Ширина ×{new_sx:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб ширины: ×{new_sx:.3g}. Пересчитываю...")
        elif param == "sty":
            mult = float(val_str)
            new_sy = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_layout.get("stretch_y", 1.0)) * mult, 4)))
            meta["stretch_y"] = new_sy
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_y=new_sy, menu_tab="stretch")
            _update_user_prefs(user_id, stretch_y=new_sy, menu_tab="stretch")
            action_desc = f"Высота ×{new_sy:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб высоты: ×{new_sy:.3g}. Пересчитываю...")
        elif param == "rst":
            meta["stretch_x"] = 1.0
            meta["stretch_y"] = 1.0
            meta["menu_tab"] = "stretch"
            storage_update_job(job_id, stretch_x=1.0, stretch_y=1.0, menu_tab="stretch")
            _update_user_prefs(user_id, stretch_x=1.0, stretch_y=1.0, menu_tab="stretch")
            action_desc = "Сброс пропорций 1:1"
            bot.answer_callback_query(call.id, "Пропорции сброшены 1:1. Пересчитываю...")
        elif param == "a4":
            use_a4 = (val_str == "1")
            meta["fit_a4_sheet"] = use_a4
            meta["nesting_qty"] = 1
            storage_update_job(job_id, fit_a4_sheet=int(use_a4), nesting_qty=1)
            _update_user_prefs(user_id, fit_a4_sheet=use_a4, nesting_qty=1)
            action_desc = "Раскладка на лист А4 (210×297 мм)" if use_a4 else "Режим 1 наклейки"
            bot.answer_callback_query(call.id, f"{action_desc}...")
        elif param == "perf":
            meta["perf_cut_mode"] = val_str
            meta["menu_tab"] = "cut"
            storage_update_job(job_id, perf_cut_mode=val_str, menu_tab="cut")
            _update_user_prefs(user_id, perf_cut_mode=val_str, menu_tab="cut")
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
            _update_user_prefs(
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
            _update_user_prefs(user_id, nesting_qty=q, fit_a4_sheet=False, menu_tab="layout")
            action_desc = f"Раскладка тиража {q} шт. на рулон"
            bot.answer_callback_query(call.id, f"Собираю раскладку {q} шт. на рулон...")
        elif param == "bg":
            use_bg = (val_str == "1")
            meta["keep_background"] = use_bg
            storage_update_job(job_id, keep_background=int(use_bg))
            _update_user_prefs(user_id, keep_background=use_bg)
            action_desc = "Изменён режим фона"
            bot.answer_callback_query(call.id, "Режим фона изменён. Пересчитываю...")

        save_json_file(meta_file, meta)

        _record_user_activity(
            user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            action=action_desc,
            last_job_id=job_id,
        )

        if limiter:
            can_convert, limit_reason = limiter.check_and_acquire_user_slot(user_id)
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

        if limiter and hasattr(limiter, "run_conversion_slot"):
            with limiter.run_conversion_slot(user_id, on_queued=on_queue_cb):
                do_process(
                    chat_id=chat_id,
                    user_id=user_id,
                    job_dir=job_dir,
                    status_msg_id=status_msg.message_id,
                    was_compressed_photo=False,
                    action_title=action_desc,
                )
        else:
            do_process(
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


def handle_hot_folder_request_callback(call: CallbackQuery) -> None:
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    admin_user = call.from_user
    if not is_admin(admin_user.id):
        bot.answer_callback_query(call.id, "⛔ Только для администраторов", show_alert=True)
        return

    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Неверная команда")
        return

    _, job_id, action = parts
    job_dir = jobs_dir / job_id
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


def register_callback_handlers(bot: Any) -> None:
    """Регистрирует обработчики callback-запросов в боте."""
    bot.register_callback_query_handler(
        handle_user_callback,
        func=lambda call: call.data and call.data.startswith("user:"),
    )
    bot.register_callback_query_handler(
        handle_job_callback,
        func=lambda call: call.data and call.data.startswith("job:"),
    )
    bot.register_callback_query_handler(
        handle_hot_folder_request_callback,
        func=lambda call: call.data and call.data.startswith("hreq:"),
    )
