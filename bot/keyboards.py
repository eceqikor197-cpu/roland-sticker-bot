"""Модуль генерации инлайн-клавиатур и представлений Telegram-бота Roland VP-540.

Содержит:
- build_job_keyboard: меню управления макетом (размер, кант, перфорация, раскладка А4/рулон)
- build_admin_home_view: главная сводка админ-панели
- build_admin_users_view: список пользователей с кнопками управления
"""

from __future__ import annotations

import html
from typing import Any, Dict, Optional, Tuple

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    ADMIN_IDS,
    HOT_QUEUE_EPS_DIR,
    LIVE_SPY_DEFAULT,
    WEB_PORT,
)
from storage import (
    format_user_trigger as storage_format_user_trigger,
    get_admin_state as storage_get_admin_state,
    get_all_users as storage_get_all_users,
    get_user_display as storage_get_user_display,
    get_user_prefs as storage_get_user_prefs,
    list_all_jobs as storage_list_all_jobs,
)
from web.auth import get_public_web_url
from web.pages import format_ts


def is_admin(user_id: int) -> bool:
    """Проверяет, входит ли пользователь в список администраторов бота."""
    return int(user_id) in ADMIN_IDS


def _get_allow_user_hot_folder() -> bool:
    try:
        import telegram_bot

        return bool(getattr(telegram_bot, "ALLOW_USER_HOT_FOLDER", False))
    except Exception:
        return False


def _get_hot_queue_dir():
    try:
        import telegram_bot

        return getattr(telegram_bot, "HOT_QUEUE_EPS_DIR", HOT_QUEUE_EPS_DIR)
    except Exception:
        return HOT_QUEUE_EPS_DIR


def build_job_keyboard(
    job_id: str,
    prefs: Dict[str, Any],
    in_hot_folder: bool = False,
    a4_fit_qty: int = 1,
    menu_tab: Optional[str] = None,
    hot_request_status: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Строит многоуровневую инлайн-клавиатуру настройки макета."""
    offset_mm = float(prefs.get("offset_mm", 2.5))
    size_mm = float(prefs.get("size_mm", 100.0))
    keep_bg = bool(prefs.get("keep_background", False))
    perf_mode = str(prefs.get("perf_cut_mode", "none"))
    qty = int(prefs.get("nesting_qty", 1))
    fit_a4 = bool(prefs.get("fit_a4_sheet", False))
    sx = float(prefs.get("stretch_x", 1.0))
    sy = float(prefs.get("stretch_y", 1.0))
    active_tab = menu_tab or str(prefs.get("menu_tab", "main"))

    kb = InlineKeyboardMarkup(row_width=2)

    # ПОДМЕНЮ 1: РАЗМЕР МАКЕТА
    if active_tab == "size":
        kb.row(
            InlineKeyboardButton(
                f"📏 Сейчас: {size_mm/10.0:g} см ({size_mm:g} мм)",
                callback_data=f"job:{job_id}:noop:0",
            )
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 0.5 см", callback_data=f"job:{job_id}:dsz:-5"),
            InlineKeyboardButton("➕ Увеличить 0.5 см", callback_data=f"job:{job_id}:dsz:5"),
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 1 см", callback_data=f"job:{job_id}:dsz:-10"),
            InlineKeyboardButton("➕ Увеличить 1 см", callback_data=f"job:{job_id}:dsz:10"),
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 5 см", callback_data=f"job:{job_id}:dsz:-50"),
            InlineKeyboardButton("➕ Увеличить 5 см", callback_data=f"job:{job_id}:dsz:50"),
        )

        row_s1 = []
        for s_val, s_lbl in [(50.0, "5 см"), (70.0, "7 см"), (100.0, "10 см")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s1.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s1)

        row_s2 = []
        for s_val, s_lbl in [(150.0, "15 см"), (210.0, "А5 (21см)"), (297.0, "А4 (30см)")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s2.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s2)

        row_s3 = []
        for s_val, s_lbl in [(500.0, "50 см"), (1000.0, "100 см (1м)"), (1370.0, "137 см")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s3.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s3)

        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # ПОДМЕНЮ 2: РАСТЯЖЕНИЕ И СЖАТИЕ ПО ОСЯМ
    if active_tab == "stretch":
        kb.row(
            InlineKeyboardButton(
                f"📐 Пропорции сейчас: Ш ×{sx:.2g} | В ×{sy:.2g}",
                callback_data=f"job:{job_id}:noop:0",
            )
        )
        kb.row(
            InlineKeyboardButton("↔️ Сжать ширину (-10%)", callback_data=f"job:{job_id}:stx:0.9"),
            InlineKeyboardButton("↔️ Растянуть вширь (+10%)", callback_data=f"job:{job_id}:stx:1.1"),
        )
        kb.row(
            InlineKeyboardButton("↕️ Сжать высоту (-10%)", callback_data=f"job:{job_id}:sty:0.9"),
            InlineKeyboardButton("↕️ Растянуть вверх (+10%)", callback_data=f"job:{job_id}:sty:1.1"),
        )
        kb.row(
            InlineKeyboardButton("↔️ Ширина -25%", callback_data=f"job:{job_id}:stx:0.75"),
            InlineKeyboardButton("↔️ Ширина +25%", callback_data=f"job:{job_id}:stx:1.25"),
        )
        kb.row(
            InlineKeyboardButton("↕️ Высота -25%", callback_data=f"job:{job_id}:sty:0.75"),
            InlineKeyboardButton("↕️ Высота +25%", callback_data=f"job:{job_id}:sty:1.25"),
        )
        kb.row(
            InlineKeyboardButton("🔄 Сбросить пропорции 1:1", callback_data=f"job:{job_id}:rst:1")
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # ПОДМЕНЮ 3: КОНТУР РЕЗА И ОТСТУП КАНТА
    if active_tab == "cut":
        perf_passes = int(prefs.get("perf_cut_passes", 2))
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 0.0) < 0.05 else ''}Без канта (0 мм)",
                callback_data=f"job:{job_id}:off:0.0",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 1.5) < 0.05 else ''}Тонкий кант (1.5 мм)",
                callback_data=f"job:{job_id}:off:1.5",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 2.5) < 0.05 else ''}Стандарт (2.5 мм)",
                callback_data=f"job:{job_id}:off:2.5",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 4.0) < 0.05 else ''}Широкий кант (4 мм)",
                callback_data=f"job:{job_id}:off:4.0",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'none' and not fit_a4 else ''}✂️ Только до подложки (CutContour)",
                callback_data=f"job:{job_id}:perf:none",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'sheet_frame' or fit_a4 else ''}📦 До подложки + Сквозная рамка листа",
                callback_data=f"job:{job_id}:perf:sheet_frame",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'die_cut' else ''}🔪 До подложки + Вырубка по контуру насквозь",
                callback_data=f"job:{job_id}:perf:die_cut",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes == 1 else ''}🔁 Сквозной: 1 проход",
                callback_data=f"job:{job_id}:ppass:1",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes == 2 else ''}🔥 Сквозной: 2 прохода (Дорез)",
                callback_data=f"job:{job_id}:ppass:2",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes >= 3 else ''}💥 Сквозной: 3 прохода (Толстая плёнка / Ламинат)",
                callback_data=f"job:{job_id}:ppass:3",
            )
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # ПОДМЕНЮ 4: РАСКЛАДКА НА ЛИСТ А4 И ТИРАЖ НА РУЛОН
    if active_tab == "layout":
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if fit_a4 else ''}📄 Заполнить лист А4 ({a4_fit_qty} шт на листе)",
                callback_data=f"job:{job_id}:a4:{'0' if fit_a4 else '1'}",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 1) else ''}1️⃣ Одиночный макет (1 штука)",
                callback_data=f"job:{job_id}:qty:1",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 10) else ''}📦 На рулон: 10 штук",
                callback_data=f"job:{job_id}:qty:10",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 25) else ''}📦 На рулон: 25 штук",
                callback_data=f"job:{job_id}:qty:25",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 50) else ''}📦 На рулон: 50 штук",
                callback_data=f"job:{job_id}:qty:50",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 100) else ''}📦 На рулон: 100 штук",
                callback_data=f"job:{job_id}:qty:100",
            ),
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # ГЛАВНОЕ МЕНЮ МАКЕТА
    stretch_badge = (
        f" ({sx:.2g}×{sy:.2g})"
        if (abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02)
        else ""
    )
    qty_badge = f"А4 ({a4_fit_qty} шт)" if fit_a4 else f"{qty} шт"

    kb.row(
        InlineKeyboardButton(
            f"📐 Размер ({size_mm/10.0:g} см) ▸",
            callback_data=f"job:{job_id}:nav:size",
        ),
        InlineKeyboardButton(
            f"↔️ Сжать / Растянуть{stretch_badge} ▸",
            callback_data=f"job:{job_id}:nav:stretch",
        ),
    )

    kb.row(
        InlineKeyboardButton(
            f"✂️ Контур и Кант ({offset_mm:g} мм) ▸",
            callback_data=f"job:{job_id}:nav:cut",
        ),
        InlineKeyboardButton(
            f"📦 Тираж: {qty_badge} ▸",
            callback_data=f"job:{job_id}:nav:layout",
        ),
    )

    a4_btn_lbl = (
        f"✅ Собрано на лист А4 ({a4_fit_qty} шт) — Вернуть 1 шт"
        if fit_a4
        else f"📄 Разложить на весь лист А4 ({a4_fit_qty} шт)"
    )
    kb.row(
        InlineKeyboardButton(a4_btn_lbl, callback_data=f"job:{job_id}:a4:{'0' if fit_a4 else '1'}")
    )

    bg_toggle_val = "0" if keep_bg else "1"
    bg_lbl = (
        "🔲 Фон: Оставлен (нажмите, чтобы вырезать)"
        if keep_bg
        else "✂️ Фон: Вырезан (нажмите, чтобы оставить)"
    )
    kb.row(InlineKeyboardButton(bg_lbl, callback_data=f"job:{job_id}:bg:{bg_toggle_val}"))

    allow_hot = _get_allow_user_hot_folder()
    if in_hot_folder:
        hot_lbl = "✅ Уже в Hot Folder плоттера (отправить ещё раз)"
    elif hot_request_status == "pending":
        hot_lbl = "⏳ Заявка ожидает подтверждения админа"
    elif not allow_hot:
        hot_lbl = "🚀 Запросить печать на плоттере (Hot Folder)"
    else:
        hot_lbl = "🚀 Отправить на ПК с плоттером (Hot Folder)"
    kb.row(InlineKeyboardButton(hot_lbl, callback_data=f"job:{job_id}:hot:1"))

    return kb


def build_admin_home_view() -> Tuple[str, InlineKeyboardMarkup]:
    """Формирует текст сводки и инлайн-клавиатуру главного экрана /admin."""
    users = storage_get_all_users()
    jobs = storage_list_all_jobs()
    state = storage_get_admin_state()
    spy_on = state.get("live_spy_enabled", LIVE_SPY_DEFAULT)
    hot_queue_dir = _get_hot_queue_dir()
    hot_files = (
        sorted(hot_queue_dir.glob("*.eps"), key=lambda p: p.stat().st_mtime, reverse=True)
        if hot_queue_dir.exists()
        else []
    )

    total_contours = sum(int(j.get("num_contours", 1)) for j in jobs)
    total_mb = sum(float(j.get("eps_size_mb", 0.0)) for j in jobs)
    public_url = get_public_web_url()

    lines = [
        "👑 <b>Админ-панель Plotter EPS Bot (сервер crybaby)</b>\n",
        f"👥 Пользователей в боте: <b>{len(users)}</b>",
        f"🗂 Всего сконвертировано макетов: <b>{len(jobs)}</b> ({total_mb:.1f} МБ)",
        f"✂️ Построено контуров <code>CutContour</code>: <b>{total_contours} шт.</b>",
        f"🖨 В сетевой папке Hot Folder плоттера: <b>{len(hot_files)} файл(ов)</b>",
        f"🔔 Live-уведомления о чужих работах: <b>{'🟢 ВКЛ' if spy_on else '🔴 ВЫКЛ'}</b>\n",
        "🌐 <b>Веб-Дашборд и База Стикеров (доступ из любой точки):</b>",
    ]
    if public_url:
        lines.append(
            f"• 🌍 <b>Онлайн HTTPS (без VPN, для всех):</b> <a href=\"{html.escape(public_url)}/harvester\">{html.escape(public_url)}/harvester</a>"
        )
    lines.extend(
        [
            f"• 🦊 <b>Tailscale:</b> <code>http://[fd7a:115c:a1e0::b62e:d84a]:{WEB_PORT}/harvester</code>",
            f"• 🦊 <b>Tailscale IPv4:</b> <code>http://100.76.216.73:{WEB_PORT}/harvester</code>",
            f"• 🏠 <b>По локалке:</b> <code>http://192.168.0.115:{WEB_PORT}/harvester</code>",
            "• 🔐 <b>Вход в веб-админку:</b> используйте пароль администратора\n",
            "📂 <b>Сетевая папка для ПК с плоттером (Samba):</b>",
            "• Windows: <code>\\\\192.168.0.115\\Roland_HotFolder</code> (или <code>\\\\100.76.216.73\\Roland_HotFolder</code>)",
            "• Mac: <code>smb://192.168.0.115/Roland_HotFolder</code>\n",
            "<b>Последние действия в боте:</b>",
        ]
    )

    for j in jobs[:6]:
        uid = int(j.get("user_id", 0))
        udisp = storage_get_user_display(uid)
        ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
        nc = j.get("num_contours", 1)
        qty = j.get("nesting_qty", 1)
        w_mm = j.get("width_mm", 100)
        h_mm = j.get("height_mm", 100)
        lines.append(
            f"• <code>{ts_str}</code> — {udisp}: <code>{html.escape(j.get('orig_stem', 'file'))}.eps</code> ({w_mm:g}×{h_mm:g}мм, {qty}шт, {nc} конт.)"
        )

    kb = InlineKeyboardMarkup()
    if public_url:
        kb.row(
            InlineKeyboardButton(
                "🌐 Веб-База Стикеров (Админ)",
                url=f"{public_url}/harvester",
            ),
            InlineKeyboardButton(
                "👑 Веб-Панель Работ (Админ)",
                url=f"{public_url}/",
            ),
        )
    kb.row(
        InlineKeyboardButton("🚜 Парсер Базы 24/7 (Google Drive 5 ТБ)", callback_data="harv:home"),
    )
    kb.row(
        InlineKeyboardButton(f"🖼 Смотреть работы ({len(jobs)})", callback_data="adm:feed:0:all"),
        InlineKeyboardButton(f"👥 Пользователи ({len(users)})", callback_data="adm:users"),
    )
    kb.row(
        InlineKeyboardButton(
            f"🔔 Live-шпион: {'ВКЛ 🟢' if spy_on else 'ВЫКЛ 🔴'}",
            callback_data="adm:toggle_spy",
        ),
        InlineKeyboardButton("🔄 Обновить", callback_data="adm:home"),
    )
    return "\n".join(lines), kb


def build_admin_users_view() -> Tuple[str, InlineKeyboardMarkup]:
    """Формирует список пользователей для просмотра и управления админом."""
    users = storage_get_all_users()
    state = storage_get_admin_state()
    banned_set = {int(x) for x in state.get("banned_users", [])}

    lines = ["👥 <b>Пользователи бота:</b>\n"]
    kb = InlineKeyboardMarkup()

    sorted_users = sorted(
        users.values(),
        key=lambda u: float(u.get("last_seen", 0)),
        reverse=True,
    )

    for u in sorted_users:
        uid = int(u["user_id"])
        udisp = storage_format_user_trigger(u)
        jcount = len(storage_list_all_jobs(filter_user_id=uid))
        last_str = format_ts(float(u.get("last_seen", 0)))
        last_act = html.escape(str(u.get("last_action", "—")))
        role_badge = "👑 Админ" if is_admin(uid) else ("🚫 Заблокирован" if uid in banned_set else "🟢 Активен")
        prefs = storage_get_user_prefs(uid)

        lines.append(
            f"• {udisp} [{role_badge}]\n"
            f"  └ Работ: <b>{jcount}</b> | Посл. раз: <code>{last_str}</code>\n"
            f"  └ Настройки: размер <b>{prefs['size_mm']:g}мм</b>, кант <b>{prefs['offset_mm']:g}мм</b>, тираж <b>{prefs.get('nesting_qty', 1)}шт</b>\n"
            f"  └ Действие: <i>{last_act}</i>\n"
        )

        fn_short = (u.get("first_name") or str(uid))[:14]
        row_btns = [
            InlineKeyboardButton(
                f"🗂 Работы {fn_short} ({jcount})",
                callback_data=f"adm:feed:0:{uid}",
            )
        ]
        if not is_admin(uid):
            ban_lbl = "✅ Разбанить" if uid in banned_set else "🚫 Забанить"
            row_btns.append(
                InlineKeyboardButton(ban_lbl, callback_data=f"adm:ban:{uid}")
            )
        kb.row(*row_btns)

    kb.row(InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="adm:home"))
    return "\n".join(lines), kb
