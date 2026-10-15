#!/usr/bin/env python3
"""
Конфигурация автономного парсера и полиграфического конвейера 24/7 (Sticker Harvester).
Режим работы:
1. Отдельный поиск цельных листов-стикерпаков (несколько стикеров на листе -> разметка CutContour + PerfCutContour без разрезания на мелкие куски).
2. Отдельный поиск одиночных стикеров и авто-наклеек (1 крупный качественный стикер на листе -> чистый единый контур CutContour).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = BASE_DIR / "bot_workspace"
HARVESTER_DIR = WORK_DIR / "harvester"
DB_PATH = HARVESTER_DIR / "harvester.db"
STAGING_DIR = HARVESTER_DIR / "staging"
PREVIEWS_DIR = HARVESTER_DIR / "previews"
TMP_DOWNLOAD_DIR = HARVESTER_DIR / "tmp_downloads"
MODELS_DIR = BASE_DIR / "models"
U2NETP_ONNX_PATH = MODELS_DIR / "u2netp.onnx"

from config import (
    HOT_FOLDER_ROOT as HOT_FOLDER_ROOT,
    HOT_PREVIEWS_DIR as HOT_PREVIEWS_DIR,
    HOT_QUEUE_EPS_DIR as HOT_QUEUE_EPS_DIR,
)

for _d in (
    WORK_DIR,
    HARVESTER_DIR,
    STAGING_DIR,
    PREVIEWS_DIR,
    TMP_DOWNLOAD_DIR,
    MODELS_DIR,
    HOT_QUEUE_EPS_DIR,
    HOT_PREVIEWS_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

# Настройки облака rclone (Google Drive 5 TB на сервере crybaby)
RCLONE_BIN = os.getenv("RCLONE_BIN", "/usr/bin/rclone")
RCLONE_REMOTE = os.getenv("HARVESTER_RCLONE_REMOTE", "gdrive:Roland_Print_Base")

# Локальный прокси Xray на сервере crybaby для обхода блокировок Pinterest / Telegram / поиска
DEFAULT_PROXY_URL = os.getenv("HARVESTER_PROXY", "http://127.0.0.1:10809")

# Токен Telegram-бота (ОБЯЗАТЕЛЬНО задать через переменную окружения или .env файл!)
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

from config import (
    HARVESTER_ENABLED as HARVESTER_ENABLED,
    JOB_RETENTION_HOURS as JOB_RETENTION_HOURS,
    LIVE_SPY_DEFAULT as LIVE_SPY_DEFAULT,
    PUBLIC_CATALOG as PUBLIC_CATALOG,
)

BOT_TOKEN = os.environ.get("PLOTTER_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    import warnings
    warnings.warn(
        "⚠️ PLOTTER_BOT_TOKEN не задан! Создайте файл .env в корне проекта "
        "или задайте переменную окружения PLOTTER_BOT_TOKEN=<ваш_токен>",
        stacklevel=2,
    )

# Ограничения диска и памяти на сервере crybaby
MIN_FREE_DISK_GB = 12.0
MAX_LOCAL_STAGING_MB = 2500.0
PHASH_HAMMING_THRESHOLD = 5


@dataclass(frozen=True)
class CategoryPrintProfile:
    """Профиль физической подготовки макета для плоттера Roland по категории."""
    category_lvl1: str
    pack_sheet_size_mm: float      # Размер цельного листа-стикерпака по большей стороне (мм)
    single_sticker_size_mm: float  # Размер одиночного стикера / наклейки по большей стороне (мм)
    offset_mm: float               # Отступ контура CutContour (мм)
    pack_perf_mode: str            # Режим PerfCut для листа-стикерпака ('sheet_frame')
    single_perf_mode: str          # Режим PerfCut для одиночного стикера ('die_cut' или 'none')
    keep_background: bool
    min_sheet_px: int              # Мин. разрешение большей стороны для листа-стикерпака
    min_single_px: int             # Мин. разрешение большей стороны для одиночного стикера


CATEGORY_PROFILES: Dict[str, CategoryPrintProfile] = {
    "01_Стикерпаки": CategoryPrintProfile(
        category_lvl1="01_Стикерпаки",
        pack_sheet_size_mm=250.0,      # Лист стикерпака ~25 см (под А4)
        single_sticker_size_mm=100.0,  # Одиночный стикер 10 см
        offset_mm=2.5,
        pack_perf_mode="sheet_frame",
        single_perf_mode="die_cut",
        keep_background=False,
        min_sheet_px=720,
        min_single_px=580,
    ),
    "02_Авто_Наклейки": CategoryPrintProfile(
        category_lvl1="02_Авто_Наклейки",
        pack_sheet_size_mm=280.0,      # Лист авто-наклеек 28 см
        single_sticker_size_mm=180.0,  # Одиночная авто-наклейка 18 см
        offset_mm=2.5,
        pack_perf_mode="sheet_frame",
        single_perf_mode="die_cut",
        keep_background=False,
        min_sheet_px=720,
        min_single_px=580,
    ),
    "03_Одиночные_Стикеры": CategoryPrintProfile(
        category_lvl1="03_Одиночные_Стикеры",
        pack_sheet_size_mm=250.0,
        single_sticker_size_mm=100.0,
        offset_mm=2.5,
        pack_perf_mode="sheet_frame",
        single_perf_mode="die_cut",
        keep_background=False,
        min_sheet_px=720,
        min_single_px=580,
    ),
    "04_Баннеры_и_Вывески": CategoryPrintProfile(
        category_lvl1="04_Баннеры_и_Вывески",
        pack_sheet_size_mm=300.0,
        single_sticker_size_mm=300.0,
        offset_mm=0.0,
        pack_perf_mode="none",
        single_perf_mode="none",
        keep_background=True,
        min_sheet_px=900,
        min_single_px=800,
    ),
}


def get_profile_for_category(category_lvl1: str) -> CategoryPrintProfile:
    return CATEGORY_PROFILES.get(category_lvl1, CATEGORY_PROFILES["01_Стикерпаки"])
