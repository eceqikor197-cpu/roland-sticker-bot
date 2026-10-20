"""Подготовка исходника для живого превью в мини-приложении.

Один раз на макет (и на режим фона) вырезает фон тем же кодом, что и конвертер (jpeg2eps.py),
обрезает картинку по силуэту и сохраняет в папку задачи:
  miniapp/art_<bg>.png     — рисунок с прозрачным фоном, не больше PREVIEW_MAX_PX по длинной стороне;
  miniapp/source_<bg>.json — размер рамки силуэта в пикселях исходника и внешние контуры силуэта
                             в долях от рамки (0..1), по ним браузер рисует подложку и контур реза.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from jpeg2eps import extract_foreground_mask, fill_external_silhouette, load_original_image

logger = logging.getLogger(__name__)

PREVIEW_MAX_PX = 1024
SOURCE_VERSION = 1
BG_TOLERANCE = 12.0


def _cache_paths(job_dir: Path, keep_background: bool) -> tuple[Path, Path]:
    tag = "bg" if keep_background else "cut"
    cache = job_dir / "miniapp"
    return cache / f"art_{tag}.png", cache / f"source_{tag}.json"


def prepare_source(job_dir: Path, input_filename: str, keep_background: bool) -> Dict[str, Any]:
    """Возвращает описание исходника для превью, при необходимости строит и кеширует его."""
    art_path, json_path = _cache_paths(job_dir, keep_background)
    if json_path.exists() and art_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if data.get("version") == SOURCE_VERSION:
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Кеш превью %s повреждён, пересобираю: %s", json_path, exc)

    input_path = job_dir / input_filename
    orig_rgb, alpha = load_original_image(input_path)
    fg_mask = extract_foreground_mask(orig_rgb, alpha, bg_tolerance=BG_TOLERANCE, keep_background=keep_background)
    silhouette = fill_external_silhouette(fg_mask)

    ys, xs = np.nonzero(silhouette)
    if len(xs) == 0:
        h, w = silhouette.shape[:2]
        x0, y0, x1, y1 = 0, 0, w, h
        silhouette = np.full_like(silhouette, 255)
        fg_mask = np.full_like(fg_mask, 255)
    else:
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
    crop_rgb = orig_rgb[y0:y1, x0:x1]
    crop_fg = fg_mask[y0:y1, x0:x1]
    crop_sil = silhouette[y0:y1, x0:x1]
    crop_h, crop_w = crop_sil.shape[:2]

    scale = min(1.0, PREVIEW_MAX_PX / float(max(crop_w, crop_h)))
    pw, ph = max(1, int(round(crop_w * scale))), max(1, int(round(crop_h * scale)))
    # Мягкий край как в конвертере (gaussian sigma=0.65), чтобы превью не было «рваным»
    soft_alpha = ndimage.gaussian_filter(crop_fg.astype(np.float32) / 255.0, sigma=0.65)
    rgba = np.dstack([crop_rgb, (soft_alpha * 255.0).clip(0, 255).astype(np.uint8)])
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    if scale < 1.0:
        rgba = cv2.resize(rgba, (pw, ph), interpolation=interp)
        sil_small = cv2.resize(crop_sil, (pw, ph), interpolation=cv2.INTER_AREA)
        _, sil_small = cv2.threshold(sil_small, 127, 255, cv2.THRESH_BINARY)
    else:
        sil_small = crop_sil

    polygons = silhouette_polygons(sil_small)

    art_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(art_path, format="PNG", optimize=True)
    data = {
        "version": SOURCE_VERSION,
        "keep_background": keep_background,
        "crop_w_px": int(crop_w),
        "crop_h_px": int(crop_h),
        "preview_w_px": int(pw),
        "preview_h_px": int(ph),
        "polygons": polygons,
    }
    json_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data


def artwork_path(job_dir: Path, keep_background: bool) -> Path:
    return _cache_paths(job_dir, keep_background)[0]


def silhouette_polygons(sil: np.ndarray, epsilon_px: float = 0.8, min_area_px: float = 12.0) -> List[List[List[float]]]:
    """Внешние контуры силуэта, упрощённые и нормированные к 0..1 по ширине и высоте."""
    h, w = sil.shape[:2]
    contours, _ = cv2.findContours(sil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out: List[List[List[float]]] = []
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area_px:
            continue
        approx = cv2.approxPolyDP(cnt, epsilon_px, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        # +0.5: центр пикселя, чтобы контур не смещался на полпикселя влево-вверх
        out.append([[round((float(x) + 0.5) / w, 5), round((float(y) + 0.5) / h, 5)] for x, y in approx])
    return out
