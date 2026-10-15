#!/usr/bin/env python3
"""
Интеллектуальный модуль работы со стикерпаками:
1. Определяет, является ли изображение листом-стикерпаком (несколько отдельных наклеек на одном полотне)
   или одиночным макетом.
2. Аккуратно разрезает лист-стикерпак на отдельные прозрачные PNG-стикеры без захвата соседей.
3. Собирает отдельные штучные PNG-стикеры одной темы в готовый печатный лист-стикерпак (Nesting Grid).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image


def detect_and_split_sticker_sheet(
    cleaned_rgba: Image.Image,
    fg_mask: np.ndarray,
    min_item_px: int = 60,
    max_items: int = 16,
) -> Tuple[bool, List[Image.Image]]:
    """
    Анализирует очищенное изображение (RGBA) и маску переднего плана (fg_mask).
    Если на холсте обнаружено >= 3 отдельных полноценных стикеров:
    возвращает (True, [список вырезанных одиночных RGBA-стикеров]).
    Иначе возвращает (False, []).
    """
    rgba = np.array(cleaned_rgba.convert("RGBA"), dtype=np.uint8)
    h, w = fg_mask.shape[:2]
    total_area = float(h * w)

    # Адаптивный минимальный размер одного стикера на листе (~6% от меньшей стороны холста)
    dyn_min_px = max(48, min(min_item_px, int(round(min(h, w) * 0.065))))

    # Заполняем внутренние дырки каждого стикера без раздувания внешних границ,
    # чтобы соседние наклейки на плотном листе не слипались друг с другом
    bin_fg = (fg_mask > 45).astype(np.uint8) * 255
    k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    bin_fg = cv2.morphologyEx(bin_fg, cv2.MORPH_CLOSE, k_small, iterations=1)

    contours, _ = cv2.findContours(bin_fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled_islands = np.zeros_like(bin_fg)
    for cnt in contours:
        if cv2.contourArea(cnt) >= (dyn_min_px * dyn_min_px * 0.22):
            cv2.drawContours(filled_islands, [cnt], -1, 255, thickness=cv2.FILLED)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        filled_islands, connectivity=8
    )

    valid_components: List[Tuple[int, int, int, int, int, float]] = []
    total_fg_pixels = float(np.count_nonzero(filled_islands))
    if total_fg_pixels < 1000:
        return False, []

    for lbl in range(1, num_labels):
        x = int(stats[lbl, cv2.CC_STAT_LEFT])
        y = int(stats[lbl, cv2.CC_STAT_TOP])
        cw = int(stats[lbl, cv2.CC_STAT_WIDTH])
        ch = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        area = float(stats[lbl, cv2.CC_STAT_AREA])

        if cw < dyn_min_px or ch < dyn_min_px:
            continue
        if area < total_area * 0.0045:
            continue
        aspect = float(max(cw, ch)) / float(max(1, min(cw, ch)))
        if aspect > 4.5:
            continue

        valid_components.append((lbl, x, y, cw, ch, area))

    if len(valid_components) < 3:
        return False, []

    # Если один гигантский компонент занимает > 78% всей площади переднего плана,
    # значит это одиночный стикер с парой мелких деталей, а не лист со стикерпаком.
    max_comp_area = max(c[5] for c in valid_components)
    if max_comp_area / total_fg_pixels > 0.78:
        return False, []

    # Сортируем стикеры сверху вниз, слева направо (по строкам)
    row_tol = max(35, h // 9)
    valid_components.sort(key=lambda c: (c[2] // row_tol, c[1]))

    extracted_stickers: List[Image.Image] = []
    for lbl, x, y, cw, ch, _ in valid_components[:max_items]:
        comp_mask = (labels[y : y + ch, x : x + cw] == lbl).astype(np.uint8)
        sub_rgba = rgba[y : y + ch, x : x + cw].copy()
        sub_fg = fg_mask[y : y + ch, x : x + cw].copy()
        sub_alpha = np.where(comp_mask > 0, sub_fg, 0).astype(np.uint8)

        ys, xs = np.nonzero(sub_alpha > 20)
        if len(xs) < 30 or len(ys) < 30:
            continue
        x0, x1 = int(np.min(xs)), int(np.max(xs)) + 1
        y0, y1 = int(np.min(ys)), int(np.max(ys)) + 1
        real_w, real_h = x1 - x0, y1 - y0
        if max(real_w, real_h) < dyn_min_px:
            continue

        crop_rgb = sub_rgba[y0:y1, x0:x1, :3]
        crop_a = sub_alpha[y0:y1, x0:x1]
        crop_rgba = np.dstack([crop_rgb, crop_a])

        # Если вырезанный из листа стикер меньше 550px по большей стороне,
        # плавно увеличиваем его (Lanczos), чтобы векторный контур реза был идеально гладким
        longest_side = max(real_w, real_h)
        if longest_side < 550:
            scale = 600.0 / float(longest_side)
            nw = int(round(real_w * scale))
            nh = int(round(real_h * scale))
            pil_sub = Image.fromarray(crop_rgba, mode="RGBA").resize(
                (nw, nh), Image.Resampling.LANCZOS
            )
            crop_rgba = np.array(pil_sub, dtype=np.uint8)
            real_h, real_w = crop_rgba.shape[:2]

        pad = 20
        padded = np.zeros((real_h + pad * 2, real_w + pad * 2, 4), dtype=np.uint8)
        padded[pad : pad + real_h, pad : pad + real_w] = crop_rgba
        extracted_stickers.append(Image.fromarray(padded, mode="RGBA"))

    if len(extracted_stickers) < 3:
        return False, []

    return True, extracted_stickers


def assemble_sticker_pack_sheet(
    single_png_paths: List[Path],
    output_sheet_png: Path,
    cols: int = 3,
    cell_px: int = 620,
    gap_px: int = 90,
    margin_px: int = 90,
) -> bool:
    """
    Собирает от 4 до 12 одиночных PNG-стикеров с прозрачным фоном в единый красивый
    лист-стикерпак высокого разрешения, чтобы jpeg2eps построил вокруг каждого стикера
    контур CutContour и общую рамку листа PerfCutContour.
    """
    valid_imgs: List[Image.Image] = []
    for p in single_png_paths:
        if not p.exists():
            continue
        try:
            im = Image.open(p).convert("RGBA")
            valid_imgs.append(im)
        except Exception:
            continue

    if len(valid_imgs) < 4:
        return False

    n = len(valid_imgs)
    cols = max(2, min(cols, int(math.ceil(math.sqrt(n)))))
    rows = int(math.ceil(n / float(cols)))

    sheet_w = margin_px * 2 + cols * cell_px + max(0, cols - 1) * gap_px
    sheet_h = margin_px * 2 + rows * cell_px + max(0, rows - 1) * gap_px

    sheet = Image.new("RGBA", (sheet_w, sheet_h), (255, 255, 255, 0))

    for idx, im in enumerate(valid_imgs):
        r = idx // cols
        c = idx % cols

        arr = np.array(im, dtype=np.uint8)
        ys, xs = np.nonzero(arr[:, :, 3] > 20)
        if len(xs) > 0 and len(ys) > 0:
            im = im.crop((int(np.min(xs)), int(np.min(ys)), int(np.max(xs)) + 1, int(np.max(ys)) + 1))

        iw, ih = im.size
        scale = min(float(cell_px) / float(max(1, iw)), float(cell_px) / float(max(1, ih)))
        nw = max(32, int(round(iw * scale)))
        nh = max(32, int(round(ih * scale)))
        resized = im.resize((nw, nh), Image.Resampling.LANCZOS)

        x_cell = margin_px + c * (cell_px + gap_px) + (cell_px - nw) // 2
        y_cell = margin_px + r * (cell_px + gap_px) + (cell_px - nh) // 2
        sheet.paste(resized, (x_cell, y_cell), resized)

    output_sheet_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_sheet_png, format="PNG", optimize=True)
    return True
