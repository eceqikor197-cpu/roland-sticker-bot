#!/usr/bin/env python3
"""
Главный демон 24/7 (Sticker Harvester Worker):
Непрерывно обходит очередь из 625+ тем (и срочные заказы из Telegram-бота):
1. Отдельно ищет цельные листы-стикерпаки высокого разрешения (размечает CutContour + рамку листа PerfCutContour, НЕ разрезая их на мелкие куски).
2. Отдельно ищет одиночные крутые стикеры и авто-наклейки по 1 штуке на листе (размечает единый гладкий контур CutContour).
3. Пропускает только чистые макеты на белом или прозрачном фоне без потери оригинального качества и выгружает в Google Drive (gdrive:Roland_Print_Base).
"""

from __future__ import annotations

import argparse
import gc
import html
import random
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from harvester.config import BOT_TOKEN, RCLONE_REMOTE, TMP_DOWNLOAD_DIR, get_profile_for_category
from harvester.database import (
    get_conn,
    get_state,
    incr_state,
    init_db,
    is_url_seen,
    mark_topic_harvested,
    pick_next_topic,
    record_phash,
    record_seen_url,
    set_state,
    upsert_topic,
)
from harvester.filters.quality_gate import evaluate_image_quality
from harvester.filters.topic_filter import is_candidate_relevant_to_topic, is_cyrillic_text
from harvester.processor.eps_pipeline import process_validated_image_into_eps_assets
from harvester.scrapers.http_client import CandidateImage, download_image_to_tmp
from harvester.scrapers.pinterest import search_pinterest_pins
from harvester.scrapers.tg_stickers import search_telegram_stickers_for_query
from harvester.scrapers.web_clipart import search_highres_web_images
from harvester.seeds import classify_user_topic, seed_database_if_needed
from harvester.storage.rclone_sync import sync_staging_to_gdrive

ADMIN_CHAT_ID = 870592714


def _build_pack_and_single_queries(topic: Dict[str, Any]) -> List[str]:
    """
    Формирует для темы сбалансированный набор запросов на языке темы:
    1) Запрос на поиск цельных листов-стикерпаков (несколько стикеров на 1 листе)
    2) Запрос на поиск одиночных стикеров по 1 штуке на листе (персонажи, мемы, авто-наклейки).
    ВАЖНО: для русскоязычных тем запросы формируются на русском языке без примеси длинных
    английских хвостов, чтобы поисковики не выдавали случайные пины по английским словам!
    """
    base_queries: List[str] = topic.get("search_queries") or [topic["topic_name"]]
    raw_first = base_queries[0]
    core_kw = re.sub(
        r"\b(sticker pack|stickers sheet|sticker sheet|printable|vector|car sticker|decal|стикерпак|стикеры|наклейки|наклейка)\b",
        "",
        raw_first,
        flags=re.I,
    ).strip()
    if not core_kw:
        core_kw = topic["topic_name"].replace("_", " ")

    cat1 = topic["category_lvl1"]
    if cat1 == "04_Баннеры_и_Вывески":
        return base_queries

    has_cyr = is_cyrillic_text(core_kw)

    if cat1 == "02_Авто_Наклейки":
        if has_cyr:
            return [
                f"{core_kw} наклейка на авто на белом фоне",
                f"{core_kw} стикерпак наклейки на авто",
                raw_first,
            ]
        return [
            f"{core_kw} die cut car sticker white background",
            f"{core_kw} car decals sticker sheet",
            raw_first,
        ]

    # Для стикерпаков и одиночных стикеров ищем И цельные паки на листе, И одиночные стикеры персонажей
    if has_cyr:
        return [
            f"{core_kw} стикерпак наклейки",
            f"{core_kw} стикеры наклейки",
            raw_first,
        ]
    return [
        f"{core_kw} sticker pack sheet white background",
        f"{core_kw} single die cut sticker white background",
        raw_first,
    ]


def _notify_admin_urgent_topic_done(
    topic: Dict[str, Any],
    created_assets: List[Dict[str, Any]],
) -> None:
    if not BOT_TOKEN or not created_assets:
        return
    try:
        import telebot
        from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

        tbot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
        cat1 = topic["category_lvl1"]
        cat2 = topic["category_lvl2"]
        slug = topic["folder_slug"]
        tname = topic["topic_name"]

        packs = [a for a in created_assets if a["asset_type"] == "pack_sheet"]
        singles = [a for a in created_assets if a["asset_type"] in ("single_sticker", "car_decal")]
        banners = [a for a in created_assets if a["asset_type"] == "banner"]

        caption = (
            f"🔥 <b>Срочный сбор по теме «{html.escape(tname)}» завершён!</b>\n\n"
            f"📂 Папка в облаке: <code>{html.escape(RCLONE_REMOTE)}/{html.escape(cat1)}/{html.escape(cat2)}/{html.escape(slug)}</code>\n"
            f"📦 Цельных листов-стикерпаков (.EPS): <b>{len(packs)} шт.</b>\n"
            f"✂️ Одиночных стикеров / наклеек (.EPS): <b>{len(singles)} шт.</b>\n"
            + (f"🪧 Баннеров: <b>{len(banners)} шт.</b>\n" if banners else "")
            + "☁️ Все файлы в 100% оригинальном качестве выгружены на Google Диск!"
        )

        kb = InlineKeyboardMarkup()
        sample_asset = packs[0] if packs else created_assets[0]
        aid = sample_asset["asset_id"]
        kb.row(
            InlineKeyboardButton("📥 Скачать .EPS", callback_data=f"harv:dl_eps:{aid}"),
            InlineKeyboardButton("🚀 В Hot Folder", callback_data=f"harv:hot:{aid}"),
        )
        kb.row(
            InlineKeyboardButton("🚜 Открыть пульт Парсера", callback_data="harv:home"),
        )

        prev_path = Path(sample_asset.get("local_preview_path", ""))
        if prev_path.exists():
            with open(prev_path, "rb") as pf:
                tbot.send_photo(ADMIN_CHAT_ID, photo=pf, caption=caption, reply_markup=kb)
        else:
            tbot.send_message(ADMIN_CHAT_ID, caption, reply_markup=kb)
    except Exception:
        pass


def harvest_single_topic(
    topic: Dict[str, Any],
    max_sources_accept: int = 5,
    sync_after: bool = True,
) -> List[Dict[str, Any]]:
    """
    Выполняет цикл сбора для выбранной темы:
    Ищет отдельно цельные стикерпаки на листе и отдельно одиночные стикеры по 1 штуке.
    """
    cat1 = topic["category_lvl1"]
    cat2 = topic["category_lvl2"]
    tname = topic["topic_name"]
    is_urgent = int(topic.get("priority", 10)) >= 100
    profile = get_profile_for_category(cat1)

    set_state("current_topic", tname)
    set_state("current_category", f"{cat1} / {cat2}")
    set_state("status_text", f"Поиск паков и одиночных стикеров: {tname}")
    set_state("last_heartbeat", time.time())

    smart_queries = _build_pack_and_single_queries(topic)
    pack_query = smart_queries[0]
    single_query = smart_queries[1] if len(smart_queries) > 1 else smart_queries[0]

    # 1. Ищем цельные листы-стикерпаки (Pinterest Originals + High-Res Web)
    set_state("current_source", "Pinterest (Стикерпаки)")
    pin_packs, _ = search_pinterest_pins(pack_query, max_results=16)
    web_packs = search_highres_web_images(pack_query, category_lvl1=cat1, max_results=10)

    # 2. Ищем одиночные стикеры по 1 штуке на листе (Pinterest Originals + High-Res Web + Telegram)
    set_state("current_source", "Pinterest (Одиночные стикеры)")
    pin_singles, _ = search_pinterest_pins(single_query, max_results=16)

    set_state("current_source", "Web High-Res Clipart")
    web_singles = search_highres_web_images(single_query, category_lvl1=cat1, max_results=12)

    tg_cands: List[CandidateImage] = []
    if cat1 in ("01_Стикерпаки", "03_Одиночные_Стикеры"):
        set_state("current_source", "Telegram Single Stickers")
        tg_cands = search_telegram_stickers_for_query(
            tname.replace("_", " "),
            max_total=8,
            topic=topic,
        )

    # Чередуем кандидатов из паков и одиночных стикеров, чтобы в каждой теме собирались ОБА типа
    pack_pool = pin_packs + web_packs
    single_pool = pin_singles + web_singles + tg_cands
    candidates: List[CandidateImage] = []
    max_len = max(len(pack_pool), len(single_pool))
    for idx in range(max_len):
        if idx < len(pack_pool):
            candidates.append(pack_pool[idx])
        if idx < len(single_pool):
            candidates.append(single_pool[idx])

    all_created: List[Dict[str, Any]] = []
    accepted_sources = 0

    for cand in candidates:
        if not is_urgent and not bool(get_state("enabled", True)):
            break

        if is_url_seen(cand.url):
            continue

        # СТРОГИЙ ФИЛЬТР РЕЛЕВАНТНОСТИ ТЕМЕ (до скачивания файла!)
        is_rel, rel_reason = is_candidate_relevant_to_topic(
            topic=topic,
            cand_title=cand.title,
            cand_url=cand.url,
        )
        if not is_rel:
            record_seen_url(cand.url, status="rejected_offtopic", reason=rel_reason)
            incr_state("total_rejected_mockup", 1)
            continue

        set_state("last_heartbeat", time.time())
        set_state("current_source", cand.source_name)
        set_state("status_text", f"Проверка качества: {tname} ({cand.source_name})")

        tmp_file = download_image_to_tmp(cand.url, fallback_url=cand.fallback_url)
        if tmp_file is None:
            record_seen_url(cand.url, status="download_failed")
            continue

        incr_state("total_downloaded", 1)

        try:
            qres = evaluate_image_quality(
                image_path=tmp_file,
                profile=profile,
                title_or_desc=cand.title,
                allow_smaller_telegram_sticker=cand.is_telegram_sticker,
            )

            if not qres.passed:
                reason = qres.reason
                record_seen_url(cand.url, status="rejected", reason=reason)
                if "duplicate_phash" in reason:
                    incr_state("total_rejected_dup", 1)
                elif "low_res" in reason or "bad_aspect" in reason or "blurry" in reason:
                    incr_state("total_rejected_lowres", 1)
                else:
                    incr_state("total_rejected_mockup", 1)
                    if qres.phash_hex:
                        record_phash(qres.phash_hex, status="rejected")
                continue

            record_seen_url(cand.url, status="accepted", reason=qres.detected_mode)
            created = process_validated_image_into_eps_assets(
                qres=qres,
                topic=topic,
                source_name=cand.source_name,
                source_url=cand.url,
                raw_title=cand.title,
            )
            if created:
                all_created.extend(created)
                accepted_sources += 1

            time.sleep(random.uniform(0.8, 1.8))

            if accepted_sources >= max_sources_accept:
                break
        except Exception as exc:
            set_state("last_error", f"Ошибка обработки {cand.url[:60]}: {exc}")
        finally:
            tmp_file.unlink(missing_ok=True)
            if qres_path := getattr(locals().get("qres", None), "prepared_input_path", None):
                if qres_path != tmp_file:
                    Path(qres_path).unlink(missing_ok=True)
            gc.collect()

    added_packs = sum(1 for a in all_created if a["asset_type"] == "pack_sheet")
    added_items = len(all_created) - added_packs
    mark_topic_harvested(int(topic["id"]), added_items=added_items, added_packs=added_packs)

    if sync_after and all_created:
        set_state("status_text", f"Выгрузка {len(all_created)} макетов в Google Drive...")
        sync_staging_to_gdrive()

    if is_urgent and all_created:
        _notify_admin_urgent_topic_done(topic, all_created)

    return all_created


def run_forever_daemon() -> None:
    print("🚜 Инициализация базы данных и стартового каталога тем (625+ категорий)...")
    init_db()
    total_topics = seed_database_if_needed()
    print(f"✅ В базе готово {total_topics} тематических папок.")

    set_state("status_text", "Демон 24/7 запущен (Режим: Цельные Стикерпаки + Одиночные Стикеры)")
    set_state("last_heartbeat", time.time())

    while True:
        try:
            set_state("last_heartbeat", time.time())
            enabled = bool(get_state("enabled", True))

            topic = pick_next_topic()
            is_urgent = topic is not None and int(topic.get("priority", 10)) >= 100

            if not enabled and not is_urgent:
                set_state("status_text", "⏸ На паузе (ожидание команды в боте)")
                time.sleep(5.0)
                continue

            if topic is None:
                time.sleep(10.0)
                continue

            max_accept = 6 if is_urgent else 4
            print(f"🔍 Обход темы: [{topic['category_lvl1']} / {topic['category_lvl2']}] -> {topic['topic_name']} (prio={topic['priority']})")
            created = harvest_single_topic(topic, max_sources_accept=max_accept, sync_after=True)
            print(f"✅ Тема {topic['topic_name']}: создано {len(created)} макетов без потери качества (.EPS)")

            for leftover in TMP_DOWNLOAD_DIR.glob("dl_*"):
                try:
                    if time.time() - leftover.stat().st_mtime > 600:
                        leftover.unlink(missing_ok=True)
                except Exception:
                    pass

            gc.collect()
            time.sleep(random.uniform(2.5, 5.0))
        except KeyboardInterrupt:
            print("🛑 Остановка воркера по сигналу...")
            break
        except Exception as exc:
            traceback.print_exc()
            set_state("last_error", str(exc)[:250])
            time.sleep(8.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sticker Harvester 24/7 Daemon")
    parser.add_argument("--once", action="store_true", help="Выполнить 1 цикл по одной теме и завершиться")
    parser.add_argument("--topic", type=str, default="", help="Запустить сбор по конкретной теме")
    parser.add_argument("--max-accept", type=int, default=4, help="Максимум макетов за 1 цикл в режиме --once")
    args = parser.parse_args()

    init_db()
    seed_database_if_needed()

    if args.once:
        if args.topic:
            cat1, cat2, clean_name, queries = classify_user_topic(args.topic)
            tid = upsert_topic(cat1, cat2, clean_name, queries, priority=150)
            conn = get_conn()
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (tid,)).fetchone()
            conn.close()
            tdict = dict(row)
            tdict["search_queries"] = queries
        else:
            tdict = pick_next_topic()

        if not tdict:
            print("Нет доступных тем")
            return 1

        print(f"🚀 Тестовый прогон темы: {tdict['category_lvl1']} / {tdict['category_lvl2']} / {tdict['topic_name']}")
        created = harvest_single_topic(tdict, max_sources_accept=args.max_accept, sync_after=True)
        print(f"🎉 Создано и выгружено в Google Drive: {len(created)} макетов (.EPS + оригинал)")
        for a in created:
            print(f"  • [{a['asset_type']}] {a['rel_folder_path']}/{a['eps_filename']} ({a['width_mm']}x{a['height_mm']} мм, контуров: {a['num_contours']})")
        return 0

    run_forever_daemon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
