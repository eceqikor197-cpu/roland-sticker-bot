#!/usr/bin/env python3
"""
Единый HTTP-клиент для парсеров с поддержкой локального прокси Xray (127.0.0.1:10809)
и автоматическим переключением на прямое соединение, если прокси недоступен.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import requests

from harvester.config import DEFAULT_PROXY_URL, TMP_DOWNLOAD_DIR

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


@dataclass
class CandidateImage:
    url: str
    title: str
    source_name: str
    fallback_url: str = ""
    is_telegram_sticker: bool = False


def get_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if extra:
        h.update(extra)
    return h


# Глобальные сессии с пулом TCP/TLS-соединений (переиспользуются между запросами)
_session_proxy: Optional[requests.Session] = None
_session_direct: Optional[requests.Session] = None


def _get_session(use_proxy: bool) -> requests.Session:
    """Возвращает (и лениво создаёт) глобальную requests.Session с пулом соединений."""
    global _session_proxy, _session_direct
    if use_proxy and DEFAULT_PROXY_URL:
        if _session_proxy is None:
            _session_proxy = requests.Session()
            _session_proxy.proxies = {"http": DEFAULT_PROXY_URL, "https": DEFAULT_PROXY_URL}
        return _session_proxy
    else:
        if _session_direct is None:
            _session_direct = requests.Session()
        return _session_direct


def http_get(
    url: str,
    params: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 14.0,
    use_proxy: bool = True,
) -> Optional[requests.Response]:
    req_headers = get_headers(headers)
    try:
        session = _get_session(use_proxy)
        resp = session.get(
            url,
            params=params,
            headers=req_headers,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp
    except Exception:
        pass

    # Фоллбэк: если через прокси не получилось — пробуем напрямую
    if use_proxy and DEFAULT_PROXY_URL:
        try:
            session = _get_session(use_proxy=False)
            resp = session.get(
                url,
                params=params,
                headers=req_headers,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
    return None


def download_image_to_tmp(url: str, fallback_url: str = "") -> Optional[Path]:
    """
    Скачивает изображение по URL (при неудаче пробует fallback_url) во временный файл.
    Проверяет минимальный размер файла (> 8 КБ) и заголовок.
    """
    for target in (url, fallback_url):
        if not target:
            continue
        resp = http_get(target, timeout=18.0, use_proxy=True)
        if resp is None or not resp.content or len(resp.content) < 8192:
            continue
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            continue

        ext = ".jpg"
        if ".png" in target.lower() or "png" in ctype:
            ext = ".png"
        elif ".webp" in target.lower() or "webp" in ctype:
            ext = ".webp"

        TMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TMP_DOWNLOAD_DIR / f"dl_{uuid.uuid4().hex[:12]}{ext}"
        try:
            out_path.write_bytes(resp.content)
            return out_path
        except Exception:
            out_path.unlink(missing_ok=True)
    return None
