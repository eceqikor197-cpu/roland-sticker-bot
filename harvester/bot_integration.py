#!/usr/bin/env python3
"""
Интеграция комбайна Sticker Harvester 24/7 в существующий Telegram-бот (telegram_bot.py)
и веб-панель администратора (http://192.168.0.108:8090).
"""

from __future__ import annotations

import datetime
import html
import threading
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import telebot
from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from harvester.config import (
    HARVESTER_ENABLED,
    RCLONE_REMOTE,
)
from harvester.database import (
    LICENSE_LABELS,
    get_all_state,
    get_asset,
    get_catalog_summary,
    get_conn,
    get_folder_tree,
    get_state,
    init_db,
    list_assets,
    set_state,
    upsert_topic,
)
from harvester.seeds import classify_user_topic, seed_database_if_needed
from harvester.storage.rclone_sync import (
    copy_harvester_asset_to_hot_folder,
    ensure_local_eps_for_asset,
    get_gdrive_quota_info,
    sync_staging_to_gdrive,
)
from harvester.worker import harvest_single_topic

# Состояние ожидания ввода срочной темы от админа: {user_id: timestamp}
_waiting_for_topic_input: Dict[int, float] = {}

ASSET_TYPE_LABELS = {
    "pack_sheet": "📦 Лист-стикерпак",
    "auto_pack_sheet": "🧩 Собранный стикерпак",
    "single_sticker": "✂️ Штучный стикер",
    "car_decal": "🚗 Авто-наклейка",
    "banner": "🪧 Баннер / Постер",
}

CAT1_LABELS = {
    "01_Стикерпаки": "📦 01 Стикерпаки",
    "02_Авто_Наклейки": "🚗 02 Авто-наклейки",
    "03_Одиночные_Стикеры": "✂️ 03 Одиночные стикеры",
    "04_Баннеры_и_Вывески": "🪧 04 Баннеры и вывески",
}

CAT2_ICONS = {
    "Сериалы_и_Кино": "🎬",
    "Кино_и_Сериалы": "🎬",
    "Аниме_и_Манга": "🎌",
    "Аниме": "🎌",
    "Игры_и_Гейминг": "🎮",
    "Мемы_Котики_и_Поп_Культура": "🐸",
    "Мемы_и_Стримеры": "🐸",
    "JDM_и_Японские_Авто": "🇯🇵",
    "Немецкие_и_Европейские_Авто": "🇩🇪",
    "Отечественные_ВАЗ_Lada_Боевая_Классика": "🚘",
    "Приколы_Надписи_и_Инфо_на_Стекло": "⚠️",
    "Эстетика_Арт_и_Дизайн": "🎨",
    "Автобизнес_Гараж_и_Сервис": "🔧",
    "Таблички_Предупреждения_и_Постеры": "🪧",
    "Заказы_Пользователей": "🔥",
}

FOLDERS_PER_PAGE = 10


def format_c1_label(c1: str) -> str:
    return CAT1_LABELS.get(c1, f"📂 {c1.replace('_', ' ')}")


def format_c2_label(c2: str) -> str:
    icon = CAT2_ICONS.get(c2, "📁")
    return f"{icon} {c2.replace('_', ' ')}"


def format_ts_msk(ts: float) -> str:
    if not ts:
        return "—"
    dt = datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone(datetime.timedelta(hours=3))
    )
    return dt.strftime("%d.%m %H:%M:%S")


def build_harvester_home_view(is_admin: bool = False) -> Tuple[str, InlineKeyboardMarkup]:
    if not HARVESTER_ENABLED:
        text = (
            "📦 <b>База Стикерпаков и Макетов (Roland Print & Cut)</b>\n\n"
            "⏸ <b>Модуль отключён</b>\n"
            "Автономный сборщик и каталог отключены в конфигурации сервера (<code>HARVESTER_ENABLED=false</code>)."
        )
        kb = InlineKeyboardMarkup()
        if is_admin:
            kb.row(InlineKeyboardButton("👑 Панель администратора", callback_data="admin:refresh"))
        return text, kb

    init_db()
    st = get_all_state()
    summary = get_catalog_summary()
    totals = summary.get("totals", {})
    tstat = summary.get("topics_stat", {})

    enabled = bool(st.get("enabled", True))
    hb = float(st.get("last_heartbeat", 0))
    worker_alive = (time.time() - hb) < 120.0
    status_badge = (
        "🟢 РАБОТАЕТ 24/7"
        if (enabled and worker_alive)
        else ("🟡 ОЖИДАНИЕ ВОРКЕРА" if enabled else "⏸ НА ПАУЗЕ")
    )

    total_assets = int(totals.get("total_assets") or 0)
    total_packs = int(totals.get("total_packs") or 0)
    total_singles = int(totals.get("total_singles") or 0)
    total_cars = int(totals.get("total_car_decals") or 0)
    total_banners = int(totals.get("total_banners") or 0)
    synced_cnt = int(totals.get("synced_count") or 0)
    total_mb = float(totals.get("total_eps_mb") or 0.0)
    total_contours = int(totals.get("total_contours") or 0)

    rej_mockup = int(st.get("total_rejected_mockup") or 0)
    rej_lowres = int(st.get("total_rejected_lowres") or 0)
    rej_dup = int(st.get("total_rejected_dup") or 0)

    lines = [
        "📦 <b>База Стикерпаков, Наклеек и Макетов (Roland Print & Cut)</b>\n",
        f"📡 Статус авто-пополнения: <b>{status_badge}</b>",
        f"🎯 Сейчас собирается тема: <code>{html.escape(str(st.get('current_topic', '—')))}</code>",
        f"📂 Категория: <code>{html.escape(str(st.get('current_category', '—')))}</code>\n",
        "📊 <b>Доступно готовых макетов в базе (.EPS с контуром реза + оригинал):</b>",
        f"• Всего макетов: <b>{total_assets} шт.</b> ({total_mb:.1f} МБ, ✂️ {total_contours} конт.)",
        f"  ├ 📦 Цельных листов-стикерпаков: <b>{total_packs}</b>",
        f"  ├ ✂️ Одиночных стикеров: <b>{total_singles}</b>",
        f"  ├ 🚗 Автомобильных наклеек: <b>{total_cars}</b>",
        f"  └ 🪧 Баннеров и вывесок: <b>{total_banners}</b>\n",
        "🛡 <b>Контроль качества:</b>",
        f"• Отсеяно грязных мокапов/фото: <b>{rej_mockup}</b> | мелких: <b>{rej_lowres}</b> | дублей: <b>{rej_dup}</b>",
        f"• Темы в каталоге: <b>{int(tstat.get('harvested_topics') or 0)} / {int(tstat.get('total_topics') or 0)}</b> (выгружено в облако: <b>{synced_cnt}</b>)",
    ]

    recent = summary.get("recent_folders", [])[:5]
    if recent:
        lines.append("\n<b>📂 Недавно пополненные папки:</b>")
        for rf in recent:
            lines.append(
                f"• <code>{html.escape(rf['category_lvl1'])}/{html.escape(rf['category_lvl2'])}/{html.escape(rf['folder_slug'])}</code> — <b>{rf['assets_cnt']} мак.</b>"
            )

    public_url = str(st.get("public_web_url") or "").strip().rstrip("/")
    if public_url:
        lines.append(f"\n🌐 <b>Онлайн Веб-Каталог (с любого устройства):</b>\n<a href=\"{html.escape(public_url)}/harvester\">{html.escape(public_url)}/harvester</a>")

    kb = InlineKeyboardMarkup()
    if public_url:
        if is_admin:
            kb.row(
                InlineKeyboardButton(
                    "🌐 Открыть Веб-Дашборд Базы (Админ)",
                    url=f"{public_url}/harvester",
                )
            )
        else:
            kb.row(
                InlineKeyboardButton(
                    "🌐 Открыть Веб-Каталог в браузере",
                    url=f"{public_url}/harvester",
                )
            )
    kb.row(
        InlineKeyboardButton("📂 Дерево папок и категорий", callback_data="harv:cats"),
    )
    kb.row(
        InlineKeyboardButton(f"🖼 Смотреть все макеты подряд ({total_assets})", callback_data="harv:browse:0:all"),
    )
    if is_admin:
        kb.row(
            InlineKeyboardButton("🔥 Заказать тему вне очереди", callback_data="harv:order_prompt"),
        )
        kb.row(
            InlineKeyboardButton(
                "⏸ Поставить на паузу" if enabled else "▶️ Запустить парсер 24/7",
                callback_data="harv:toggle",
            ),
            InlineKeyboardButton("☁️ Синхр. Google Drive", callback_data="harv:sync_now"),
        )
        kb.row(
            InlineKeyboardButton("🔄 Обновить", callback_data="harv:home"),
            InlineKeyboardButton("👑 Админ-панель", callback_data="adm:home"),
        )
    else:
        kb.row(
            InlineKeyboardButton("🔄 Обновить каталог", callback_data="harv:home"),
        )
    return "\n".join(lines), kb


def build_harvester_categories_view() -> Tuple[str, InlineKeyboardMarkup]:
    """Уровень 1 дерева: показывает категории 1-го уровня и быстрый вход в подразделы 2-го уровня."""
    tree = get_folder_tree()
    qinfo = get_gdrive_quota_info()

    total_folders = sum(c["folders_count"] for c in tree)
    total_files = sum(c["files_count"] for c in tree)

    lines = [
        "📂 <b>Дерево категорий и папок базы:</b>",
        f"☁️ Облако Google Drive: свободно <b>{html.escape(qinfo.get('free', '4.73 TiB'))}</b> из <b>{html.escape(qinfo.get('total', '5 TiB'))}</b>",
        f"🗂 Всего готовых папок с макетами: <b>{total_folders} папок</b> (<b>{total_files} макетов .EPS</b>)\n",
        "👇 <b>Выберите категорию или сразу нажмите на нужный раздел ниже, чтобы открыть список папок:</b>",
    ]
    if not tree:
        lines.append("\n<i>Парсер только начал сбор первых папок. Нажмите «Обновить» через минуту!</i>")
    else:
        for c1 in tree:
            c1_lbl = format_c1_label(c1["category_lvl1"])
            lines.append(
                f"\n<b>{html.escape(c1_lbl)}</b> — <b>{c1['folders_count']} пап.</b> ({c1['files_count']} мак., {c1['size_mb']} МБ)"
            )
            for c2 in c1["subcategories"]:
                c2_lbl = format_c2_label(c2["category_lvl2"])
                lines.append(
                    f"  ├ {html.escape(c2_lbl)}: <b>{c2['folders_count']} папок</b> ({c2['files_count']} мак.)"
                )

    kb = InlineKeyboardMarkup()
    # Кнопки категорий 1-го уровня (широкие, по 1 в ряд)
    for c1_idx, c1 in enumerate(tree):
        c1_lbl = format_c1_label(c1["category_lvl1"])
        kb.row(
            InlineKeyboardButton(
                f"{c1_lbl} ▸ ({c1['folders_count']} папок • {c1['files_count']} мак.)",
                callback_data=f"harv:c1:{c1_idx}",
            )
        )

    # Быстрый вход сразу в разделы 2-го уровня (Кино и Сериалы, Аниме, Игры, JDM и т.д.)
    for c1_idx, c1 in enumerate(tree):
        for c2_idx, c2 in enumerate(c1["subcategories"]):
            c2_lbl = format_c2_label(c2["category_lvl2"])
            kb.row(
                InlineKeyboardButton(
                    f"  └ {c2_lbl} ({c2['folders_count']} папок • {c2['files_count']} шт)",
                    callback_data=f"harv:c2:{c1_idx}:{c2_idx}:0",
                )
            )

    kb.row(
        InlineKeyboardButton("⬅️ Назад на главную базы", callback_data="harv:home"),
    )
    return "\n".join(lines), kb


def build_harvester_subcategories_view(c1_idx: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Уровень 2 дерева: список подразделов (category_lvl2) внутри выбранной категории (category_lvl1)."""
    tree = get_folder_tree()
    if not tree or c1_idx < 0 or c1_idx >= len(tree):
        return build_harvester_categories_view()

    c1 = tree[c1_idx]
    c1_lbl = format_c1_label(c1["category_lvl1"])
    lines = [
        f"📂 <b>Дерево папок</b> ▸ <b>{html.escape(c1_lbl)}</b>\n",
        f"• Готовых папок в категории: <b>{c1['folders_count']} шт.</b>",
        f"• Всего макетов .EPS: <b>{c1['files_count']} шт.</b> ({c1['size_mb']} МБ)\n",
        "👇 <b>Выберите раздел, чтобы открыть список конкретных папок:</b>",
    ]
    kb = InlineKeyboardMarkup()
    for c2_idx, c2 in enumerate(c1["subcategories"]):
        c2_lbl = format_c2_label(c2["category_lvl2"])
        sample_folders = ", ".join(f["topic_name"] for f in c2["folders"][:4])
        lines.append(
            f"\n<b>{html.escape(c2_lbl)}</b> — <b>{c2['folders_count']} папок</b> ({c2['files_count']} мак.)\n"
            f"  <i>Примеры папок: {html.escape(sample_folders)}...</i>"
        )
        kb.row(
            InlineKeyboardButton(
                f"{c2_lbl} ▸ {c2['folders_count']} папок ({c2['files_count']} мак.)",
                callback_data=f"harv:c2:{c1_idx}:{c2_idx}:0",
            )
        )

    kb.row(
        InlineKeyboardButton(
            f"👁 Смотреть все макеты категории ({c1['files_count']} шт.)",
            callback_data=f"harv:br:0:{c1_idx}:-1:-1",
        )
    )
    kb.row(
        InlineKeyboardButton("⬅️ Все категории", callback_data="harv:cats"),
        InlineKeyboardButton("🏠 Главная базы", callback_data="harv:home"),
    )
    return "\n".join(lines), kb


def build_harvester_folders_view(
    c1_idx: int, c2_idx: int, page: int = 0
) -> Tuple[str, InlineKeyboardMarkup]:
    """Уровень 3 дерева: постраничный список всех папок (folder_slug) внутри выбранного раздела."""
    tree = get_folder_tree()
    if not tree or c1_idx < 0 or c1_idx >= len(tree):
        return build_harvester_categories_view()
    c1 = tree[c1_idx]
    subcats = c1["subcategories"]
    if not subcats or c2_idx < 0 or c2_idx >= len(subcats):
        return build_harvester_subcategories_view(c1_idx)

    c2 = subcats[c2_idx]
    folders = c2["folders"]
    total_folders = len(folders)
    total_pages = max(1, (total_folders + FOLDERS_PER_PAGE - 1) // FOLDERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start_i = page * FOLDERS_PER_PAGE
    end_i = min(total_folders, start_i + FOLDERS_PER_PAGE)
    page_folders = folders[start_i:end_i]

    c1_lbl = format_c1_label(c1["category_lvl1"])
    c2_lbl = format_c2_label(c2["category_lvl2"])

    lines = [
        f"📂 <b>{html.escape(c1_lbl)}</b> ▸ <b>{html.escape(c2_lbl)}</b>\n",
        f"🗂 Папок в разделе: <b>{total_folders} шт.</b> (всего <b>{c2['files_count']} макетов .EPS</b>, {c2['size_mb']} МБ)",
        f"📄 Страница папок: <b>{page + 1} из {total_pages}</b> (показаны папки {start_i + 1}–{end_i})\n",
        "👇 <b>Нажмите на любую папку, чтобы смотреть и скачивать стикеры внутри неё:</b>",
    ]

    kb = InlineKeyboardMarkup()
    for offset_i, f in enumerate(page_folders):
        f_idx = start_i + offset_i
        tname = f["topic_name"]
        fcnt = f["files_count"]
        pcnt = f["packs_count"]
        scnt = f["singles_count"]
        if pcnt > 0 and scnt > 0:
            detail = f"{fcnt} мак. ({pcnt} пак / {scnt} шт)"
        else:
            detail = f"{fcnt} мак."
        kb.row(
            InlineKeyboardButton(
                f"📁 {tname} — {detail}",
                callback_data=f"harv:br:0:{c1_idx}:{c2_idx}:{f_idx}",
            )
        )

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Пред. папки",
                    callback_data=f"harv:c2:{c1_idx}:{c2_idx}:{page - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                f"Стр. {page + 1}/{total_pages}",
                callback_data="harv:noop",
            )
        )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(
                    "След. папки ➡️",
                    callback_data=f"harv:c2:{c1_idx}:{c2_idx}:{page + 1}",
                )
            )
        kb.row(*nav_row)

    kb.row(
        InlineKeyboardButton(
            f"👁 Смотреть все макеты раздела ({c2['files_count']} шт.)",
            callback_data=f"harv:br:0:{c1_idx}:{c2_idx}:-1",
        )
    )
    kb.row(
        InlineKeyboardButton("⬅️ К разделам категории", callback_data=f"harv:c1:{c1_idx}"),
        InlineKeyboardButton("📂 Все категории", callback_data="harv:cats"),
    )
    return "\n".join(lines), kb


def send_harvester_tree_browse_item(
    bot: telebot.TeleBot,
    chat_id: int,
    index: int,
    c1_idx: int = -1,
    c2_idx: int = -1,
    f_idx: int = -1,
    prev_msg_id: Optional[int] = None,
) -> None:
    """Просмотр макетов внутри конкретной папки (или раздела/категории) по компактным индексам дерева."""
    tree = get_folder_tree()
    c1_name: Optional[str] = None
    c2_name: Optional[str] = None
    folder_slug: Optional[str] = None
    scope_title = "Весь каталог"
    back_cb = "harv:cats"
    back_lbl = "📂 Дерево папок"

    if c1_idx >= 0 and c1_idx < len(tree):
        c1 = tree[c1_idx]
        c1_name = c1["category_lvl1"]
        scope_title = format_c1_label(c1_name)
        back_cb = f"harv:c1:{c1_idx}"
        back_lbl = f"⬅️ Разделы ({c1['category_lvl1'][:14]})"

        subcats = c1["subcategories"]
        if c2_idx >= 0 and c2_idx < len(subcats):
            c2 = subcats[c2_idx]
            c2_name = c2["category_lvl2"]
            scope_title = f"{format_c1_label(c1_name)} ▸ {format_c2_label(c2_name)}"
            back_cb = f"harv:c2:{c1_idx}:{c2_idx}:0"
            back_lbl = f"⬅️ Папки «{c2_name.replace('_', ' ')[:18]}»"

            folders = c2["folders"]
            if f_idx >= 0 and f_idx < len(folders):
                fl = folders[f_idx]
                folder_slug = fl["folder_slug"]
                scope_title = f"{format_c2_label(c2_name)} ▸ 📁 {fl['topic_name']}"
                folder_page = f_idx // FOLDERS_PER_PAGE
                back_cb = f"harv:c2:{c1_idx}:{c2_idx}:{folder_page}"
                back_lbl = f"📂 К списку папок ({c2_name.replace('_', ' ')[:16]})"

    items = list_assets(
        category_lvl1=c1_name,
        category_lvl2=c2_name,
        folder_slug=folder_slug,
        limit=250,
    )
    if not items:
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton(back_lbl, callback_data=back_cb))
        kb.row(InlineKeyboardButton("🏠 Главная базы", callback_data="harv:home"))
        bot.send_message(chat_id, "📭 В этой папке пока нет собранных макетов.", reply_markup=kb)
        return

    index = max(0, min(index, len(items) - 1))
    a = items[index]
    aid = a["asset_id"]
    atype_lbl = ASSET_TYPE_LABELS.get(a["asset_type"], a["asset_type"])
    synced_lbl = "☁️ В облаке" if a.get("synced_to_gdrive") else "⏳ В буфере"

    caption = (
        f"🗂 <b>Макет #{index + 1} из {len(items)}</b> ({atype_lbl})\n"
        f"🧭 Раздел: <b>{html.escape(scope_title)}</b>\n"
        f"📌 Тема: <b>{html.escape(a['topic_name'])}</b>\n"
        f"📂 Путь: <code>{html.escape(a['rel_folder_path'])}</code>\n"
        f"📄 Файл: <code>{html.escape(a['eps_filename'])}</code> ({a['eps_size_mb']:.1f} МБ)\n"
        f"📐 Размер макета: <b>{a['width_mm']:g} × {a['height_mm']:g} мм</b> (исходник {a['width_px']}×{a['height_px']} px)\n"
        f"✂️ Контуров реза: <b>{a['num_contours']} шт.</b> (кант {a['offset_mm']:g} мм)\n"
        f"🌐 Источник: <code>{html.escape(a['source_name'])}</code> • {synced_lbl}"
    )

    kb = InlineKeyboardMarkup()
    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Пред.",
                callback_data=f"harv:br:{index - 1}:{c1_idx}:{c2_idx}:{f_idx}",
            )
        )
    nav.append(InlineKeyboardButton(f"{index + 1} / {len(items)}", callback_data="harv:noop"))
    if index < len(items) - 1:
        nav.append(
            InlineKeyboardButton(
                "След. ➡️",
                callback_data=f"harv:br:{index + 1}:{c1_idx}:{c2_idx}:{f_idx}",
            )
        )
    kb.row(*nav)

    kb.row(
        InlineKeyboardButton("📥 Скачать .EPS", callback_data=f"harv:dl_eps:{aid}"),
        InlineKeyboardButton("🖼 Исходник", callback_data=f"harv:dl_orig:{aid}"),
    )
    kb.row(
        InlineKeyboardButton("🚀 В Hot Folder плоттера", callback_data=f"harv:hot:{aid}"),
    )
    kb.row(
        InlineKeyboardButton(back_lbl, callback_data=back_cb),
    )
    kb.row(
        InlineKeyboardButton("📂 Все категории", callback_data="harv:cats"),
        InlineKeyboardButton("🏠 Главная базы", callback_data="harv:home"),
    )

    if prev_msg_id is not None:
        try:
            bot.delete_message(chat_id, prev_msg_id)
        except Exception:
            pass

    prev_path = Path(a.get("local_preview_path", ""))
    if prev_path.exists():
        with open(prev_path, "rb") as pf:
            bot.send_photo(chat_id, photo=pf, caption=caption, reply_markup=kb)
    else:
        bot.send_message(chat_id, caption, reply_markup=kb)


def send_harvester_browse_item(
    bot: telebot.TeleBot,
    chat_id: int,
    index: int,
    cat_filter: str = "all",
    prev_msg_id: Optional[int] = None,
) -> None:
    if cat_filter != "all":
        tree = get_folder_tree()
        for c1_i, c1 in enumerate(tree):
            if c1["category_lvl1"] == cat_filter:
                send_harvester_tree_browse_item(
                    bot,
                    chat_id=chat_id,
                    index=index,
                    c1_idx=c1_i,
                    c2_idx=-1,
                    f_idx=-1,
                    prev_msg_id=prev_msg_id,
                )
                return

    send_harvester_tree_browse_item(
        bot,
        chat_id=chat_id,
        index=index,
        c1_idx=-1,
        c2_idx=-1,
        f_idx=-1,
        prev_msg_id=prev_msg_id,
    )


def enqueue_and_run_urgent_topic(
    bot: telebot.TeleBot,
    chat_id: int,
    raw_topic_text: str,
) -> None:
    init_db()
    seed_database_if_needed()
    cat1, cat2, clean_name, queries = classify_user_topic(raw_topic_text)
    tid = upsert_topic(cat1, cat2, clean_name, queries, priority=200)

    bot.send_message(
        chat_id,
        f"🔥 <b>Тема «{html.escape(clean_name)}» запущена в сбор!</b>\n\n"
        f"📂 Категория: <code>{html.escape(cat1)} / {html.escape(cat2)} / {html.escape(clean_name)}</code>\n"
        f"🔎 Запросы: <code>{html.escape(', '.join(queries))}</code>\n\n"
        "⏳ <i>Ищу цельные листы-стикерпаки и одиночные стикеры высокого качества, строю контуры реза CutContour — готовые макеты придут сюда через ~20–45 секунд!</i>",
    )

    def _bg_urgent():
        try:
            conn = get_conn()
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (tid,)).fetchone()
            conn.close()
            if row:
                tdict = dict(row)
                tdict["search_queries"] = queries
                harvest_single_topic(tdict, max_sources_accept=4, sync_after=True)
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_bg_urgent, daemon=True).start()


def check_and_consume_waiting_topic(user_id: int) -> bool:
    ts = _waiting_for_topic_input.get(user_id)
    if ts and (time.time() - ts) < 300.0:
        _waiting_for_topic_input.pop(user_id, None)
        return True
    return False


def register_harvester_bot_handlers(
    bot: telebot.TeleBot,
    is_admin_fn: Callable[[int], bool],
    is_banned_fn: Optional[Callable[[int], bool]] = None,
) -> None:
    """Подключает команды /harvester, /parse и колбэки harv:* для пользователей бота."""

    def _check_banned(uid: int) -> bool:
        return bool(is_banned_fn and is_banned_fn(uid))

    def _show_text_view(chat_id: int, msg: Message, text: str, kb: InlineKeyboardMarkup) -> None:
        if msg.content_type == "text":
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg.message_id, reply_markup=kb)
        else:
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            bot.send_message(chat_id, text, reply_markup=kb)

    @bot.message_handler(commands=["harvester", "parser", "base", "stickers", "packs"])
    def handle_harvester_cmd(message: Message) -> None:
        uid = message.from_user.id
        if _check_banned(uid):
            bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
            return
        text, kb = build_harvester_home_view(is_admin=is_admin_fn(uid))
        bot.send_message(message.chat.id, text, reply_markup=kb)

    @bot.message_handler(commands=["parse", "topic"])
    def handle_parse_topic_cmd(message: Message) -> None:
        uid = message.from_user.id
        if not is_admin_fn(uid):
            bot.reply_to(
                message,
                "⛔ Заказ новых тем для парсера доступен только администратору.\n"
                "Вы можете смотреть и скачивать готовые стикерпаки из базы по команде /harvester!",
            )
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            _waiting_for_topic_input[uid] = time.time()
            bot.reply_to(
                message,
                "✍️ <b>Напишите тему для поиска и нарезки стикерпака / наклеек:</b>\n"
                "Например: <code>Пираты Карибского моря</code>, <code>Король и Шут</code>, <code>Toyota Mark 2</code> или <code>Рик и Морти</code>.",
            )
            return
        enqueue_and_run_urgent_topic(bot, message.chat.id, parts[1].strip())

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("harv:"))
    def handle_harv_callback(call: CallbackQuery) -> None:
        uid = call.from_user.id
        if _check_banned(uid):
            bot.answer_callback_query(call.id, "⛔ Доступ ограничен", show_alert=True)
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
                text, kb = build_harvester_home_view(is_admin=is_admin_fn(uid))
                _show_text_view(chat_id, call.message, text, kb)
                return

            if action == "toggle":
                if not is_admin_fn(uid):
                    bot.answer_callback_query(call.id, "⛔ Остановка серверного демона 24/7 доступна только администратору", show_alert=True)
                    return
                curr = bool(get_state("enabled", True))
                new_val = not curr
                set_state("enabled", new_val)
                bot.answer_callback_query(
                    call.id,
                    f"Парсер 24/7: {'ЗАПУЩЕН 🟢' if new_val else 'НА ПАУЗЕ ⏸'}",
                )
                text, kb = build_harvester_home_view(is_admin=True)
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
                return

            if action == "order_prompt":
                if not is_admin_fn(uid):
                    bot.answer_callback_query(call.id, "⛔ Заказ новых тем доступен только администратору", show_alert=True)
                    return
                _waiting_for_topic_input[uid] = time.time()
                bot.answer_callback_query(call.id)
                bot.send_message(
                    chat_id,
                    "🔥 <b>Заказ срочной темы вне очереди:</b>\n"
                    "Просто напишите следующим сообщением в чат название сериала, игры, аниме, марки авто или тематики (например: <code>Пацаны сериал</code>, <code>Subaru WRX</code>, <code>Человек-бензопила</code>):",
                )
                return

            if action == "cats":
                bot.answer_callback_query(call.id)
                text, kb = build_harvester_categories_view()
                _show_text_view(chat_id, call.message, text, kb)
                return

            if action == "c1" and len(parts) >= 3:
                c1_idx = int(parts[2])
                bot.answer_callback_query(call.id)
                text, kb = build_harvester_subcategories_view(c1_idx)
                _show_text_view(chat_id, call.message, text, kb)
                return

            if action == "c2" and len(parts) >= 5:
                c1_idx = int(parts[2])
                c2_idx = int(parts[3])
                page = int(parts[4])
                bot.answer_callback_query(call.id)
                text, kb = build_harvester_folders_view(c1_idx, c2_idx, page)
                _show_text_view(chat_id, call.message, text, kb)
                return

            if action == "br" and len(parts) >= 6:
                idx = int(parts[2])
                c1_idx = int(parts[3])
                c2_idx = int(parts[4])
                f_idx = int(parts[5])
                bot.answer_callback_query(call.id)
                send_harvester_tree_browse_item(
                    bot,
                    chat_id=chat_id,
                    index=idx,
                    c1_idx=c1_idx,
                    c2_idx=c2_idx,
                    f_idx=f_idx,
                    prev_msg_id=call.message.message_id,
                )
                return

            if action == "browse" and len(parts) >= 4:
                idx = int(parts[2])
                cfilter = parts[3]
                bot.answer_callback_query(call.id)
                send_harvester_browse_item(
                    bot,
                    chat_id=chat_id,
                    index=idx,
                    cat_filter=cfilter,
                    prev_msg_id=call.message.message_id,
                )
                return

            if action == "sync_now":
                if not is_admin_fn(uid):
                    bot.answer_callback_query(call.id, "⛔ Только для администратора", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "☁️ Запускаю синхронизацию с Google Drive...")
                ok, cnt, mb, msg = sync_staging_to_gdrive()
                bot.send_message(
                    chat_id,
                    f"{'✅' if ok else '⚠️'} <b>Синхронизация с Google Drive (<code>{html.escape(RCLONE_REMOTE)}</code>):</b>\n"
                    f"Выгружено новых макетов: <b>{cnt} шт.</b> ({mb:.1f} МБ)\n"
                    f"Статус: <i>{html.escape(msg)}</i>",
                )
                return

            if action == "dl_eps" and len(parts) >= 3:
                aid = parts[2]
                bot.answer_callback_query(call.id, "📥 Подготавливаю .EPS файл...")
                eps_path = ensure_local_eps_for_asset(aid)
                asset = get_asset(aid)
                if eps_path is None or not eps_path.exists() or not asset:
                    bot.send_message(chat_id, "⚠️ Файл .EPS не найден ни локально, ни на Google Диске.")
                    return
                with open(eps_path, "rb") as ef:
                    bot.send_document(
                        chat_id,
                        document=ef,
                        visible_file_name=asset["eps_filename"],
                        caption=(
                            f"🖨 Готовый макет из базы: <b>{html.escape(asset['topic_name'])}</b>\n"
                            f"📂 <code>{html.escape(asset['rel_folder_path'])}/{html.escape(asset['eps_filename'])}</code>"
                        ),
                    )
                return

            if action == "dl_orig" and len(parts) >= 3:
                aid = parts[2]
                asset = get_asset(aid)
                if not asset:
                    bot.answer_callback_query(call.id, "⚠️ Макет не найден", show_alert=True)
                    return
                orig_path = Path(asset.get("local_png_path", ""))
                if not orig_path.exists():
                    orig_path = Path(asset.get("local_preview_path", ""))
                if not orig_path.exists():
                    bot.answer_callback_query(call.id, "⚠️ Исходная картинка уже выгружена в облако", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🖼 Отправляю исходную картинку...")
                with open(orig_path, "rb") as of:
                    bot.send_document(
                        chat_id,
                        document=of,
                        visible_file_name=orig_path.name,
                        caption=f"🖼 Оригинал макета: <b>{html.escape(asset['topic_name'])}</b>",
                    )
                return

            if action == "hot" and len(parts) >= 3:
                aid = parts[2]
                asset = get_asset(aid)
                if not asset:
                    bot.answer_callback_query(call.id, "⚠️ Макет не найден в базе", show_alert=True)
                    return
                if not is_admin_fn(uid):
                    bot.answer_callback_query(call.id, "⛔ Отправка в Hot Folder доступна только администратору", show_alert=True)
                    return

                lic = str(asset.get("license", "unknown")).lower().strip()
                if lic == "unknown":
                    kb_confirm = InlineKeyboardMarkup()
                    kb_confirm.row(
                        InlineKeyboardButton("⚠️ Да, подтверждаю (админ)", callback_data=f"harv:hot_confirm:{aid}"),
                        InlineKeyboardButton("❌ Отмена", callback_data="harv:home"),
                    )
                    bot.send_message(
                        chat_id,
                        f"⚠️ <b>Внимание: Лицензия не подтверждена, не печатать на продажу!</b>\n\n"
                        f"Макет: <b>{html.escape(asset.get('title', aid))}</b>\n"
                        f"Источник: <code>{html.escape(asset.get('source_name', 'web'))}</code>\n"
                        f"Лицензия: <code>unknown</code>\n\n"
                        f"Вы действительно хотите отправить этот макет на печать в Hot Folder плоттера?",
                        reply_markup=kb_confirm,
                    )
                    bot.answer_callback_query(call.id)
                    return

                dest = copy_harvester_asset_to_hot_folder(aid)
                if dest is None:
                    bot.answer_callback_query(call.id, "⚠️ Не удалось скопировать .EPS в Hot Folder", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🚀 Отправлено в Hot Folder плоттера!")
                bot.send_message(
                    chat_id,
                    f"🚀 <b>Макет из базы отправлен в Hot Folder плоттера!</b>\n"
                    f"📄 Файл: <code>{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>",
                )
                return

            if action == "hot_confirm" and len(parts) >= 3:
                aid = parts[2]
                if not is_admin_fn(uid):
                    bot.answer_callback_query(call.id, "⛔ Доступно только администратору", show_alert=True)
                    return
                dest = copy_harvester_asset_to_hot_folder(aid, admin_confirmed=True)
                if dest is None:
                    bot.answer_callback_query(call.id, "⚠️ Не удалось скопировать .EPS в Hot Folder", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🚀 Отправлено в Hot Folder плоттера!")
                bot.send_message(
                    chat_id,
                    f"🚀 <b>Макет (подтверждён админом) отправлен в Hot Folder плоттера!</b>\n"
                    f"📄 Файл: <code>{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>",
                )
                return

        except Exception as exc:
            if "message is not modified" in str(exc):
                return
            traceback.print_exc()
            try:
                bot.answer_callback_query(call.id, f"Ошибка: {str(exc)[:150]}")
            except Exception:
                pass


# ==============================================================================
# ВЕБ-КАТАЛОГ БАЗЫ ПАРСЕРА НА ПОРТУ 8090 (/harvester)
# ==============================================================================


def render_harvester_web_catalog(
    cat_filter: str = "all",
    subcat_filter: str = "all",
    folder_filter: str = "all",
    search_q: str = "",
    msg_banner: str = "",
    is_admin: bool = False,
    csrf_token: str = "",
) -> bytes:
    if not HARVESTER_ENABLED:
        disabled_html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>База макетов Harvester — Модуль отключён</title>
  <style>
    body { background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px; max-width: 540px; text-align: center; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
    h1 { font-size: 22px; margin-top: 0; color: #e2e8f0; }
    p { color: #94a3b8; font-size: 15px; line-height: 1.5; margin: 16px 0; }
    .badge { display: inline-block; background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 9999px; padding: 6px 14px; font-weight: 600; font-size: 13px; margin-bottom: 12px; }
    .back { display: inline-block; margin-top: 16px; background: #e11d48; color: #fff; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 14px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">⏸ МОДУЛЬ ОТКЛЮЧЁН</div>
    <h1>Sticker Harvester 24/7</h1>
    <p>Автономный модуль сбора и каталог макетов отключены в настройках сервера (<code>HARVESTER_ENABLED=false</code>).</p>
    <p>Для включения задайте <code>HARVESTER_ENABLED=true</code> в файле конфигурации <code>.env</code> или переменных окружения.</p>
    <a class="back" href="/">⬅️ Вернуться в панель управления</a>
  </div>
</body>
</html>"""
        return disabled_html.encode("utf-8")

    init_db()
    st = get_all_state()
    summary = get_catalog_summary()
    totals = summary.get("totals", {})
    tree = get_folder_tree()
    qinfo = get_gdrive_quota_info()

    enabled = bool(st.get("enabled", True))
    item_limit = 160 if (folder_filter and folder_filter != "all") else 84
    assets = list_assets(
        category_lvl1=cat_filter if cat_filter != "all" else None,
        category_lvl2=subcat_filter if subcat_filter != "all" else None,
        folder_slug=folder_filter if folder_filter != "all" else None,
        search_query=search_q if search_q else None,
        limit=item_limit,
    )

    total_assets_all = int(totals.get("total_assets") or 0)
    total_folders_all = sum(c["folders_count"] for c in tree)

    # 1. Кнопки категорий 1-го уровня (Level 1)
    cat_pills = [
        (
            "all",
            f"🏠 Весь каталог ({total_folders_all} папок • {total_assets_all} мак.)",
        ),
    ]
    for c1 in tree:
        c1_key = c1["category_lvl1"]
        c1_lbl = format_c1_label(c1_key)
        cat_pills.append(
            (c1_key, f"{c1_lbl} ({c1['folders_count']} папок • {c1['files_count']} мак.)")
        )

    pills_html = []
    for c_key, c_lbl in cat_pills:
        act = "active" if (cat_filter == c_key and subcat_filter == "all" and folder_filter == "all") else (
            "active-parent" if cat_filter == c_key else ""
        )
        pills_html.append(
            f'<a class="pill {act}" href="/harvester?cat={urllib.parse.quote(c_key)}">{html.escape(c_lbl)}</a>'
        )

    # 2. Построение интерактивного дерева разделов (Level 2) и папок (Level 3)
    breadcrumbs_parts = [
        f'<a href="/harvester">🏠 Весь каталог ({total_assets_all})</a>'
    ]
    if cat_filter != "all":
        breadcrumbs_parts.append(
            f'<a href="/harvester?cat={urllib.parse.quote(cat_filter)}">{html.escape(format_c1_label(cat_filter))}</a>'
        )
    if subcat_filter != "all":
        breadcrumbs_parts.append(
            f'<a href="/harvester?cat={urllib.parse.quote(cat_filter)}&subcat={urllib.parse.quote(subcat_filter)}">{html.escape(format_c2_label(subcat_filter))}</a>'
        )
    if folder_filter != "all":
        breadcrumbs_parts.append(
            f'<span class="crumb-current">📂 {html.escape(folder_filter.replace("_", " "))} ({len(assets)} мак.)</span>'
        )
    if search_q:
        breadcrumbs_parts.append(
            f'<span class="crumb-current">🔍 Поиск: «{html.escape(search_q)}» ({len(assets)} мак.)</span>'
        )
    breadcrumbs_html = ' <span class="crumb-sep">▸</span> '.join(breadcrumbs_parts)

    # Собираем подразделы (Level 2) и папки (Level 3)
    subcat_cards_html: List[str] = []
    folder_chips_html: List[str] = []
    active_c2_title = ""

    for c1 in tree:
        c1_key = c1["category_lvl1"]
        if cat_filter != "all" and c1_key != cat_filter:
            continue
        for c2 in c1["subcategories"]:
            c2_key = c2["category_lvl2"]
            c2_lbl = format_c2_label(c2_key)
            is_c2_active = (c1_key == cat_filter and c2_key == subcat_filter)
            act_cls = "active" if is_c2_active else ""
            c1_badge = (
                f'<span class="subcat-c1">{html.escape(c1_key[:2])}</span> '
                if cat_filter == "all"
                else ""
            )
            subcat_cards_html.append(
                f'<a class="subcat-btn {act_cls}" href="/harvester?cat={urllib.parse.quote(c1_key)}&subcat={urllib.parse.quote(c2_key)}">'
                f'{c1_badge}<b>{html.escape(c2_lbl)}</b> '
                f'<span class="subcat-cnt">{c2["folders_count"]} папок • {c2["files_count"]} мак.</span>'
                f"</a>"
            )

            # Если выбран этот подраздел (или выбрана категория 1-го уровня и подраздел all) — показываем папки!
            if is_c2_active or (cat_filter != "all" and subcat_filter == "all"):
                if is_c2_active:
                    active_c2_title = f"{format_c1_label(c1_key)} ▸ {c2_lbl} ({c2['folders_count']} папок)"
                for fl in c2["folders"]:
                    f_slug = fl["folder_slug"]
                    f_name = fl["topic_name"]
                    f_cnt = fl["files_count"]
                    f_packs = fl["packs_count"]
                    f_singles = fl["singles_count"]
                    is_f_active = (
                        c1_key == cat_filter
                        and c2_key == subcat_filter
                        and f_slug == folder_filter
                    )
                    f_cls = "active" if is_f_active else ""
                    meta_badge = (
                        f"{f_cnt} мак. ({f_packs} пак / {f_singles} шт)"
                        if (f_packs > 0 and f_singles > 0)
                        else f"{f_cnt} мак."
                    )
                    folder_chips_html.append(
                        f'<a class="folder-chip {f_cls}" data-fname="{html.escape(f_name.lower())}" '
                        f'href="/harvester?cat={urllib.parse.quote(c1_key)}&subcat={urllib.parse.quote(c2_key)}&folder={urllib.parse.quote(f_slug)}">'
                        f'<span class="folder-icon">{"📂" if is_f_active else "📁"}</span>'
                        f'<span class="folder-text">'
                        f'<span class="folder-title">{html.escape(f_name)}</span>'
                        f'<span class="folder-meta">{html.escape(meta_badge)}</span>'
                        f"</span></a>"
                    )

    # Полное раскрывающееся дерево всех папок (доступно даже на главной странице Весь каталог)
    full_tree_sections: List[str] = []
    if cat_filter == "all" and subcat_filter == "all" and folder_filter == "all":
        for c1 in tree:
            c1_key = c1["category_lvl1"]
            c1_lbl = format_c1_label(c1_key)
            for c2 in c1["subcategories"]:
                c2_key = c2["category_lvl2"]
                c2_lbl = format_c2_label(c2_key)
                f_links = []
                for fl in c2["folders"]:
                    f_slug = fl["folder_slug"]
                    f_name = fl["topic_name"]
                    f_cnt = fl["files_count"]
                    f_links.append(
                        f'<a class="folder-chip" data-fname="{html.escape(f_name.lower())}" '
                        f'href="/harvester?cat={urllib.parse.quote(c1_key)}&subcat={urllib.parse.quote(c2_key)}&folder={urllib.parse.quote(f_slug)}">'
                        f'<span class="folder-icon">📁</span>'
                        f'<span class="folder-text">'
                        f'<span class="folder-title">{html.escape(f_name)}</span>'
                        f'<span class="folder-meta">{f_cnt} мак.</span>'
                        f"</span></a>"
                    )
                full_tree_sections.append(
                    f"""
                    <details class="tree-details">
                      <summary>
                        <span><b>{html.escape(c1_lbl)}</b> ▸ <b>{html.escape(c2_lbl)}</b></span>
                        <span class="subcat-cnt">{c2['folders_count']} папок • {c2['files_count']} макетов</span>
                      </summary>
                      <div class="folder-grid" style="margin-top:12px;">
                        {"".join(f_links)}
                      </div>
                    </details>
                    """
                )

    if folder_chips_html:
        folder_explorer_box = f"""
        <div class="explorer-panel">
          <div class="explorer-header">
            <div>
              <b>📂 Выберите конкретную папку ({len(folder_chips_html)} шт.)</b>
              {f'<span style="color:var(--muted);font-size:13px;margin-left:8px;">в разделе {html.escape(active_c2_title)}</span>' if active_c2_title else ''}
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <input type="text" id="folderFilterInput" oninput="filterFoldersLive(this.value)" placeholder="🔎 Быстрый поиск папки в списке..." style="min-width:220px;padding:6px 11px;font-size:12.5px;" />
              {f'<a class="pill" style="padding:5px 12px;font-size:12px;" href="/harvester?cat={urllib.parse.quote(cat_filter)}&subcat={urllib.parse.quote(subcat_filter)}">👁 Показать все макеты раздела</a>' if folder_filter != "all" else ''}
            </div>
          </div>
          <div class="folder-grid" id="folderGridBox">
            {"".join(folder_chips_html)}
          </div>
        </div>
        """
    elif full_tree_sections:
        folder_explorer_box = f"""
        <div class="explorer-panel">
          <div class="explorer-header">
            <div>
              <b>🌳 Дерево всех папок по темам ({total_folders_all} папок)</b>
              <span style="color:var(--muted);font-size:12.5px;margin-left:8px;">Нажмите на раздел выше или разверните любую ветку дерева ниже:</span>
            </div>
            <input type="text" id="folderFilterInput" oninput="filterTreeFoldersLive(this.value)" placeholder="🔎 Быстрый фильтр папки по названию (Слово пацана, Наруто, BMW...)" style="min-width:280px;padding:6px 11px;font-size:12.5px;" />
          </div>
          <div id="fullTreeContainer" style="display:flex;flex-direction:column;gap:8px;margin-top:10px;">
            {"".join(full_tree_sections)}
          </div>
        </div>
        """
    else:
        folder_explorer_box = ""

    cards_html = []
    for a in assets:
        aid = a["asset_id"]
        atype_badge = ASSET_TYPE_LABELS.get(a["asset_type"], a["asset_type"])
        tname = html.escape(a["topic_name"])
        c1_v = a["category_lvl1"]
        c2_v = a["category_lvl2"]
        f_v = a["folder_slug"]
        rel_folder = html.escape(a["rel_folder_path"])
        eps_name = html.escape(a["eps_filename"])
        w_mm = a["width_mm"]
        h_mm = a["height_mm"]
        nc = a["num_contours"]
        mb = a["eps_size_mb"]
        synced = bool(a.get("synced_to_gdrive"))
        in_hot = bool(a.get("in_hot_folder"))
        ts_str = format_ts_msk(float(a.get("created_at", 0)))

        folder_link = (
            f"/harvester?cat={urllib.parse.quote(c1_v)}"
            f"&subcat={urllib.parse.quote(c2_v)}"
            f"&folder={urllib.parse.quote(f_v)}"
        )

        lic = str(a.get("license", "unknown")).lower().strip()
        if lic == "unknown":
            lic_badge_html = '<div style="background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.3);border-radius:6px;padding:4px 8px;font-size:11px;font-weight:600;margin-top:4px;">⚠️ Лицензия не подтверждена, не печатать на продажу</div>'
            hot_btn_html = (
                f"""<form method="POST" action="/harvester_hot/{aid}" style="margin:0;flex:1;display:flex;">
                    <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
                    <input type="hidden" name="confirm_unlicensed" value="1" />
                    <button type="submit" class="btn btn-hot" onclick="return confirm('⚠️ Внимание! Лицензия макета не подтверждена. Печать на продажу запрещена. Подтверждаете отправку в Hot Folder?');" style="width:100%;border:none;cursor:pointer;font-family:inherit;background:#991b1b;">{"✅ В Hot Folder" if in_hot else "⚠️ В Hot Folder (Подтвердить)"}</button>
                </form>"""
                if is_admin
                else f'<a class="btn btn-secondary" href="/harvester_file/{aid}/preview" target="_blank">🖼 Превью</a>'
            )
        else:
            lic_lbl = html.escape(LICENSE_LABELS.get(lic, lic))
            lic_badge_html = f'<div style="background:rgba(34,197,94,0.15);color:#4ade80;border:1px solid rgba(34,197,94,0.3);border-radius:6px;padding:4px 8px;font-size:11px;margin-top:4px;">✓ {lic_lbl}</div>'
            hot_btn_html = (
                f"""<form method="POST" action="/harvester_hot/{aid}" style="margin:0;flex:1;display:flex;">
                    <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
                    <button type="submit" class="btn btn-hot" style="width:100%;border:none;cursor:pointer;font-family:inherit;">{"✅ В Hot Folder" if in_hot else "🚀 В Hot Folder"}</button>
                </form>"""
                if is_admin
                else f'<a class="btn btn-secondary" href="/harvester_file/{aid}/preview" target="_blank">🖼 Превью</a>'
            )

        cards_html.append(
            f"""
            <div class="card">
                <div class="card-header">
                    <div>
                        <a class="user-badge" href="{folder_link}" style="color:var(--text);text-decoration:none;" title="Открыть всю папку «{tname}»">📁 {tname} ▸</a>
                        <div class="meta-time"><a href="{folder_link}" style="color:var(--cyan);text-decoration:none;">📂 <code>{rel_folder}</code></a></div>
                        <div class="meta-time">🕒 {ts_str} • {'☁️ На Google Диске' if synced else '⏳ В буфере'}</div>
                    </div>
                    <span class="contour-badge">{html.escape(atype_badge)} • ✂️ {nc}</span>
                </div>
                <a href="/harvester_file/{aid}/preview" target="_blank" class="img-box" style="aspect-ratio: 4/3;">
                    <span class="img-label magenta">CutContour / PerfCut ({w_mm:g}×{h_mm:g} мм)</span>
                    <img src="/harvester_file/{aid}/preview" loading="lazy" alt="preview" />
                </a>
                <div class="card-specs">
                    <span>📄 <b>{eps_name}</b> ({mb:.1f} МБ)</span>
                    <span>📐 <b>{w_mm:g} × {h_mm:g} мм</b></span>
                    <span>🌐 <b>{html.escape(a['source_name'])}</b></span>
                    {lic_badge_html}
                </div>
                <div class="card-actions">
                    <a class="btn btn-primary" href="/harvester_file/{aid}/eps">📥 Скачать .EPS</a>
                    {hot_btn_html}
                </div>
            </div>
            """
        )

    toast = (
        f'<div style="background:#065f46;color:#fff;padding:12px 18px;border-radius:10px;margin-bottom:18px;font-weight:600;">{html.escape(msg_banner)}</div>'
        if msg_banner
        else ""
    )

    if is_admin:
        nav_tabs_html = """
        <div class="nav-tabs">
          <a class="tab-btn" href="/">👑 Работы Пользователей Бота</a>
          <a class="tab-btn active" href="/harvester">🚜 База Парсера 24/7 (Админ)</a>
          <a class="tab-btn" href="/logout" style="border-color:#ef4444;color:#fca5a5;">🔓 Выйти</a>
        </div>
        """
        controls_html = f"""
        <div class="controls-bar">
          <form class="form-inline" method="POST" action="/harvester_order">
            <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
            <b>🔥 Срочно собрать тему:</b>
            <input type="text" name="topic" placeholder="Например: Король и Шут, Subaru, Наруто..." required />
            <button type="submit">🚀 Запустить вне очереди</button>
          </form>
          <div class="form-inline" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <form class="form-inline" method="GET" action="/harvester" style="margin:0;">
              <input type="hidden" name="cat" value="{html.escape(cat_filter)}" />
              <input type="hidden" name="subcat" value="{html.escape(subcat_filter)}" />
              <input type="hidden" name="folder" value="{html.escape(folder_filter)}" />
              <input type="text" name="q" value="{html.escape(search_q)}" placeholder="Поиск по базе тем и файлов..." />
              <button type="submit" style="background:#334155;">🔍 Найти</button>
            </form>
            <form method="POST" action="/harvester_toggle" style="margin:0;">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
              <button type="submit" class="action-link" style="background:#0284c7;border:none;cursor:pointer;font-family:inherit;">{'⏸ Пауза' if enabled else '▶️ Старт'}</button>
            </form>
            <form method="POST" action="/harvester_sync" style="margin:0;">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
              <button type="submit" class="action-link" style="background:#059669;border:none;cursor:pointer;font-family:inherit;">☁️ Выгрузить в GDrive</button>
            </form>
          </div>
        </div>
        """
    else:
        nav_tabs_html = """
        <div class="nav-tabs">
          <a class="tab-btn active" href="/harvester">📦 Каталог Стикеров и Наклеек</a>
          <a class="tab-btn" href="/login?next=/harvester">🔐 Вход админа</a>
        </div>
        """
        controls_html = f"""
        <div class="controls-bar" style="justify-content:flex-start;">
          <form class="form-inline" method="GET" action="/harvester" style="width:100%;">
            <input type="hidden" name="cat" value="{html.escape(cat_filter)}" />
            <input type="hidden" name="subcat" value="{html.escape(subcat_filter)}" />
            <input type="hidden" name="folder" value="{html.escape(folder_filter)}" />
            <input type="text" name="q" value="{html.escape(search_q)}" placeholder="🔍 Поиск по всем папкам, фильмам, аниме, маркам авто..." style="flex:1;min-width:260px;" />
            <button type="submit">🔍 Найти в базе</button>
            {'<a class="action-link" style="background:#334155;" href="/harvester">✖ Сбросить</a>' if (search_q or cat_filter != 'all' or subcat_filter != 'all' or folder_filter != 'all') else ''}
          </form>
        </div>
        """

    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>📦 База Стикерпаков и Наклеек — Roland VP-540</title>
<style>
  :root {{
    --bg: #0f1117; --card: #181b24; --border: #2a2f3d;
    --text: #f1f5f9; --muted: #94a3b8; --magenta: #ec008c; --cyan: #00aeef;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .top-bar {{
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
    gap: 16px; margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }}
  h1 {{ margin: 0; font-size: 21px; display: flex; align-items: center; gap: 10px; }}
  .nav-tabs {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .tab-btn {{
    text-decoration: none; padding: 9px 16px; border-radius: 10px; font-size: 13.5px; font-weight: 600;
    background: var(--card); color: var(--text); border: 1px solid var(--border);
  }}
  .tab-btn.active {{ background: var(--magenta); border-color: var(--magenta); color: #fff; }}
  .stats-row {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat-box {{
    background: var(--card); border: 1px solid var(--border);
    padding: 8px 13px; border-radius: 10px; font-size: 13px;
  }}
  .controls-bar {{
    background: rgba(236, 0, 140, 0.08); border: 1px solid rgba(236, 0, 140, 0.35);
    padding: 12px 16px; border-radius: 12px; margin-bottom: 16px;
    display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: space-between;
  }}
  .form-inline {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  input[type="text"] {{
    background: #0f1117; border: 1px solid var(--border); color: #fff;
    padding: 8px 12px; border-radius: 8px; font-size: 13px; min-width: 240px;
  }}
  button, .action-link {{
    background: var(--magenta); color: #fff; border: none; padding: 8px 14px;
    border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; text-decoration: none;
  }}
  .breadcrumbs {{
    background: var(--card); border: 1px solid var(--border);
    padding: 10px 15px; border-radius: 10px; margin-bottom: 14px;
    font-size: 13.5px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  }}
  .breadcrumbs a {{ color: var(--cyan); text-decoration: none; font-weight: 600; }}
  .breadcrumbs a:hover {{ text-decoration: underline; }}
  .crumb-sep {{ color: var(--muted); margin: 0 2px; }}
  .crumb-current {{ color: #fff; font-weight: 700; background: rgba(236,0,140,0.22); padding: 3px 9px; border-radius: 6px; }}
  .pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
  .pill {{
    text-decoration: none; color: var(--text); background: var(--card);
    border: 1px solid var(--border); padding: 8px 14px; border-radius: 999px; font-size: 13px;
  }}
  .pill:hover, .pill.active {{ background: var(--magenta); border-color: var(--magenta); color: #fff; font-weight: 600; }}
  .pill.active-parent {{ border-color: var(--magenta); color: #ff77c8; font-weight: 600; }}
  .subcats-bar {{
    display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;
  }}
  .subcat-btn {{
    text-decoration: none; color: var(--text); background: #1c202b;
    border: 1px solid var(--border); padding: 8px 13px; border-radius: 10px;
    font-size: 13px; display: inline-flex; align-items: center; gap: 8px; transition: 0.15s;
  }}
  .subcat-btn:hover, .subcat-btn.active {{
    background: rgba(0, 174, 239, 0.18); border-color: var(--cyan); color: #fff;
  }}
  .subcat-c1 {{
    background: rgba(255,255,255,0.08); color: var(--muted); font-size: 11px;
    padding: 2px 6px; border-radius: 5px; font-weight: 700;
  }}
  .subcat-cnt {{
    color: var(--muted); font-size: 11.5px; background: rgba(0,0,0,0.3);
    padding: 2px 7px; border-radius: 6px;
  }}
  .explorer-panel {{
    background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 14px 16px; margin-bottom: 20px;
  }}
  .explorer-header {{
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
    gap: 10px; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--border);
  }}
  .folder-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(225px, 1fr));
    gap: 8px; max-height: 360px; overflow-y: auto; padding-right: 4px;
  }}
  .folder-chip {{
    text-decoration: none; color: var(--text); background: #12151e;
    border: 1px solid var(--border); border-radius: 9px; padding: 8px 11px;
    display: flex; align-items: center; gap: 9px; transition: 0.15s;
  }}
  .folder-chip:hover {{
    border-color: var(--cyan); background: rgba(0, 174, 239, 0.1);
  }}
  .folder-chip.active {{
    background: rgba(236, 0, 140, 0.22); border-color: var(--magenta); color: #fff;
  }}
  .folder-icon {{ font-size: 17px; flex-shrink: 0; }}
  .folder-text {{ display: flex; flex-direction: column; overflow: hidden; }}
  .folder-title {{ font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .folder-meta {{ font-size: 11px; color: var(--muted); }}
  .tree-details {{
    background: #131620; border: 1px solid var(--border); border-radius: 10px; padding: 9px 13px;
  }}
  .tree-details summary {{
    cursor: pointer; display: flex; justify-content: space-between; align-items: center;
    font-size: 13.5px; list-style: none;
  }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 18px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 15px; display: flex; flex-direction: column; gap: 11px;
  }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
  .user-badge {{ font-weight: 600; font-size: 15px; }}
  .user-badge:hover {{ color: var(--cyan) !important; }}
  .meta-time {{ font-size: 11.5px; color: var(--muted); margin-top: 3px; word-break: break-all; }}
  .contour-badge {{
    background: rgba(236, 0, 140, 0.18); color: #ff5cc0; border: 1px solid rgba(236, 0, 140, 0.4);
    padding: 4px 10px; border-radius: 999px; font-size: 11.5px; font-weight: 600; white-space: nowrap;
  }}
  .img-box {{
    position: relative; background: #232734; border-radius: 10px; overflow: hidden;
    display: flex; align-items: center; justify-content: center; border: 1px solid var(--border);
  }}
  .img-box img {{ width: 100%; height: 100%; object-fit: contain; }}
  .img-label {{
    position: absolute; top: 6px; left: 6px; background: var(--magenta);
    color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 6px; font-weight: 600;
  }}
  .card-specs {{
    display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--muted);
    background: rgba(0,0,0,0.22); padding: 8px 11px; border-radius: 8px;
  }}
  .card-specs b {{ color: var(--text); }}
  .card-actions {{ display: flex; gap: 8px; margin-top: auto; }}
  .btn {{
    flex: 1; text-align: center; text-decoration: none; padding: 9px 10px;
    border-radius: 8px; font-size: 13px; font-weight: 600;
  }}
  .btn-primary {{ background: var(--magenta); color: #fff; }}
  .btn-hot {{ background: #0284c7; color: #fff; }}
  .btn-secondary {{ background: #262b38; color: var(--text); flex: 0.45; }}
</style>
<script>
function filterFoldersLive(q) {{
  q = (q || "").toLowerCase().trim();
  var chips = document.querySelectorAll("#folderGridBox .folder-chip");
  for (var i = 0; i < chips.length; i++) {{
    var name = chips[i].getAttribute("data-fname") || "";
    chips[i].style.display = (!q || name.indexOf(q) !== -1) ? "flex" : "none";
  }}
}}
function filterTreeFoldersLive(q) {{
  q = (q || "").toLowerCase().trim();
  var details = document.querySelectorAll("#fullTreeContainer .tree-details");
  for (var d = 0; d < details.length; d++) {{
    var chips = details[d].querySelectorAll(".folder-chip");
    var visibleCount = 0;
    for (var i = 0; i < chips.length; i++) {{
      var name = chips[i].getAttribute("data-fname") || "";
      var match = (!q || name.indexOf(q) !== -1);
      chips[i].style.display = match ? "flex" : "none";
      if (match) visibleCount++;
    }}
    if (q) {{
      details[d].open = (visibleCount > 0);
      details[d].style.display = (visibleCount > 0) ? "block" : "none";
    }} else {{
      details[d].open = false;
      details[d].style.display = "block";
    }}
  }}
}}
</script>
</head>
<body>
  <div class="top-bar">
    <h1>📦 База Стикерпаков и Наклеек 24/7 <span style="font-size:13px;color:var(--muted);font-weight:400;">(Roland Print &amp; Cut)</span></h1>
    {nav_tabs_html}
  </div>
  {toast}
  <div class="stats-row">
    <div class="stat-box">📡 Пополнение базы: <b>{'🟢 РАБОТАЕТ 24/7' if enabled else '⏸ ПАУЗА'}</b></div>
    <div class="stat-box">🎯 Сейчас собирается: <b>{html.escape(str(st.get('current_topic', '—')))}</b></div>
    <div class="stat-box">🗂 Готовых папок: <b>{total_folders_all} шт.</b> ({total_assets_all} мак., {float(totals.get('total_eps_mb') or 0.0):.1f} МБ)</div>
    <div class="stat-box">☁️ Облако 5 ТБ: свободно <b>{html.escape(qinfo.get('free', '4.73 TiB'))}</b></div>
  </div>
  {controls_html}
  <div class="breadcrumbs">
    🧭 Навигация: {breadcrumbs_html}
  </div>
  <div class="pills">
    {"".join(pills_html)}
  </div>
  <div class="subcats-bar">
    {"".join(subcat_cards_html)}
  </div>
  {folder_explorer_box}
  <div class="grid">
    {"".join(cards_html) if cards_html else '<p style="color:var(--muted);">В выбранной папке пока нет макетов или по вашему запросу ничего не найдено.</p>'}
  </div>
</body>
</html>"""
    return page.encode("utf-8")

