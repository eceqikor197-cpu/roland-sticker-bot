"""Модуль административных и служебных обработчиков Telegram-бота.

Включает:
- handle_admin_command (/admin, /stats): главное меню админ-панели
- handle_spy_command (/spy, /live_spy): управление Live-шпионом
- handle_admin_callback (adm:*): навигация по админке, блокировка, просмотр заданий
- handle_start (/start, /help): приветствие и сводка настроек
- handle_privacy (/privacy): политика конфиденциальности
- handle_delete_my_data (/delete_my_data): удаление данных пользователя
- parse_size_text, handle_custom_size_text: парсинг и установка размеров текстом в чате
- register_admin_handlers: регистрация всех обработчиков модуля в боте
"""

from __future__ import annotations

import html
import logging
import re
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.jobs import (
    copy_job_to_hot_folder,
    delete_user_data,
    format_privacy_text,
    load_json_file,
    process_and_send_job,
    save_json_file,
    send_or_edit_admin_feed_item,
)
from bot.keyboards import (
    build_admin_home_view,
    build_admin_users_view,
    is_admin,
)
from config import (
    ADMIN_IDS,
    HOT_FOLDER_SMB_HOST,
    JOB_RETENTION_HOURS,
    JOBS_DIR,
    LIVE_SPY_DEFAULT,
)
from harvester.bot_integration import (
    check_and_consume_waiting_topic,
    enqueue_and_run_urgent_topic,
)
from storage import (
    get_admin_state as storage_get_admin_state,
    get_job as storage_get_job,
    get_user_display as storage_get_user_display,
    get_user_prefs as storage_get_user_prefs,
    is_user_banned as storage_is_user_banned,
    record_user_activity as storage_record_user_activity,
    toggle_ban_user as storage_toggle_ban_user,
    update_admin_state as storage_update_admin_state,
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


def _get_process_and_send_job() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "process_and_send_job", process_and_send_job)
    except Exception:
        return process_and_send_job


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


def _get_admin_state() -> Dict[str, Any]:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "get_admin_state", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return storage_get_admin_state()


def _update_admin_state(**kwargs: Any) -> Dict[str, Any]:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "update_admin_state", None)
        if callable(fn):
            return fn(**kwargs)
    except Exception:
        pass
    return storage_update_admin_state(**kwargs)


def _toggle_ban_user(target_uid: int) -> bool:
    try:
        import telegram_bot

        fn = getattr(telegram_bot, "toggle_ban_user", None)
        if callable(fn):
            return bool(fn(target_uid))
    except Exception:
        pass
    return storage_toggle_ban_user(target_uid)


def handle_admin_command(message: Message) -> None:
    bot = _get_bot()
    user = message.from_user
    _record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/admin",
    )
    if not is_admin(user.id):
        bot.reply_to(message, "⛔ Доступ к админ-панели есть только у администратора.")
        return

    text, kb = build_admin_home_view()
    bot.send_message(message.chat.id, text, reply_markup=kb)


def handle_spy_command(message: Message) -> None:
    bot = _get_bot()
    user = message.from_user
    _record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/spy",
    )
    if not is_admin(user.id):
        bot.reply_to(message, "⛔ Доступ к управлению Live-шпионом есть только у администратора.")
        return

    raw_text = (message.text or "").strip()
    parts = raw_text.split()
    st = _get_admin_state()
    current = st.get("live_spy_enabled", LIVE_SPY_DEFAULT)

    if len(parts) > 1:
        arg = parts[1].lower()
        if arg in ("on", "1", "true", "вкл", "включить"):
            new_val = True
        elif arg in ("off", "0", "false", "выкл", "выключить"):
            new_val = False
        else:
            bot.reply_to(
                message,
                "ℹ️ Использование: <code>/spy</code> (переключить) или <code>/spy on</code> / <code>/spy off</code>",
            )
            return
    else:
        new_val = not current

    _update_admin_state(live_spy_enabled=new_val)
    status_str = "🟢 ВКЛЮЧЕН" if new_val else "🔴 ВЫКЛЮЧЕН"
    bot.reply_to(
        message,
        f"🔔 <b>Live-шпион: {status_str}</b>\n\n"
        f"Уведомления о макетах пользователей {'будут' if new_val else 'НЕ будут'} пересылаться администратору.\n"
        f"ℹ️ <i>Пользователи уведомлены в /privacy о том, что администратор может просматривать макеты для обработки заказов.</i>",
    )


def handle_admin_callback(call: CallbackQuery) -> None:
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Только для администратора", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    chat_id = call.message.chat.id

    try:
        if action == "noop":
            bot.answer_callback_query(call.id)
            return

        if action == "home":
            bot.answer_callback_query(call.id)
            text, kb = build_admin_home_view()
            if call.message.content_type == "text":
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            else:
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except Exception:
                    pass
                bot.send_message(chat_id, text, reply_markup=kb)
            return

        if action == "toggle_spy":
            st = _get_admin_state()
            new_val = not st.get("live_spy_enabled", LIVE_SPY_DEFAULT)
            _update_admin_state(live_spy_enabled=new_val)
            bot.answer_callback_query(
                call.id,
                f"Live-уведомления: {'ВКЛЮЧЕНЫ 🟢' if new_val else 'ВЫКЛЮЧЕНЫ 🔴'}",
            )
            text, kb = build_admin_home_view()
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            return

        if action == "users":
            bot.answer_callback_query(call.id)
            text, kb = build_admin_users_view()
            if call.message.content_type == "text":
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            else:
                bot.send_message(chat_id, text, reply_markup=kb)
            return

        if action == "ban" and len(parts) >= 3:
            target_uid = int(parts[2])
            now_banned = _toggle_ban_user(target_uid)
            bot.answer_callback_query(
                call.id,
                f"Пользователь {target_uid} {'заблокирован 🚫' if now_banned else 'разблокирован ✅'}",
            )
            text, kb = build_admin_users_view()
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            return

        if action == "feed" and len(parts) >= 4:
            idx = int(parts[2])
            filter_arg = parts[3]
            bot.answer_callback_query(call.id)
            send_or_edit_admin_feed_item(
                chat_id=chat_id,
                index=idx,
                filter_arg=filter_arg,
                prev_msg_id=call.message.message_id,
            )
            return

        if action == "hot" and len(parts) >= 3:
            job_id = parts[2]
            dest = copy_job_to_hot_folder(job_id)
            if dest is None:
                bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "✅ Отправлено в Hot Folder плоттера!")
            bot.send_message(
                chat_id,
                f"🚀 <b>Файл отправлен в сетевую папку плоттера (Hot Folder)!</b>\n"
                f"📄 Имя файла: <code>{html.escape(dest.name)}</code>\n"
                f"📂 На ПК с Roland VersaWorks: <code>\\\\{HOT_FOLDER_SMB_HOST}\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>",
            )
            return

        if action == "dl_eps" and len(parts) >= 3:
            job_id = parts[2]
            jdir = jobs_dir / job_id
            meta = load_json_file(jdir / "meta.json", {})
            eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
            eps_path = jdir / eps_name
            if not eps_path.exists():
                bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "📥 Отправляю .EPS файл...")
            with open(eps_path, "rb") as ef:
                bot.send_document(
                    chat_id,
                    document=ef,
                    visible_file_name=eps_name,
                    caption=f"🖨 Файл пользователя {storage_get_user_display(int(meta.get('user_id', 0)))} — <code>{html.escape(eps_name)}</code>",
                )
            return

        if action == "dl_orig" and len(parts) >= 3:
            job_id = parts[2]
            jdir = jobs_dir / job_id
            meta = load_json_file(jdir / "meta.json", {})
            inp_name = meta.get("input_filename", "input.jpg")
            inp_path = jdir / inp_name
            if not inp_path.exists():
                bot.answer_callback_query(call.id, "⚠️ Исходник не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "🖼 Отправляю исходный файл...")
            ext = Path(inp_name).suffix or ".jpg"
            orig_dl_name = f"{meta.get('orig_stem', 'original')}{ext}"
            with open(inp_path, "rb") as inf:
                bot.send_document(
                    chat_id,
                    document=inf,
                    visible_file_name=orig_dl_name,
                    caption=f"🖼 Исходник от {storage_get_user_display(int(meta.get('user_id', 0)))}",
                )
            return

    except Exception as exc:
        traceback.print_exc()
        bot.answer_callback_query(call.id, f"Ошибка: {exc}")


def handle_start(message: Message) -> None:
    bot = _get_bot()
    user = message.from_user
    _record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/start",
    )
    if _is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    prefs = _get_user_prefs(user.id)
    retention_days = JOB_RETENTION_HOURS / 24.0
    if retention_days.is_integer():
        retention_days_str = f"{int(retention_days)} дн."
    else:
        retention_days_str = f"{retention_days:.1f} дн."

    text = (
        "🖨 <b>Бот подготовки файлов для плоттера Roland VP-540 (Print & Cut → EPS)</b>\n"
        "Работает 24/7 на сервере <code>crybaby</code>.\n\n"
        "<b>Что умеет бот:</b>\n"
        "• 💎 <b>100% сохранение качества оригинала</b> внутри наклеек + розовый контур надсечки <code>CutContour</code>;\n"
        "• 📦 <b>Двойной рез для Roland VersaWorks:</b> умеет добавлять сквозной контур <code>PerfCutContour</code> (голубая линия на превью) — рамку вокруг всего стикерпака или вырубку каждого стикера навылет;\n"
        "• 🧩 <b>Авто-раскладка на рулон (Nesting) + калькулятор цены:</b> собирает тираж 10 / 25 / 50 / 100 шт. в один печатный <code>.eps</code> на ширину рулона и считает себестоимость плёнки/чернил;\n"
        f"• 🚀 <b>Отправка в Hot Folder плоттера:</b> в 1 клик кидает готовый <code>.eps</code> в сетевую папку <code>\\\\{HOT_FOLDER_SMB_HOST}\\Roland_HotFolder</code>.\n\n"
        f"⚙️ <b>Ваши текущие настройки:</b>\n"
        f"• Размер большей стороны: <b>{prefs['size_mm']:g} мм</b> ({prefs['size_mm']/10.0:g} см)\n"
        f"• Отступ контура: <b>{prefs['offset_mm']:g} мм</b>\n"
        f"• Резка: <b>{PERF_MODE_LABELS.get(prefs.get('perf_cut_mode', 'none'))}</b>\n"
        f"• Тираж в раскладке: <b>{prefs.get('nesting_qty', 1)} шт.</b>\n\n"
        "💡 <i>Вы также можете просто написать в чат число (например, <code>8.5 см</code> или <code>120 мм</code>), чтобы задать точный нестандартный размер!</i>\n\n"
        f"🔒 <b>Конфиденциальность:</b> файлы обрабатываются на сервере и хранятся {retention_days_str} ({int(JOB_RETENTION_HOURS)} ч.). "
        "Администратор может просматривать их для обработки заказа. "
        "Подробнее: /privacy | Удалить данные: /delete_my_data"
    )
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📦 База стикерпаков и наклеек", callback_data="harv:home"),
        InlineKeyboardButton("📂 По категориям", callback_data="harv:cats"),
    )
    if is_admin(user.id):
        kb.row(
            InlineKeyboardButton("🔥 Заказать тему вне очереди", callback_data="harv:order_prompt"),
            InlineKeyboardButton("👑 Админ-панель (/admin)", callback_data="adm:home"),
        )
    kb.row(
        InlineKeyboardButton("🔒 Конфиденциальность", callback_data="user:privacy"),
        InlineKeyboardButton("🗑 Удалить данные", callback_data="user:delete_my_data"),
    )
    bot.reply_to(message, text, reply_markup=kb)


def handle_privacy(message: Message) -> None:
    bot = _get_bot()
    user = message.from_user
    _record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/privacy",
    )
    if _is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    text = format_privacy_text()
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🗑 Удалить мои данные", callback_data="user:delete_my_data"),
    )
    bot.reply_to(message, text, reply_markup=kb)


def handle_delete_my_data(message: Message) -> None:
    bot = _get_bot()
    user = message.from_user
    if _is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    res = delete_user_data(user.id)
    cnt = res.get("deleted_jobs", 0)
    jobs_info = f"<b>{cnt} шт.</b>" if cnt > 0 else "нет активных файлов"
    text = (
        "🗑 <b>Все ваши персональные данные успешно удалены:</b>\n\n"
        f"• Макеты и задания на сервере: {jobs_info}\n"
        "• Профиль пользователя и история действий удалены\n"
        "• Пользовательские настройки сброшены\n\n"
        "Ваши файлы больше не хранятся на сервере. При следующей отправке макета профиль будет создан заново."
    )
    bot.reply_to(message, text)


def parse_size_text(text: str) -> Tuple[float, ...]:
    """Разбирает текстовое описание размера стикера или габарита ШxВ в миллиметры.

    Возвращает:
      - (w_mm, h_mm) для прямоугольных габаритов вида '20x10 см', '150x90 мм', '2x1.5 м'
      - (size_mm,) для одиночного размера вида '15 см', '150 мм', '2 м'
      - () если текст не является корректным описанием размера
    """
    raw_txt = (text or "").strip()
    txt = raw_txt.lower()
    if txt.startswith("/"):
        return ()

    # 1. Проверяем ввод вида ШxВ (например: '12x8 см', '12х8', '150x90 мм', '2x1.5 м')
    m_rect = re.match(
        r"^(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*(см|cm|мм|mm|м|m)?$",
        txt,
    )
    if m_rect:
        w_val = float(m_rect.group(1).replace(",", "."))
        h_val = float(m_rect.group(2).replace(",", "."))
        unit = m_rect.group(3) or "см"
        if unit in ("м", "m"):
            w_mm, h_mm = w_val * 1000.0, h_val * 1000.0
        elif unit in ("см", "cm"):
            w_mm, h_mm = w_val * 100.0, h_val * 100.0  # corrected logic below
            # unit: if 'м' -> val * 1000.0, if 'см' -> val * 10.0
        if unit in ("м", "m"):
            w_mm, h_mm = w_val * 1000.0, h_val * 1000.0
        elif unit in ("см", "cm"):
            w_mm, h_mm = w_val * 10.0, h_val * 10.0
        else:
            w_mm, h_mm = w_val, h_val
        w_mm = max(5.0, round(w_mm, 1))
        h_mm = max(5.0, round(h_mm, 1))
        return (w_mm, h_mm)

    # 2. Обычный ввод одной стороны ('8.5 см', '120 мм', '2.5 м')
    m = re.match(r"^(\d+(?:[.,]\d+)?)\s*(см|cm|мм|mm|м|m)?$", txt)
    if m:
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2) or "см"
        if unit in ("м", "m"):
            size_mm = val * 1000.0
        elif unit in ("см", "cm"):
            size_mm = val * 10.0
        else:
            size_mm = val
        size_mm = max(5.0, round(size_mm, 1))
        return (size_mm,)

    return ()


def handle_custom_size_text(message: Message) -> None:
    """Позволяет написать в чат размер ('8.5 см', '120 мм' или '12x8 см') или срочную тему (для админа)."""
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    limiter = _get_limiter()
    do_process = _get_process_and_send_job()

    raw_txt = (message.text or "").strip()
    txt = raw_txt.lower()
    if txt.startswith("/"):
        return
    user = message.from_user
    if _is_user_banned(user.id):
        return

    if is_admin(user.id) and check_and_consume_waiting_topic(user.id):
        enqueue_and_run_urgent_topic(bot, message.chat.id, raw_txt)
        return

    parsed = parse_size_text(raw_txt)

    if len(parsed) == 2:
        w_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[0]))
        h_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[1]))
        size_mm = max(w_mm, h_mm)
        uinfo = _record_user_activity(
            user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            action=f"Задан размер {w_mm:g}×{h_mm:g} мм",
        )
        last_jid = uinfo.get("last_job_id", "")
        job_dir = jobs_dir / last_jid if last_jid else None

        sx, sy = w_mm / size_mm, h_mm / size_mm
        if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
            meta = storage_get_job(last_jid) or load_json_file(job_dir / "meta.json", {})
            orig_w = float(meta.get("single_w_mm") or meta.get("width_mm") or 100.0)
            orig_h = float(meta.get("single_h_mm") or meta.get("height_mm") or 100.0)
            curr_sx = float(meta.get("stretch_x", 1.0))
            curr_sy = float(meta.get("stretch_y", 1.0))
            base_w = orig_w / max(0.05, curr_sx)
            base_h = orig_h / max(0.05, curr_sy)
            if base_w > 1 and base_h > 1:
                base_longest = max(base_w, base_h)
                base_w_at_sz = (base_w / base_longest) * size_mm
                base_h_at_sz = (base_h / base_longest) * size_mm
                sx = w_mm / max(1.0, base_w_at_sz)
                sy = h_mm / max(1.0, base_h_at_sz)

        sx = max(MIN_STRETCH, min(MAX_STRETCH, round(sx, 4)))
        sy = max(MIN_STRETCH, min(MAX_STRETCH, round(sy, 4)))

        _update_user_prefs(
            user.id,
            size_mm=size_mm,
            stretch_x=sx,
            stretch_y=sy,
            fit_a4_sheet=False,
        )
        if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
            meta = storage_get_job(last_jid) or load_json_file(job_dir / "meta.json", {})
            if not is_admin(user.id) and int(meta.get("user_id", 0)) != user.id:
                bot.reply_to(message, "⛔ Это не ваш макет.")
                return

            meta["size_mm"] = size_mm
            meta["stretch_x"] = sx
            meta["stretch_y"] = sy
            meta["fit_a4_sheet"] = False
            save_json_file(job_dir / "meta.json", meta)
            storage_update_job(last_jid, size_mm=size_mm, stretch_x=sx, stretch_y=sy, fit_a4_sheet=0)

            if limiter:
                can_convert, limit_reason = limiter.check_and_acquire_user_slot(user.id)
                if not can_convert:
                    if limit_reason == "concurrent":
                        bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
                    else:
                        bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
                    return

            status_msg = bot.reply_to(
                message,
                f"📐 Растягиваю макет под габарит <b>~{w_mm/10.0:g} × {h_mm/10.0:g} см</b>...",
            )

            def on_queue_rect() -> None:
                try:
                    bot.edit_message_text(
                        "⏳ <b>Сервер занят.</b> Макет в очереди на обработку...",
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id,
                    )
                except Exception:
                    pass

            if limiter and hasattr(limiter, "run_conversion_slot"):
                with limiter.run_conversion_slot(user.id, on_queued=on_queue_rect):
                    do_process(
                        chat_id=message.chat.id,
                        user_id=user.id,
                        job_dir=job_dir,
                        status_msg_id=status_msg.message_id,
                        was_compressed_photo=False,
                        action_title=f"Габарит {w_mm/10.0:g}×{h_mm/10.0:g} см",
                    )
            else:
                do_process(
                    chat_id=message.chat.id,
                    user_id=user.id,
                    job_dir=job_dir,
                    status_msg_id=status_msg.message_id,
                    was_compressed_photo=False,
                    action_title=f"Габарит {w_mm/10.0:g}×{h_mm/10.0:g} см",
                )
        else:
            bot.reply_to(
                message,
                f"✅ Задан габарит: <b>{w_mm/10.0:g} × {h_mm/10.0:g} см</b>. Теперь отправьте картинку!",
            )
        return

    if not parsed:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("📦 Открыть базу стикерпаков", callback_data="harv:home"),
            InlineKeyboardButton("📂 По категориям", callback_data="harv:cats"),
        )
        if is_admin(user.id):
            kb.row(InlineKeyboardButton("🔥 Заказать тему вне очереди", callback_data="harv:order_prompt"))
        bot.reply_to(
            message,
            "📎 <b>Онлайн-конвертер:</b> отправьте любую картинку (фото или файл без сжатия), чтобы получить готовый <code>.eps</code> с контуром реза.\n"
            "📐 <b>Размер макета:</b> напишите желаемый размер, например: <code>8.5 см</code>, <code>120 мм</code>, <code>1.2 м</code> или <code>12x8 см</code> (до 1370 мм).\n"
            "📦 <b>Готовые стикерпаки:</b> нажмите кнопку ниже, чтобы выбрать готовые макеты из базы!",
            reply_markup=kb,
        )
        return

    size_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[0]))

    _update_user_prefs(user.id, size_mm=size_mm, fit_a4_sheet=False)
    uinfo = _record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action=f"Задан размер {size_mm:g} мм",
    )

    last_jid = uinfo.get("last_job_id", "")
    job_dir = jobs_dir / last_jid if last_jid else None
    if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
        meta = storage_get_job(last_jid) or load_json_file(job_dir / "meta.json", {})
        if not is_admin(user.id) and int(meta.get("user_id", 0)) != user.id:
            bot.reply_to(message, "⛔ Это не ваш макет.")
            return

        meta["size_mm"] = size_mm
        meta["fit_a4_sheet"] = False
        save_json_file(job_dir / "meta.json", meta)
        storage_update_job(last_jid, size_mm=size_mm, fit_a4_sheet=0)

        if limiter:
            can_convert, limit_reason = limiter.check_and_acquire_user_slot(user.id)
            if not can_convert:
                if limit_reason == "concurrent":
                    bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
                else:
                    bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
                return

        status_msg = bot.reply_to(
            message,
            f"📐 Установлен размер <b>{size_mm:g} мм ({size_mm/10.0:g} см)</b>. Пересчитываю ваш последний макет...",
        )

        def on_queue_single() -> None:
            try:
                bot.edit_message_text(
                    "⏳ <b>Сервер занят.</b> Макет в очереди на обработку...",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        if limiter and hasattr(limiter, "run_conversion_slot"):
            with limiter.run_conversion_slot(user.id, on_queued=on_queue_single):
                do_process(
                    chat_id=message.chat.id,
                    user_id=user.id,
                    job_dir=job_dir,
                    status_msg_id=status_msg.message_id,
                    was_compressed_photo=False,
                    action_title=f"Размер {size_mm:g} мм",
                )
        else:
            do_process(
                chat_id=message.chat.id,
                user_id=user.id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=False,
                action_title=f"Размер {size_mm:g} мм",
            )
    else:
        bot.reply_to(
            message,
            f"✅ Установлен размер по умолчанию: <b>{size_mm:g} мм ({size_mm/10.0:g} см)</b>.\nТеперь отправьте картинку!",
        )


def register_admin_handlers(bot: Any) -> None:
    """Регистрирует административные и служебные обработчики в боте."""
    bot.register_message_handler(handle_admin_command, commands=["admin", "stats"])
    bot.register_message_handler(handle_spy_command, commands=["spy", "live_spy"])
    bot.register_callback_query_handler(
        handle_admin_callback,
        func=lambda call: call.data and call.data.startswith("adm:"),
    )
    bot.register_message_handler(handle_start, commands=["start", "help"])
    bot.register_message_handler(handle_privacy, commands=["privacy"])
    bot.register_message_handler(handle_delete_my_data, commands=["delete_my_data"])
    bot.register_message_handler(handle_custom_size_text, content_types=["text"])
