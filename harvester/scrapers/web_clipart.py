#!/usr/bin/env python3
"""
Парсер поисковой выдачи в режиме высокого разрешения (Bing Images Async Large / Clipart):
Отлично подходит для:
- Автомобильных наклеек (JDM, логотипы, русскоязычные надписи на заднее стекло, ВАЗ, BMW)
- Крупных стикерпаков с Etsy / Redbubble / StickerMule в высоком разрешении
- Баннеров, вывесок и ретро-постеров.
"""

from __future__ import annotations

import html
import re
from typing import List

from harvester.database import is_url_seen
from harvester.scrapers.http_client import CandidateImage, http_get
from harvester.scrapers.pinterest import upgrade_to_pinimg_original

SKIP_DOMAINS = (
    "shutterstock.com",
    "gettyimages.",
    "alamy.com",
    "123rf.com",
    "depositphotos.com",
    "dreamstime.com",
    "istockphoto.com",
    "youtube.com",
    "ytimg.com",
    "tiktok.com",
)


def search_highres_web_images(
    query: str,
    category_lvl1: str,
    max_results: int = 20,
) -> List[CandidateImage]:
    """
    Ищет изображения большого размера (+filterui:imagesize-large) по запросу.
    Для стикеров и авто-наклеек добавляет уточняющие ключи прозрачного/белого фона на языке запроса.
    """
    has_cyr = bool(re.search(r"[а-яёА-ЯЁ]", query))
    low_q = query.lower()
    if has_cyr:
        if "белом фоне" in low_q or "вывеска" in low_q or "плакат" in low_q:
            q_full = query
        elif category_lvl1 == "02_Авто_Наклейки":
            q_full = f"{query} наклейка на белом фоне"
        elif category_lvl1 == "04_Баннеры_и_Вывески":
            q_full = f"{query} плакат макет"
        else:
            q_full = f"{query} на белом фоне"
    else:
        if "white background" in low_q or "high resolution" in low_q:
            q_full = query
        elif category_lvl1 == "02_Авто_Наклейки":
            q_full = f"{query} sticker decal white background"
        elif category_lvl1 == "04_Баннеры_и_Вывески":
            q_full = f"{query} high resolution print"
        else:
            q_full = f"{query} white background printable"

    resp = http_get(
        "https://www.bing.com/images/async",
        params={
            "q": q_full,
            "first": "0",
            "count": "35",
            "qft": "+filterui:imagesize-large",
        },
        timeout=14.0,
        use_proxy=True,
    )
    if resp is None:
        return []

    candidates: List[CandidateImage] = []
    for m_url in re.finditer(r"murl&quot;:&quot;(https?://[^&]+?)&quot;", resp.text):
        raw_url = html.unescape(m_url.group(1)).strip()
        low_url = raw_url.lower()
        if any(d in low_url for d in SKIP_DOMAINS):
            continue

        fallback_u = ""
        if "i.pinimg.com" in low_url:
            raw_url, fallback_u = upgrade_to_pinimg_original(raw_url)

        if is_url_seen(raw_url):
            continue

        ctx_start = max(0, m_url.start() - 350)
        ctx_end = min(len(resp.text), m_url.end() + 450)
        snippet = resp.text[ctx_start:ctx_end]

        m_title = re.search(r"t&quot;:&quot;([^&]*?)&quot;", snippet)
        m_purl = re.search(r"purl&quot;:&quot;([^&]*?)&quot;", snippet)
        t_str = html.unescape(m_title.group(1)).strip() if m_title else ""
        p_str = html.unescape(m_purl.group(1)).strip() if m_purl else ""
        title = " | ".join([x for x in (t_str, p_str) if x])[:260]

        candidates.append(
            CandidateImage(
                url=raw_url,
                fallback_url=fallback_u,
                title=title,
                source_name="web_highres",
            )
        )
        if len(candidates) >= max_results:
            break

    return candidates
