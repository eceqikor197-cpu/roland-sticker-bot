#!/usr/bin/env python3
"""
Парсер официальных стикерпаков Telegram:
1. Находит короткие имена стикерпаков (t.me/addstickers/<short_name>) по теме через поиск.
2. Запрашивает через официальное Telegram Bot API метод getStickerSet.
3. Отбирает статичные стикеры высокого качества (512px WebP/PNG с идеальным альфа-каналом без фона)
   и получает прямые ссылки на файлы через getFile.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from harvester.config import BOT_TOKEN
from harvester.database import is_url_seen, record_seen_url
from harvester.filters.topic_filter import is_candidate_relevant_to_topic
from harvester.scrapers.http_client import CandidateImage, http_get


def discover_telegram_sticker_packs(query: str, max_packs: int = 4) -> List[str]:
    """Ищет названия стикерпаков t.me/addstickers/<pack_name> по ключевому запросу."""
    clean_q = re.sub(r"\b(sticker pack|stickers sheet|printable|vector|стикерпак|наклейки)\b", "", query, flags=re.I).strip()
    if not clean_q:
        clean_q = query

    pack_names: List[str] = []

    # 1. DuckDuckGo HTML поиск по t.me/addstickers
    ddg_resp = http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": f"site:t.me/addstickers {clean_q}"},
        timeout=12.0,
        use_proxy=True,
    )
    if ddg_resp is not None:
        found = re.findall(r"t\.me/addstickers/([a-zA-Z0-9_]{3,60})", ddg_resp.text)
        for p in found:
            if p not in pack_names:
                pack_names.append(p)

    # 2. Добор через Bing при необходимости
    if len(pack_names) < max_packs:
        bing_resp = http_get(
            "https://www.bing.com/search",
            params={"q": f"site:t.me/addstickers {clean_q}"},
            timeout=12.0,
            use_proxy=True,
        )
        if bing_resp is not None:
            found = re.findall(r"t\.me/addstickers/([a-zA-Z0-9_]{3,60})", bing_resp.text)
            for p in found:
                if p not in pack_names:
                    pack_names.append(p)

    return pack_names[:max_packs]


def fetch_stickers_from_telegram_pack(
    pack_name: str,
    max_stickers_per_pack: int = 12,
    topic: Optional[Dict[str, Any]] = None,
) -> List[CandidateImage]:
    """
    Через Telegram Bot API (getStickerSet + getFile) получает прямые ссылки на статичные стикеры пака.
    Если передан `topic`, проверяет, что название пака (`pack_title` / `pack_name`) строго относится к теме!
    """
    if not BOT_TOKEN:
        return []

    pack_marker_url = f"https://t.me/addstickers/{pack_name}"
    if is_url_seen(pack_marker_url):
        return []

    api_base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    resp = http_get(
        f"{api_base}/getStickerSet",
        params={"name": pack_name},
        timeout=12.0,
        use_proxy=True,
    )
    if resp is None:
        return []

    try:
        data = resp.json()
        if not data.get("ok"):
            record_seen_url(pack_marker_url, status="invalid_pack")
            return []

        result = data.get("result") or {}
        pack_title = str(result.get("title") or pack_name).strip()
        stickers = result.get("stickers") or []

        # Строгая проверка релевантности пака теме (отсекает чужие паки с каталогов)
        if topic is not None:
            is_rel, rel_reason = is_candidate_relevant_to_topic(
                topic=topic,
                cand_title=f"{pack_title} | {pack_name}",
                cand_url=pack_marker_url,
            )
            if not is_rel:
                record_seen_url(pack_marker_url, status="rejected_offtopic_pack", reason=rel_reason)
                return []

        candidates: List[CandidateImage] = []
        for idx, st in enumerate(stickers, start=1):
            if len(candidates) >= max_stickers_per_pack:
                break
            # Берём только статичные растровые стикеры (не TGS-анимации и не WEBM-видео)
            if st.get("is_animated") or st.get("is_video"):
                continue

            file_id = st.get("file_id")
            if not file_id:
                continue

            f_resp = http_get(
                f"{api_base}/getFile",
                params={"file_id": file_id},
                timeout=10.0,
                use_proxy=True,
            )
            if f_resp is None:
                continue
            f_data = f_resp.json()
            file_path = (f_data.get("result") or {}).get("file_path")
            if not file_path:
                continue
            if not file_path.lower().endswith((".webp", ".png", ".jpg")):
                continue

            dl_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            if is_url_seen(dl_url):
                continue

            candidates.append(
                CandidateImage(
                    url=dl_url,
                    title=f"{pack_title} #{idx} ({pack_name})",
                    source_name="tg_stickers",
                    is_telegram_sticker=True,
                )
            )

        record_seen_url(pack_marker_url, status="harvested_pack")
        return candidates
    except Exception:
        return []


def search_telegram_stickers_for_query(
    query: str,
    max_total: int = 12,
    topic: Optional[Dict[str, Any]] = None,
) -> List[CandidateImage]:
    packs = discover_telegram_sticker_packs(query, max_packs=4)
    out: List[CandidateImage] = []
    for pname in packs:
        items = fetch_stickers_from_telegram_pack(
            pname,
            max_stickers_per_pack=max_total - len(out),
            topic=topic,
        )
        out.extend(items)
        if len(out) >= max_total:
            break
    return out
