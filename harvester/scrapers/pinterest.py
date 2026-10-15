#!/usr/bin/env python3
"""
Парсер Pinterest (BaseSearchResource JSON API + граф похожих пинов + апгрейд до originals).
Извлекает изображения в максимальном разрешении (https://i.pinimg.com/originals/...)
и собирает новые смежные темы из названий досок и описаний пинов.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import List, Tuple

from harvester.database import is_url_seen
from harvester.scrapers.http_client import CandidateImage, http_get

PINTEREST_HEADERS = {
    "X-Pinterest-AppState": "active",
    "X-Pinterest-PWS-Handler": "www/search/[scope].js",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.pinterest.com/",
}


def upgrade_to_pinimg_original(url: str) -> Tuple[str, str]:
    """
    Превращает любую ссылку превью i.pinimg.com/(236x|474x|564x|736x)/...
    в пару (original_url, fallback_736x_url).
    """
    m = re.match(
        r"^https?://i\.pinimg\.com/(?:236x|474x|564x|736x|originals)/(.+)$",
        url.strip(),
    )
    if not m:
        return url, ""
    tail = m.group(1)
    return f"https://i.pinimg.com/originals/{tail}", f"https://i.pinimg.com/736x/{tail}"


def search_pinterest_pins(
    query: str,
    max_results: int = 24,
) -> Tuple[List[CandidateImage], List[str]]:
    """
    Выполняет поиск пинов по запросу через внутренний JSON API Pinterest.
    Возвращает (список CandidateImage в оригинальном качестве, список найденных смежных тем/досок).
    """
    candidates: List[CandidateImage] = []
    discovered_boards: List[str] = []

    encoded_q = urllib.parse.quote(query)
    payload = {
        "options": {
            "query": query,
            "scope": "pins",
            "page_size": 25,
        },
        "context": {},
    }
    resp = http_get(
        "https://www.pinterest.com/resource/BaseSearchResource/get/",
        params={
            "source_url": f"/search/pins/?q={encoded_q}",
            "data": json.dumps(payload, separators=(",", ":")),
        },
        headers=PINTEREST_HEADERS,
        timeout=14.0,
        use_proxy=True,
    )

    if resp is not None:
        try:
            data = resp.json()
            results = (
                data.get("resource_response", {})
                .get("data", {})
                .get("results", [])
            )
            for item in results:
                if not isinstance(item, dict):
                    continue
                if item.get("type") and item.get("type") != "pin":
                    continue
                images = item.get("images") or {}
                orig_info = images.get("orig") or {}
                url_736 = (images.get("736x") or {}).get("url") or ""
                orig_url = orig_info.get("url") or ""

                if not orig_url and url_736:
                    orig_url, url_736 = upgrade_to_pinimg_original(url_736)
                elif orig_url:
                    _, fallback_736 = upgrade_to_pinimg_original(orig_url)
                    url_736 = url_736 or fallback_736

                if not orig_url or is_url_seen(orig_url):
                    continue

                w = int(orig_info.get("width") or 0)
                h = int(orig_info.get("height") or 0)
                if w and h and max(w, h) < 500:
                    continue

                board = item.get("board") or {}
                bname = str(board.get("name") or "").strip() if isinstance(board, dict) else ""
                if bname and 3 <= len(bname) <= 40:
                    discovered_boards.append(bname)

                pin_join = item.get("pin_join") or {}
                vis_ann = pin_join.get("visual_annotation") if isinstance(pin_join, dict) else []
                vis_str = " ".join(str(x).strip() for x in (vis_ann or []) if isinstance(x, str))

                raw_gt = item.get("grid_title")
                raw_t = item.get("title")
                raw_d = item.get("description")
                raw_alt = item.get("auto_alt_text") or item.get("seo_alt_text")

                title_parts = [
                     raw_gt.strip() if isinstance(raw_gt, str) else "",
                    raw_t.strip() if isinstance(raw_t, str) else "",
                    raw_d.strip() if isinstance(raw_d, str) else "",
                    bname,
                    vis_str,
                    raw_alt.strip() if isinstance(raw_alt, str) else "",
                ]
                # Сохраняем только реальные метаданные пина (НЕ подставляем query при пустом заголовке!)
                title = " | ".join([p for p in title_parts if p])[:320]

                candidates.append(
                    CandidateImage(
                        url=orig_url,
                        fallback_url=url_736,
                        title=title,
                        source_name="pinterest",
                    )
                )
                if len(candidates) >= max_results:
                    break
        except Exception:
            pass

    # Резервный добор оригиналов Pinterest через индекс, если API вернул мало результатов
    if len(candidates) < 6:
        b_resp = http_get(
            "https://www.bing.com/images/async",
            params={
                "q": f"site:pinterest.com {query}",
                "first": "0",
                "count": "30",
                "qft": "+filterui:imagesize-large",
            },
            timeout=12.0,
            use_proxy=True,
        )
        if b_resp is not None:
            import html as _html
            for m in re.finditer(r"murl&quot;:&quot;(https?://i\.pinimg\.com/[^&]+?)&quot;", b_resp.text):
                u = _html.unescape(m.group(1)).strip()
                orig_u, fb_u = upgrade_to_pinimg_original(u)
                if is_url_seen(orig_u):
                    continue
                # Извлекаем реальный заголовок и URL страницы вокруг найденного murl
                ctx_start = max(0, m.start() - 350)
                ctx_end = min(len(b_resp.text), m.end() + 450)
                snippet = b_resp.text[ctx_start:ctx_end]
                mt = re.search(r"t&quot;:&quot;([^&]*?)&quot;", snippet)
                mp = re.search(r"purl&quot;:&quot;([^&]*?)&quot;", snippet)
                real_title = _html.unescape(mt.group(1)).strip() if mt else ""
                page_url = _html.unescape(mp.group(1)).strip() if mp else ""
                combined_title = " | ".join([x for x in (real_title, page_url) if x])[:260]
                candidates.append(
                    CandidateImage(
                        url=orig_u,
                        fallback_url=fb_u,
                        title=combined_title,
                        source_name="pinterest",
                    )
                )
                if len(candidates) >= max_results:
                    break

    return candidates, discovered_boards
