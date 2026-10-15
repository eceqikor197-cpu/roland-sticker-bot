"""Модуль управления заданиями печати, очередью и фоновыми задачами.

Включает:
- process_and_send_job: запуск конвертации, расчет цен и отправка готового .EPS пользователю
- copy_job_to_hot_folder: отправка макета в сетевую папку плоттера Roland
- inspect_eps_stats: извлечение контуров и габаритов из заголовков EPS
- notify_admins_about_job: оповещение администраторов о новых макетах (Live-шпион)
- send_or_edit_admin_feed_item: просмотр очереди заданий в Telegram-ленте
- cleanup_old_jobs, run_cleanup_loop, start_cleanup_scheduler: фоновое удаление старых заданий
- delete_user_data, format_privacy_text: приватность и удаление данных пользователя
- list_all_jobs, backfill_existing_jobs_and_users: учет и синхронизация макетов
"""

from __future__ import annotations

import datetime
import html
import json
import logging
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL.Image import DecompressionBombError

from bot.keyboards import build_job_keyboard, is_admin
from config import (
    ADMIN_IDS,
    HOT_PREVIEWS_DIR,
    HOT_QUEUE_EPS_DIR,
    JOB_RETENTION_HOURS,
    JOBS_DIR,
    LIVE_SPY_DEFAULT,
    STORAGE_DB_PATH,
    WORK_DIR,
)
from jpeg2eps import (
    MM_TO_PT,
    convert_image_to_plotter_eps,
)
from pricing import calculate_nesting_and_price
from storage import (
    delete_job as storage_delete_job,
    delete_user_jobs as storage_delete_user_jobs,
    delete_user_prefs as storage_delete_user_prefs,
    delete_user_record as storage_delete_user_record,
    format_user_trigger as storage_format_user_trigger,
    get_admin_state as storage_get_admin_state,
    get_all_users as storage_get_all_users,
    get_job as storage_get_job,
    get_user_display as storage_get_user_display,
    get_user_prefs as storage_get_user_prefs,
    get_user_short_slug as storage_get_user_short_slug,
    init_storage,
    is_user_banned as storage_is_user_banned,
    list_all_jobs as storage_list_all_jobs,
    load_all_prefs as storage_load_all_prefs,
    record_user_activity as storage_record_user_activity,
    save_job as storage_save_job,
    toggle_ban_user as storage_toggle_ban_user,
    update_admin_state as storage_update_admin_state,
    update_job as storage_update_job,
    update_user_jobs_count as storage_update_user_jobs_count,
    update_user_prefs as storage_update_user_prefs,
)
from web.pages import PERF_MODE_LABELS, format_ts

logger = logging.getLogger(__name__)

MIN_SIZE_MM = 5.0
MAX_SIZE_MM = 1370.0
MIN_STRETCH = 0.1
MAX_STRETCH = 10.0


def _get_bot() -> Any:
    import telegram_bot

    return telegram_bot.bot


def _get_converter() -> Any:
    try:
        import telegram_bot

        return getattr(telegram_bot, "convert_image_to_plotter_eps", convert_image_to_plotter_eps)
    except Exception:
        return convert_image_to_plotter_eps


def _get_jobs_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "JOBS_DIR", JOBS_DIR))
    except Exception:
        return JOBS_DIR


def _get_hot_queue_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "HOT_QUEUE_EPS_DIR", HOT_QUEUE_EPS_DIR))
    except Exception:
        return HOT_QUEUE_EPS_DIR


def _get_hot_previews_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "HOT_PREVIEWS_DIR", HOT_PREVIEWS_DIR))
    except Exception:
        return HOT_PREVIEWS_DIR


def _get_prefs_file() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "PREFS_FILE", WORK_DIR / "user_prefs.json"))
    except Exception:
        return WORK_DIR / "user_prefs.json"


def _get_users_file() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "USERS_FILE", WORK_DIR / "users_db.json"))
    except Exception:
        return WORK_DIR / "users_db.json"


def load_json_file(path: Path, default: Any) -> Any:
    """Загружает JSON из файла с безопасным возвратом default при ошибке."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json_file(path: Path, data: Any) -> None:
    """Атомарно сохраняет данные в JSON через временный файл."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def inspect_eps_stats(eps_path: Path) -> Tuple[int, int, float, float]:
    """Возвращает (cut_contours_count, perf_contours_count, width_mm, height_mm)."""
    if not eps_path.exists():
        return 1, 0, 100.0, 100.0
    try:
        raw = eps_path.read_bytes()
        header_part = raw[:1200].decode("latin1", errors="ignore")
        m_bbox = re.search(
            r"%%HiResBoundingBox:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            header_part,
        )
        w_mm, h_mm = 100.0, 100.0
        if m_bbox:
            w_pt = float(m_bbox.group(3)) - float(m_bbox.group(1))
            h_pt = float(m_bbox.group(4)) - float(m_bbox.group(2))
            w_mm = round(w_pt / MM_TO_PT, 1)
            h_mm = round(h_pt / MM_TO_PT, 1)

        cut_marker = b"% --- Plotter CutContour Spot Color Vector Layer"
        perf_marker = b"% --- Plotter PerfCutContour Spot Color Vector Layer"

        cut_cnt = 0
        c_idx = raw.find(cut_marker)
        if c_idx != -1:
            c_end = raw.find(b"\r\ns\r\n", c_idx)
            c_sec = raw[c_idx:c_end] if c_end != -1 else raw[c_idx : c_idx + 200000]
            cut_cnt = len(re.findall(rb"\bcp\b", c_sec))

        perf_cnt = 0
        p_idx = raw.find(perf_marker)
        if p_idx != -1:
            p_end = raw.find(b"\r\ns\r\n", p_idx)
            p_sec = raw[p_idx:p_end] if p_end != -1 else raw[p_idx : p_idx + 200000]
            perf_cnt = len(re.findall(rb"\bcp\b", p_sec))

        return cut_cnt, perf_cnt, w_mm, h_mm
    except Exception:
        return 1, 0, 100.0, 100.0


def copy_job_to_hot_folder(job_id: str) -> Optional[Path]:
    """Копирует готовый .eps и превью в сетевую папку плоттера Roland_HotFolder."""
    jobs_dir = _get_jobs_dir()
    hot_queue_dir = _get_hot_queue_dir()
    hot_prev_dir = _get_hot_previews_dir()

    jdir = jobs_dir / job_id
    meta_file = jdir / "meta.json"
    meta = storage_get_job(job_id)
    if not meta and meta_file.exists():
        meta = load_json_file(meta_file, {})
    if not meta:
        return None
    eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
    eps_path = jdir / eps_name
    if not eps_path.exists():
        return None

    uid = int(meta.get("user_id", 0))
    user_slug = storage_get_user_short_slug(uid)
    qty = int(meta.get("nesting_qty", 1))
    size_mm = int(round(float(meta.get("size_mm", 100))))
    dt_str = datetime.datetime.now().strftime("%m-%d_%H-%M")
    safe_stem = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", str(meta.get("orig_stem", "sticker"))).strip("_")

    hot_base = f"{dt_str}_{user_slug}_{qty}sht_{size_mm}mm_{safe_stem}"
    dest_eps = hot_queue_dir / f"{hot_base}.eps"
    shutil.copy2(eps_path, dest_eps)

    preview_png = jdir / "preview_cutcontour.png"
    if preview_png.exists():
        shutil.copy2(preview_png, hot_prev_dir / f"{hot_base}.png")

    meta["in_hot_folder"] = True
    meta["hot_filename"] = dest_eps.name
    save_json_file(meta_file, meta)
    storage_update_job(job_id, in_hot_folder=1, hot_filename=dest_eps.name)
    return dest_eps


def list_all_jobs(filter_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает список всех макетов с метаданными о наличии .eps и превью."""
    jobs_dir = _get_jobs_dir()
    jobs = storage_list_all_jobs(filter_user_id=filter_user_id)
    seen_ids = set()
    enriched_jobs: List[Dict[str, Any]] = []

    for meta in jobs:
        jid = meta.get("job_id")
        if not jid:
            continue
        seen_ids.add(jid)
        jdir = jobs_dir / jid
        eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
        eps_path = jdir / eps_name
        preview_path = jdir / "preview_cutcontour.png"
        meta["has_eps"] = eps_path.exists()
        meta["eps_filename"] = eps_name
        meta["eps_size_mb"] = (
            round(eps_path.stat().st_size / (1024 * 1024), 2) if eps_path.exists() else 0.0
        )
        meta["has_preview"] = preview_path.exists()
        enriched_jobs.append(meta)

    if jobs_dir.exists():
        for jdir in jobs_dir.iterdir():
            if not jdir.is_dir() or jdir.name in seen_ids:
                continue
            meta_file = jdir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if filter_user_id is not None and int(meta.get("user_id", 0)) != int(filter_user_id):
                    continue
                eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
                eps_path = jdir / eps_name
                preview_path = jdir / "preview_cutcontour.png"
                meta["has_eps"] = eps_path.exists()
                meta["eps_filename"] = eps_name
                meta["eps_size_mb"] = (
                    round(eps_path.stat().st_size / (1024 * 1024), 2) if eps_path.exists() else 0.0
                )
                meta["has_preview"] = preview_path.exists()
                enriched_jobs.append(meta)
                try:
                    storage_save_job(meta)
                except Exception:
                    pass
            except Exception:
                continue

    enriched_jobs.sort(key=lambda x: float(x.get("updated_at", x.get("created_at", 0))), reverse=True)
    return enriched_jobs


def backfill_existing_jobs_and_users() -> None:
    """Синхронизирует существующие файлы и задания в SQLite."""
    init_storage(STORAGE_DB_PATH)
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    users_file = _get_users_file()

    try:
        from scripts.migrate_json_to_sqlite import migrate
        all_db_users = storage_get_all_users()
        if not all_db_users:
            logger.info("SQLite хранилище пусто, запускаем автомиграцию из JSON...")
            migrate(base_dir=WORK_DIR.parent, db_path=STORAGE_DB_PATH, dry_run=False)
    except Exception as exc:
        logger.warning("Автомиграция в SQLite завершилась с предупреждением: %s", exc)

    users = storage_get_all_users()
    prefs = storage_load_all_prefs()
    seen_uids = {int(k) for k in prefs.keys() if str(k).isdigit()}
    all_jobs = list_all_jobs()
    for j in all_jobs:
        uid = int(j.get("user_id", 0))
        if uid:
            seen_uids.add(uid)

    for uid in seen_uids:
        if str(uid) not in users:
            try:
                chat = bot.get_chat(uid)
                storage_record_user_activity(
                    uid,
                    first_name=getattr(chat, "first_name", None) or f"User {uid}",
                    last_name=getattr(chat, "last_name", None) or "",
                    username=getattr(chat, "username", None) or "",
                    action="Активен в боте",
                )
            except Exception:
                storage_record_user_activity(uid, first_name=f"User {uid}", action="Активен в боте")

    users = storage_get_all_users()
    counts: Dict[str, int] = {}
    for j in all_jobs:
        uid_str = str(j.get("user_id", ""))
        counts[uid_str] = counts.get(uid_str, 0) + 1
        jdir = jobs_dir / j["job_id"]
        meta_file = jdir / "meta.json"
        eps_path = jdir / f"{j.get('orig_stem', 'sticker')}.eps"
        changed = False
        if "num_contours" not in j or "width_mm" not in j:
            cc, pc, w_mm, h_mm = inspect_eps_stats(eps_path)
            j["num_contours"] = cc
            j["perf_contours"] = pc
            j["width_mm"] = w_mm
            j["height_mm"] = h_mm
            changed = True
        if "offset_mm" not in j:
            up = storage_get_user_prefs(int(j.get("user_id", 0)))
            j["offset_mm"] = up["offset_mm"]
            j["size_mm"] = up["size_mm"]
            j["keep_background"] = up["keep_background"]
            j["perf_cut_mode"] = up.get("perf_cut_mode", "none")
            j["nesting_qty"] = up.get("nesting_qty", 1)
            changed = True
        if changed:
            save_json_file(meta_file, j)
            storage_save_job(j)

    for k, v in counts.items():
        if k.isdigit():
            storage_update_user_jobs_count(int(k), v)
            if k in users:
                users[k]["jobs_count"] = v
    save_json_file(users_file, users)


def notify_admins_about_job(
    actor_id: int,
    job_meta: Dict[str, Any],
    preview_png: Path,
    action_title: str,
) -> None:
    """Оповещает администраторов о действиях пользователей при включенном live-шпионе."""
    if is_admin(actor_id):
        return
    state = storage_get_admin_state()
    if not state.get("live_spy_enabled", LIVE_SPY_DEFAULT):
        return

    bot = _get_bot()
    user_disp = storage_get_user_display(actor_id)
    job_id = job_meta["job_id"]
    eps_filename = f"{job_meta['orig_stem']}.eps"
    num_contours = job_meta.get("num_contours", 1)
    perf_mode = job_meta.get("perf_cut_mode", "none")
    qty = int(job_meta.get("nesting_qty", 1))
    w_mm = job_meta.get("width_mm", 100.0)
    h_mm = job_meta.get("height_mm", 100.0)
    calc = job_meta.get("pricing", {})

    text = (
        f"👀 <b>Активность в боте: {action_title}</b>\n"
        f"👤 Пользователь: {user_disp}\n"
        f"📄 Файл: <code>{html.escape(eps_filename)}</code>\n"
        f"📐 Габарит: <b>{w_mm:g} × {h_mm:g} мм</b> | Тираж: <b>{qty} шт.</b>\n"
        f"✂️ Резка: <b>{PERF_MODE_LABELS.get(perf_mode, perf_mode)}</b> ({num_contours} конт.)\n"
        f"💰 Себестоимость (ЗЕНОН): <b>~{calc.get('cost_rub', 10)} ₽</b> | Цена клиенту: <b>~{calc.get('retail_rub', 150)} ₽</b>"
    )

    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📥 Скачать его .EPS", callback_data=f"adm:dl_eps:{job_id}"),
        InlineKeyboardButton("🚀 В Hot Folder плоттера", callback_data=f"adm:hot:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("🖼 Исходник", callback_data=f"adm:dl_orig:{job_id}"),
        InlineKeyboardButton("👑 Админ-панель", callback_data="adm:home"),
    )

    for admin_id in ADMIN_IDS:
        try:
            if preview_png.exists() and preview_png.stat().st_size > 0:
                try:
                    with open(preview_png, "rb") as pf:
                        bot.send_photo(admin_id, photo=pf, caption=text, reply_markup=kb)
                except Exception:
                    bot.send_message(admin_id, text, reply_markup=kb)
            else:
                bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            traceback.print_exc()


def process_and_send_job(
    chat_id: int,
    user_id: int,
    job_dir: Path,
    status_msg_id: Optional[int] = None,
    was_compressed_photo: bool = False,
    action_title: str = "Новая конвертация",
) -> None:
    """Выполняет конвертацию изображения, строит векторный .EPS и отправляет результат."""
    bot = _get_bot()
    converter = _get_converter()
    users_file = _get_users_file()

    meta_file = job_dir / "meta.json"
    meta = storage_get_job(job_dir.name)
    if not meta and meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if not meta:
        meta = {}
    input_path = job_dir / meta.get("input_filename", "input.jpg")
    orig_stem = meta.get("orig_stem", "sticker")
    prefs = storage_get_user_prefs(user_id)

    offset_mm = float(meta.get("offset_mm") if meta.get("offset_mm") is not None else prefs["offset_mm"])
    size_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, float(meta.get("size_mm") if meta.get("size_mm") is not None else prefs["size_mm"])))
    keep_bg = bool(meta.get("keep_background") if meta.get("keep_background") is not None else prefs["keep_background"])
    perf_mode = str(meta.get("perf_cut_mode") if meta.get("perf_cut_mode") is not None else prefs.get("perf_cut_mode", "none"))
    perf_passes = int(meta.get("perf_cut_passes") if meta.get("perf_cut_passes") is not None else prefs.get("perf_cut_passes", 2))
    nesting_qty = int(meta.get("nesting_qty") if meta.get("nesting_qty") is not None else prefs.get("nesting_qty", 1))
    roll_width_mm = float(meta.get("roll_width_mm") if meta.get("roll_width_mm") is not None else prefs.get("roll_width_mm", 1370.0))
    stretch_x = max(MIN_STRETCH, min(MAX_STRETCH, float(meta.get("stretch_x") if meta.get("stretch_x") is not None else prefs.get("stretch_x", 1.0))))
    stretch_y = max(MIN_STRETCH, min(MAX_STRETCH, float(meta.get("stretch_y") if meta.get("stretch_y") is not None else prefs.get("stretch_y", 1.0))))
    fit_a4_sheet = bool(meta.get("fit_a4_sheet") if meta.get("fit_a4_sheet") is not None else prefs.get("fit_a4_sheet", False))

    eps_filename = f"{orig_stem}.eps"
    output_eps = job_dir / eps_filename
    preview_png = job_dir / "preview_cutcontour.png"

    try:
        t0 = time.time()
        converter(
            input_path=input_path,
            output_eps=output_eps,
            size_mm=size_mm,
            offset_mm=offset_mm,
            bg_tolerance=12.0,
            keep_background=keep_bg,
            no_cut_contour=False,
            also_svg=False,
            preview_png_path=preview_png,
            perf_cut_mode=perf_mode,
            nesting_qty=nesting_qty,
            roll_width_mm=roll_width_mm,
            stretch_x=stretch_x,
            stretch_y=stretch_y,
            fit_a4_sheet=fit_a4_sheet,
            render_a4_guide_on_preview=True,
            perf_cut_passes=perf_passes,
        )
    except DecompressionBombError:
        logger.warning("DecompressionBombError in process_and_send_job for user %s", user_id)
        if status_msg_id is not None:
            try:
                bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
        bot.send_message(
            chat_id,
            "⚠️ <b>Файл слишком большой или повреждён (превышен лимит пикселей).</b>\n"
            "Пожалуйста, уменьшите разрешение изображения.",
        )
        return
    except Exception as exc:
        logger.exception("Error in process_and_send_job for user %s", user_id)
        if status_msg_id is not None:
            try:
                bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
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
        return

    elapsed = time.time() - t0
    num_contours, perf_contours, total_w_mm, total_h_mm = inspect_eps_stats(output_eps)
    eps_size_mb = output_eps.stat().st_size / (1024 * 1024)

    if nesting_qty == 1 and not fit_a4_sheet:
        single_w_mm, single_h_mm = total_w_mm, total_h_mm
        meta["single_w_mm"] = single_w_mm
        meta["single_h_mm"] = single_h_mm
    else:
        single_w_mm = float(meta.get("single_w_mm", size_mm))
        single_h_mm = float(meta.get("single_h_mm", size_mm))

    calc_for_25 = calculate_nesting_and_price(
        single_w_mm, single_h_mm, qty=max(nesting_qty, 25), roll_width_mm=roll_width_mm
    )
    calc_curr = calculate_nesting_and_price(
        single_w_mm, single_h_mm, qty=(int(calc_for_25["a4_fit_qty"]) if fit_a4_sheet else nesting_qty), roll_width_mm=roll_width_mm
    )
    a4_fit_qty = int(calc_curr.get("a4_fit_qty", 1))

    meta.update(
        {
            "updated_at": time.time(),
            "offset_mm": offset_mm,
            "size_mm": size_mm,
            "keep_background": keep_bg,
            "perf_cut_mode": perf_mode,
            "perf_cut_passes": perf_passes,
            "nesting_qty": nesting_qty,
            "stretch_x": stretch_x,
            "stretch_y": stretch_y,
            "fit_a4_sheet": fit_a4_sheet,
            "num_contours": num_contours,
            "perf_contours": perf_contours,
            "width_mm": total_w_mm,
            "height_mm": total_h_mm,
            "eps_size_mb": round(eps_size_mb, 2),
            "elapsed_sec": round(elapsed, 2),
            "last_action": action_title,
            "pricing": calc_curr,
        }
    )
    save_json_file(meta_file, meta)
    storage_save_job(meta)

    storage_record_user_activity(
        user_id,
        action=f"{action_title} ({eps_filename})",
        last_job_id=job_dir.name,
    )
    user_jobs_cnt = len(list_all_jobs(filter_user_id=user_id))
    storage_update_user_jobs_count(user_id, user_jobs_cnt)
    if users_file.exists():
        users = load_json_file(users_file, {})
        ukey = str(user_id)
        if ukey in users:
            users[ukey]["jobs_count"] = user_jobs_cnt
            users[ukey]["last_seen"] = time.time()
            users[ukey]["last_action"] = f"{action_title} ({eps_filename})"
            users[ukey]["last_job_id"] = job_dir.name
            save_json_file(users_file, users)

    perf_info = (
        f" + голубой <code>PerfCutContour</code> ({perf_contours} шт. × <b>{perf_passes} прох.</b>)"
        if perf_contours > 0
        else ""
    )
    stretch_info = (
        f" (пропорции {stretch_x:.2g}×{stretch_y:.2g})"
        if (abs(stretch_x - 1.0) > 0.02 or abs(stretch_y - 1.0) > 0.02)
        else ""
    )

    caption_lines = [
        f"✅ <b>Готово для Roland VP-540!</b> ({elapsed:.1f} сек)",
        f"📄 Файл: <code>{html.escape(eps_filename)}</code> ({eps_size_mb:.1f} МБ)",
        f"📐 Габарит макета: <b>{total_w_mm:g} × {total_h_mm:g} мм</b> (1 шт: <b>{single_w_mm/10.0:.1f} × {single_h_mm/10.0:.1f} см</b>){stretch_info}",
        f"✂️ Контуры: розовый <code>CutContour</code> (<b>{num_contours} шт.</b>){perf_info}",
        f"📏 Кант: <b>{offset_mm:g} мм</b> | Режим: <b>{'Лист А4 с вырубкой рамки' if fit_a4_sheet else PERF_MODE_LABELS.get(perf_mode, perf_mode)}</b>",
        "",
        "💰 <b>Калькулятор по накладной ЗЕНОН-СПБ (рулон 1.37м 103₽/м + чернила STARINK 4006₽/л):</b>",
        f"• 📄 <b>На 1 лист А4 (21×29.7 см):</b> входит <b>{a4_fit_qty} шт.</b> (себест. листа А4 ~<b>{calc_curr['a4_cost_rub']} ₽</b> | клиенту ~<b>{calc_curr['a4_retail_rub']} ₽</b>)",
    ]
    if fit_a4_sheet:
        caption_lines.extend(
            [
                f"• ✅ <b>Сейчас собран полный лист А4 ({a4_fit_qty} шт.):</b> себестоимость <b>~{calc_curr['a4_cost_rub']} ₽</b>, розница <b>~{calc_curr['a4_retail_rub']} ₽</b>",
            ]
        )
    elif nesting_qty > 1:
        caption_lines.extend(
            [
                f"• Собрано на рулон 1.37 м: <b>{nesting_qty} шт.</b> ({calc_curr['cols']} в ряд × {calc_curr['rows']} ряд., расход {calc_curr['used_length_m']} пог. м)",
                f"• Себестоимость: <b>~{calc_curr['cost_rub']} ₽</b> (материал {calc_curr['material_cost_rub']} ₽ + чернила {calc_curr['ink_cost_rub']} ₽)",
                f"• Рекомендуемая цена клиенту: <b>{calc_curr['retail_rub']} ₽</b> ({calc_curr['unit_rub']} ₽/шт)",
            ]
        )
    else:
        caption_lines.extend(
            [
                f"• На рулон 1.37 м войдёт: <b>по {calc_for_25['cols']} шт. в ряд</b>",
                f"• 1 шт.: себест. ~<b>{calc_curr['cost_rub']} ₽</b> | тираж 25 шт. ({calc_for_25['used_length_m']} пог.м): себест. <b>~{calc_for_25['cost_rub']} ₽</b> (материал {calc_for_25['material_cost_rub']}₽ + чернила {calc_for_25['ink_cost_rub']}₽), клиенту <b>~{calc_for_25['retail_rub']} ₽</b>",
            ]
        )

    caption_lines.append(
        "\n💡 <i>Справа на превью показан точный масштаб на листе А4. Вы можете сжимать/растягивать кнопками ниже или написать в чат <code>12x8 см</code>!</i>"
    )
    caption = "\n".join(caption_lines)
    kb = build_job_keyboard(
        job_dir.name,
        meta,
        in_hot_folder=bool(meta.get("in_hot_folder")),
        a4_fit_qty=a4_fit_qty,
        hot_request_status=meta.get("hot_folder_request_status"),
    )

    if preview_png.exists():
        try:
            with open(preview_png, "rb") as pf:
                bot.send_photo(
                    chat_id,
                    photo=pf,
                    caption=caption,
                    reply_markup=kb,
                )
        except Exception:
            try:
                from PIL import Image as _PILImage

                with _PILImage.open(preview_png) as _im:
                    _w, _h = _im.size
                    _safe_w = max(_w, int(_h / 15) + 1)
                    _safe_h = max(_h, int(_w / 15) + 1)
                    _pad = _PILImage.new("RGB", (_safe_w, _safe_h), (24, 27, 36))
                    _pad.paste(_im.convert("RGB"), ((_safe_w - _w) // 2, (_safe_h - _h) // 2))
                    _safe_path = job_dir / "preview_safe.jpg"
                    _pad.save(_safe_path, quality=90)
                with open(_safe_path, "rb") as pf:
                    bot.send_photo(
                        chat_id,
                        photo=pf,
                        caption=caption,
                        reply_markup=kb,
                    )
            except Exception:
                bot.send_message(chat_id, caption, reply_markup=kb)

    if fit_a4_sheet:
        dl_eps_name = f"{orig_stem}_A4_{a4_fit_qty}sht_{int(round(size_mm))}mm.eps"
    elif nesting_qty > 1:
        dl_eps_name = f"{orig_stem}_{nesting_qty}sht_{int(round(size_mm))}mm.eps"
    else:
        dl_eps_name = eps_filename

    with open(output_eps, "rb") as ef:
        bot.send_document(
            chat_id,
            document=ef,
            visible_file_name=dl_eps_name,
            caption=f"🖨 Готовый <code>.EPS</code> для Roland VersaWorks — <code>{html.escape(dl_eps_name)}</code>",
        )

    if status_msg_id is not None:
        try:
            bot.delete_message(chat_id, status_msg_id)
        except Exception:
            pass

    notify_admins_about_job(
        actor_id=user_id,
        job_meta=meta,
        preview_png=preview_png,
        action_title=action_title,
    )


def send_or_edit_admin_feed_item(
    chat_id: int,
    index: int,
    filter_arg: str = "all",
    prev_msg_id: Optional[int] = None,
) -> None:
    """Отображает конкретный макет в ленте просмотра для администратора."""
    bot = _get_bot()
    jobs_dir = _get_jobs_dir()
    filter_uid = int(filter_arg) if filter_arg != "all" and filter_arg.isdigit() else None
    jobs = list_all_jobs(filter_user_id=filter_uid)

    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    if not jobs:
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="adm:home"))
        bot.send_message(chat_id, "📭 Работ пока не найдено.", reply_markup=kb)
        return

    index = max(0, min(index, len(jobs) - 1))
    j = jobs[index]
    job_id = j["job_id"]
    uid = int(j.get("user_id", 0))
    udisp = storage_get_user_display(uid)
    ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
    eps_name = f"{j.get('orig_stem', 'sticker')}.eps"
    nc = j.get("num_contours", 1)
    off = j.get("offset_mm", 2.5)
    w_mm = j.get("width_mm", 100)
    h_mm = j.get("height_mm", 100)
    qty = j.get("nesting_qty", 1)
    perf_mode = j.get("perf_cut_mode", "none")
    size_mb = j.get("eps_size_mb", 0.0)
    calc = j.get("pricing", {})

    caption = (
        f"🗂 <b>Работа #{index + 1} из {len(jobs)}</b>\n"
        f"👤 Автор: {udisp}\n"
        f"🕒 Время: <code>{ts_str}</code>\n"
        f"📄 Файл: <code>{html.escape(eps_name)}</code> ({size_mb:.1f} МБ)\n"
        f"📐 Размер: <b>{w_mm:g} × {h_mm:g} мм</b> | Тираж: <b>{qty} шт.</b>\n"
        f"✂️ Контуров: <b>{nc} шт.</b> ({PERF_MODE_LABELS.get(perf_mode, perf_mode)}, кант {off:g}мм)\n"
        f"💰 Себестоимость: <b>~{calc.get('cost_rub', 30)} ₽</b> | Клиенту: <b>~{calc.get('retail_rub', 200)} ₽</b>"
    )

    kb = InlineKeyboardMarkup()
    nav_row = []
    if index > 0:
        nav_row.append(
            InlineKeyboardButton("⬅️ Пред.", callback_data=f"adm:feed:{index - 1}:{filter_arg}")
        )
    nav_row.append(
        InlineKeyboardButton(f"{index + 1}/{len(jobs)}", callback_data="adm:noop")
    )
    if index < len(jobs) - 1:
        nav_row.append(
            InlineKeyboardButton("След. ➡️", callback_data=f"adm:feed:{index + 1}:{filter_arg}")
        )
    kb.row(*nav_row)

    kb.row(
        InlineKeyboardButton("📥 Скачать .EPS", callback_data=f"adm:dl_eps:{job_id}"),
        InlineKeyboardButton("🖼 Скачать оригинал", callback_data=f"adm:dl_orig:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("🚀 В Hot Folder плоттера", callback_data=f"adm:hot:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("👑 В главное меню админки", callback_data="adm:home"),
    )

    if prev_msg_id is not None:
        try:
            bot.delete_message(chat_id, prev_msg_id)
        except Exception:
            pass

    preview_png = jobs_dir / job_id / "preview_cutcontour.png"
    if preview_png.exists():
        with open(preview_png, "rb") as pf:
            bot.send_photo(chat_id, photo=pf, caption=caption, reply_markup=kb)
    else:
        bot.send_message(chat_id, caption, reply_markup=kb)


def cleanup_old_jobs(max_age_hours: Optional[float] = None) -> int:
    """Удаляет директории заданий старше max_age_hours часов (по умолчанию JOB_RETENTION_HOURS)."""
    jobs_dir = _get_jobs_dir()
    if max_age_hours is None:
        max_age_hours = JOB_RETENTION_HOURS
    now = time.time()
    cutoff = now - max_age_hours * 3600.0
    deleted_count = 0
    if not jobs_dir.exists():
        return 0
    for item in list(jobs_dir.iterdir()):
        try:
            if item.is_dir() and item.stat().st_mtime < cutoff:
                shutil.rmtree(item, ignore_errors=True)
                storage_delete_job(item.name)
                deleted_count += 1
        except Exception as exc:
            logger.warning("Не удалось удалить устаревшее задание %s: %s", item, exc)
    return deleted_count


_cleanup_thread: Optional[threading.Thread] = None
_cleanup_stop_event: Optional[threading.Event] = None


def run_cleanup_loop(interval_seconds: float = 3600.0, stop_event: Optional[threading.Event] = None) -> None:
    """Периодическая фоновая очистка старых заданий."""
    logger.info(
        "Запущен фоновый поток очистки заданий (интервал %s с, срок хранения %s ч)",
        interval_seconds,
        JOB_RETENTION_HOURS,
    )
    while not (stop_event and stop_event.is_set()):
        try:
            cnt = cleanup_old_jobs(max_age_hours=JOB_RETENTION_HOURS)
            if cnt > 0:
                logger.info("Фоновая автоочистка: удалено %d устаревших заданий", cnt)
        except Exception as exc:
            logger.warning("Ошибка при периодической очистке старых заданий: %s", exc)

        if stop_event:
            if stop_event.wait(interval_seconds):
                break
        else:
            time.sleep(interval_seconds)


def start_cleanup_scheduler(interval_seconds: float = 3600.0) -> threading.Thread:
    """Запускает фоновый поток периодической очистки заданий."""
    global _cleanup_thread, _cleanup_stop_event
    if _cleanup_thread and _cleanup_thread.is_alive():
        return _cleanup_thread
    _cleanup_stop_event = threading.Event()
    _cleanup_thread = threading.Thread(
        target=run_cleanup_loop,
        args=(interval_seconds, _cleanup_stop_event),
        daemon=True,
        name="JobCleanupScheduler",
    )
    _cleanup_thread.start()
    return _cleanup_thread


def delete_user_data(user_id: int) -> Dict[str, Any]:
    """Удаляет все задания, файлы макетов и записи пользователя (GDPR / Privacy)."""
    jobs_dir = _get_jobs_dir()
    prefs_file = _get_prefs_file()
    users_file = _get_users_file()

    uid_int = int(user_id)
    uid_str = str(user_id)
    deleted_jobs = 0

    if jobs_dir.exists():
        for jdir in list(jobs_dir.iterdir()):
            if not jdir.is_dir():
                continue
            meta_file = jdir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = load_json_file(meta_file, {})
                if int(meta.get("user_id", 0)) == uid_int:
                    shutil.rmtree(jdir, ignore_errors=True)
                    deleted_jobs += 1
            except Exception as exc:
                logger.warning("Не удалось удалить задание пользователя %s из %s: %s", uid_int, jdir, exc)

    storage_jobs_deleted = storage_delete_user_jobs(uid_int)
    deleted_jobs = max(deleted_jobs, storage_jobs_deleted)

    user_prefs_deleted = storage_delete_user_prefs(uid_int)
    if prefs_file.exists():
        prefs = load_json_file(prefs_file, {})
        if uid_str in prefs:
            del prefs[uid_str]
            save_json_file(prefs_file, prefs)
            user_prefs_deleted = True

    user_record_deleted = storage_delete_user_record(uid_int)
    if users_file.exists():
        users = load_json_file(users_file, {})
        if uid_str in users:
            del users[uid_str]
            save_json_file(users_file, users)
            user_record_deleted = True

    try:
        import telegram_bot

        limiter = getattr(telegram_bot, "conversion_limiter", None)
        if limiter and hasattr(limiter, "_history"):
            with limiter._lock:
                limiter._history.pop(uid_int, None)
                limiter._active_users.discard(uid_int)
    except Exception:
        pass

    logger.info(
        "Данные пользователя %s удалены: заданий=%d, запись=%s, настройки=%s",
        uid_int,
        deleted_jobs,
        user_record_deleted,
        user_prefs_deleted,
    )
    return {
        "user_id": uid_int,
        "deleted_jobs": deleted_jobs,
        "deleted_user_record": user_record_deleted,
        "deleted_user_prefs": user_prefs_deleted,
    }


def format_privacy_text() -> str:
    """Формирует подробный текст политики конфиденциальности и обработки данных."""
    retention_days = JOB_RETENTION_HOURS / 24.0
    if retention_days.is_integer():
        retention_str = f"{int(retention_days)} дн. ({int(JOB_RETENTION_HOURS)} ч.)"
    else:
        retention_str = f"{retention_days:.1f} дн. ({int(JOB_RETENTION_HOURS)} ч.)"

    return (
        "🔒 <b>Политика конфиденциальности и обработки данных</b>\n\n"
        "1. <b>Обработка файлов:</b>\n"
        "Ваши изображения и документы обрабатываются на сервере исключительно для создания контура реза CutContour "
        "и подготовки файлов .EPS для плоттера Roland.\n\n"
        f"2. <b>Срок хранения файлов:</b>\n"
        f"Все файлы макетов хранятся на сервере <b>{retention_str}</b>, после чего автоматически и безвозвратно удаляются фоновой службой.\n\n"
        "3. <b>Просмотр администратором (Live-мониторинг):</b>\n"
        "Администратор сервиса может просматривать загруженные макеты для обработки заказов, проверки качества контуров "
        "и отправки на печать. Режим Live-мониторинга включается администратором осознанно вручную.\n\n"
        "4. <b>Удаление данных (/delete_my_data):</b>\n"
        "Вы можете в любой момент удалить все свои задания, файлы макетов, сохранённые настройки и профиль командой "
        "/delete_my_data или кнопкой ниже."
    )


PREFS_FILE = WORK_DIR / "user_prefs.json"
USERS_FILE = WORK_DIR / "users_db.json"
ADMIN_STATE_FILE = WORK_DIR / "admin_state.json"
db_lock = threading.RLock()


def _get_prefs_file() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "PREFS_FILE", PREFS_FILE))
    except Exception:
        return PREFS_FILE


def _get_users_file() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "USERS_FILE", USERS_FILE))
    except Exception:
        return USERS_FILE


def _get_admin_state_file() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "ADMIN_STATE_FILE", ADMIN_STATE_FILE))
    except Exception:
        return ADMIN_STATE_FILE


def _get_db_lock() -> threading.RLock:
    try:
        import telegram_bot

        return getattr(telegram_bot, "db_lock", db_lock)
    except Exception:
        return db_lock


def get_admin_state() -> Dict[str, Any]:
    return storage_get_admin_state()


def update_admin_state(**kwargs: Any) -> Dict[str, Any]:
    state = storage_update_admin_state(**kwargs)
    try:
        with _get_db_lock():
            save_json_file(_get_admin_state_file(), state)
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
        with _get_db_lock():
            save_json_file(_get_admin_state_file(), storage_get_admin_state())
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
        with _get_db_lock():
            users = load_json_file(_get_users_file(), {})
            users[str(user_id)] = entry
            save_json_file(_get_users_file(), users)
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
        with _get_db_lock():
            save_json_file(_get_prefs_file(), data)
    except Exception:
        pass


def get_user_prefs(user_id: int) -> Dict[str, Any]:
    return storage_get_user_prefs(user_id)


def update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    prefs = storage_update_user_prefs(user_id, **kwargs)
    try:
        with _get_db_lock():
            all_prefs = load_json_file(_get_prefs_file(), {})
            all_prefs[str(user_id)] = prefs
            save_json_file(_get_prefs_file(), all_prefs)
    except Exception:
        pass
    return prefs

