#!/usr/bin/env python3
"""
Полиграфический конвейер генерации .EPS (Lossless Print & Cut):
- НИКАКОГО разрезания стикерпаков на мелкие куски!
- В convert_image_to_plotter_eps подаётся 100% оригинальный нетронутый исходник высокого разрешения
  (точно так же, как при ручной отправке файла в Telegram-бота).
- Если макет распознан как цельный лист-стикерпак ('pack_sheet'):
  сохраняется в подпапку «Стикерпаки_Листы/» с разметкой CutContour вокруг каждого стикера и рамкой листа PerfCutContour.
- Если макет распознан как одиночный стикер / авто-наклейка ('single_sticker'):
  сохраняется в подпапку «Одиночные_Стикеры/» с единым гладким контуром CutContour + PerfCutContour (die_cut).
- После генерации .EPS выполняется финальная проверка числа построенных контуров реза:
  если одиночный стикер распался на > 2 контуров (мусор/текст без подложки), такой файл автоматически отбраковывается!
"""

from __future__ import annotations

import gc
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

from harvester.config import (
    PREVIEWS_DIR,
    RCLONE_REMOTE,
    STAGING_DIR,
    get_profile_for_category,
)
from harvester.database import (
    incr_state,
    record_phash,
    register_asset,
)
from harvester.filters.quality_gate import QualityGateResult
from jpeg2eps import MM_TO_PT, convert_image_to_plotter_eps


def _inspect_eps_quick(eps_path: Path) -> Tuple[int, float, float, float]:
    """Возвращает (num_contours, width_mm, height_mm, size_mb)."""
    if not eps_path.exists():
        return 1, 100.0, 100.0, 0.0
    try:
        size_mb = round(eps_path.stat().st_size / (1024 * 1024), 2)
        raw = eps_path.read_bytes()
        header = raw[:1200].decode("latin1", errors="ignore")
        m_bbox = re.search(
            r"%%HiResBoundingBox:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            header,
        )
        w_mm, h_mm = 100.0, 100.0
        if m_bbox:
            w_pt = float(m_bbox.group(3)) - float(m_bbox.group(1))
            h_pt = float(m_bbox.group(4)) - float(m_bbox.group(2))
            w_mm = round(w_pt / MM_TO_PT, 1)
            h_mm = round(h_pt / MM_TO_PT, 1)

        cut_marker = b"% --- Plotter CutContour Spot Color Vector Layer"
        cut_cnt = 1
        c_idx = raw.find(cut_marker)
        if c_idx != -1:
            c_end = raw.find(b"\r\ns\r\n", c_idx)
            c_sec = raw[c_idx:c_end] if c_end != -1 else raw[c_idx : c_idx + 200000]
            cut_cnt = max(1, len(re.findall(rb"\bcp\b", c_sec)))
        return cut_cnt, w_mm, h_mm, size_mb
    except Exception:
        return 1, 100.0, 100.0, 0.0


def _save_compact_preview_jpg(src_preview_png: Path, dest_jpg: Path, max_side: int = 800) -> None:
    if not src_preview_png.exists():
        return
    try:
        im = Image.open(src_preview_png).convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = float(max_side) / float(max(w, h))
            im = im.resize(
                (max(32, int(round(w * scale))), max(32, int(round(h * scale)))),
                Image.Resampling.LANCZOS,
            )
        dest_jpg.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest_jpg, format="JPEG", quality=88, optimize=True)
    except Exception:
        pass


def process_validated_image_into_eps_assets(
    qres: QualityGateResult,
    topic: Dict[str, Any],
    source_name: str,
    source_url: str,
    raw_title: str = "",
) -> List[Dict[str, Any]]:
    """
    Конвертирует 100% оригинальный файл (qres.prepared_input_path) в готовый .EPS
    без какого-либо разрезания листов на куски и с жёсткой проверкой чистоты контуров.
    """
    if not qres.passed or qres.prepared_input_path is None or not qres.prepared_input_path.exists():
        return []

    cat1 = topic["category_lvl1"]
    cat2 = topic["category_lvl2"]
    folder_slug = topic["folder_slug"]
    topic_id = int(topic["id"])
    topic_name = topic["topic_name"]
    profile = get_profile_for_category(cat1)

    asset_id = uuid.uuid4().hex[:10]
    short_slug = folder_slug[:28]
    mode = qres.detected_mode

    if mode == "pack_sheet":
        sub_dir_name = "Стикерпаки_Листы"
        asset_type = "pack_sheet"
        target_size_mm = profile.pack_sheet_size_mm
        target_offset_mm = profile.offset_mm
        target_perf_mode = profile.pack_perf_mode
        file_stem = f"{short_slug}_Pack_{asset_id[:6]}"
    elif mode == "banner" or cat1 == "04_Баннеры_и_Вывески":
        sub_dir_name = "Баннеры"
        asset_type = "banner"
        target_size_mm = profile.single_sticker_size_mm
        target_offset_mm = 0.0
        target_perf_mode = "none"
        file_stem = f"{short_slug}_Banner_{asset_id[:6]}"
    elif cat1 == "02_Авто_Наклейки":
        sub_dir_name = "Одиночные_Наклейки"
        asset_type = "car_decal"
        target_size_mm = profile.single_sticker_size_mm
        target_offset_mm = profile.offset_mm
        target_perf_mode = profile.single_perf_mode
        file_stem = f"{short_slug}_Auto_{asset_id[:6]}"
    else:
        sub_dir_name = "Одиночные_Стикеры"
        asset_type = "single_sticker"
        target_size_mm = profile.single_sticker_size_mm
        target_offset_mm = profile.offset_mm
        target_perf_mode = profile.single_perf_mode
        file_stem = f"{short_slug}_Sticker_{asset_id[:6]}"

    rel_folder = f"{cat1}/{cat2}/{folder_slug}/{sub_dir_name}"
    target_dir = STAGING_DIR / rel_folder
    target_dir.mkdir(parents=True, exist_ok=True)

    orig_ext = qres.prepared_input_path.suffix.lower() or ".jpg"
    saved_orig_path = target_dir / f"{file_stem}{orig_ext}"
    eps_path = target_dir / f"{file_stem}.eps"
    tmp_preview_png = target_dir / f".tmp_prev_{asset_id}.png"
    folder_preview_jpg = target_dir / f"{file_stem}_preview.jpg"
    catalog_preview_jpg = PREVIEWS_DIR / f"{asset_id}.jpg"

    # 1. Копируем 100% оригинальный файл без малейшего пережатия
    shutil.copy2(qres.prepared_input_path, saved_orig_path)

    # 2. Запускаем родной алгоритм jpeg2eps (точно как в Telegram-боте!)
    convert_image_to_plotter_eps(
        input_path=saved_orig_path,
        output_eps=eps_path,
        size_mm=target_size_mm,
        offset_mm=target_offset_mm,
        bg_tolerance=12.0,
        keep_background=profile.keep_background,
        no_cut_contour=False,
        also_svg=False,
        preview_png_path=tmp_preview_png,
        perf_cut_mode=target_perf_mode,
        nesting_qty=1,
    )

    num_contours, w_mm, h_mm, eps_mb = _inspect_eps_quick(eps_path)

    # 3. ФИНАЛЬНЫЙ КОНТРОЛЬ КАЧЕСТВА ПОСТРОЕННЫХ КОНТУРОВ РЕЗА:
    # Одиночный стикер ОБЯЗАН иметь 1 (максимум 2) цельный контур CutContour!
    # Если получилось 5, 10 или 20 контуров — значит это рассыпавшийся текст или грязный фон -> удаляем!
    if mode == "single_sticker" and num_contours > 2:
        saved_orig_path.unlink(missing_ok=True)
        eps_path.unlink(missing_ok=True)
        tmp_preview_png.unlink(missing_ok=True)
        incr_state("total_rejected_mockup", 1)
        record_phash(qres.phash_hex, status="rejected_fragmented_contours")
        return []

    # Лист-стикерпак ОБЯЗАН иметь от 3 до 24 чистых контуров стикеров (без сотни мелких осколков)
    if mode == "pack_sheet" and (num_contours < 3 or num_contours > 24):
        saved_orig_path.unlink(missing_ok=True)
        eps_path.unlink(missing_ok=True)
        tmp_preview_png.unlink(missing_ok=True)
        incr_state("total_rejected_mockup", 1)
        record_phash(qres.phash_hex, status="rejected_bad_sheet_contours")
        return []

    _save_compact_preview_jpg(tmp_preview_png, catalog_preview_jpg, max_side=780)
    _save_compact_preview_jpg(tmp_preview_png, folder_preview_jpg, max_side=960)
    tmp_preview_png.unlink(missing_ok=True)

    gdrive_folder = f"{RCLONE_REMOTE}/{rel_folder}"

    main_record = {
        "asset_id": asset_id,
        "topic_id": topic_id,
        "category_lvl1": cat1,
        "category_lvl2": cat2,
        "folder_slug": folder_slug,
        "topic_name": topic_name,
        "asset_type": asset_type,
        "title": (raw_title or f"{topic_name} ({file_stem})")[:140],
        "source_name": source_name,
        "source_url": source_url,
        "phash_hex": qres.phash_hex,
        "width_px": qres.width_px,
        "height_px": qres.height_px,
        "width_mm": w_mm,
        "height_mm": h_mm,
        "offset_mm": target_offset_mm,
        "perf_cut_mode": target_perf_mode,
        "num_contours": num_contours,
        "eps_size_mb": eps_mb,
        "rel_folder_path": rel_folder,
        "eps_filename": eps_path.name,
        "png_filename": saved_orig_path.name,
        "local_eps_path": str(eps_path),
        "local_png_path": str(saved_orig_path),
        "local_preview_path": str(catalog_preview_jpg),
        "gdrive_folder_path": gdrive_folder,
        "synced_to_gdrive": 0,
        "in_hot_folder": 0,
        "parent_asset_id": "",
        "created_at": time.time(),
    }
    register_asset(main_record)
    record_phash(qres.phash_hex, status="accepted", asset_id=asset_id)
    incr_state("total_eps_generated", 1)

    gc.collect()
    return [main_record]
