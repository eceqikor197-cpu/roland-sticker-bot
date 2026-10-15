#!/usr/bin/env python3
"""
Строгий фильтр полиграфического качества (Quality Gate) БЕЗ искажения исходников:
1. Никакого разрезания стикерпаков на мелкие шакальные куски!
2. Никакого размытия краёв дешёвыми 320x320 масками — в конвертер jpeg2eps подаётся 100% оригинальный файл.
3. Пропускаются ТОЛЬКО макеты с идеально чистым белым или прозрачным фоном (никаких столов, рук, стёкол машин и мокапов).
4. Автоматическое разделение по геометрии силуэтов:
   - 'pack_sheet' (Лист-стикерпак: от 4 до 22 отдельных чётких стикеров на одном листе, разрешение >= 900px)
   - 'single_sticker' (Одиночный стикер / авто-наклейка: РОВНО 1 цельный крупный силуэт на листе без мусора и без оторванных букв).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

from harvester.config import CategoryPrintProfile
from harvester.database import is_phash_duplicate
from jpeg2eps import (
    extract_foreground_mask,
    fill_external_silhouette,
    load_original_image,
)

BAD_META_KEYWORDS = (
    "t-shirt", "tshirt", "hoodie", "sweatshirt", "mug mockup", "pillow",
    "phone case", "iphone case", "totebag", "tote bag", "apron", "tapestry",
    "coloring page", "раскраска", "футболка", "толстовка", "худи", "кружка",
    "чехол для", "подушка", "shutterstock", "gettyimages", "alamy", "123rf",
    "depositphoto", "dreamstime", "istock", "верх торта", "картинка на торт",
    "печать на торт", "вафельная картинка", "сахарная картинка", "банка распечатка",
    "дизайн сторис",
)


@dataclass
class QualityGateResult:
    passed: bool
    reason: str
    detected_mode: str = ""  # 'pack_sheet', 'single_sticker', 'banner'
    expected_contours: int = 1
    phash_hex: str = ""
    prepared_input_path: Optional[Path] = None
    width_px: int = 0
    height_px: int = 0


def _has_clean_white_or_transparent_border(
    rgb: np.ndarray,
    alpha: Optional[np.ndarray],
) -> Tuple[bool, bool, str]:
    """
    Проверяет, что внешний край изображения — либо 100% прозрачный альфа-канал,
    либо чистый белый студийный лист (без текстуры дерева, асфальта, серых теней мокапов и рамок).
    Возвращает (is_clean, is_transparent, reason).
    """
    h, w = rgb.shape[:2]
    bw = max(3, min(12, min(h, w) // 45))

    if alpha is not None:
        border_a = np.concatenate(
            [
                alpha[:bw, :].reshape(-1),
                alpha[-bw:, :].reshape(-1),
                alpha[:, :bw].reshape(-1),
                alpha[:, -bw:].reshape(-1),
            ]
        )
        trans_ratio = float(np.mean(border_a < 25))
        if trans_ratio >= 0.80:
            return True, True, "transparent_border"

    border_rgb = np.concatenate(
        [
            rgb[:bw, :, :].reshape(-1, 3),
            rgb[-bw:, :, :].reshape(-1, 3),
            rgb[:, :bw, :].reshape(-1, 3),
            rgb[:, -bw:, :].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.int32)

    # Пиксель считается чисто белым/студийным светлым, если все каналы >= 218 и разброс R,G,B <= 18
    min_ch = np.min(border_rgb, axis=1)
    max_ch = np.max(border_rgb, axis=1)
    chroma = max_ch - min_ch
    is_white_px = (min_ch >= 218) & (chroma <= 18)
    white_ratio = float(np.mean(is_white_px))

    if white_ratio >= 0.68:
        return True, False, f"clean_white_border({white_ratio:.2f})"

    return False, False, f"non_white_mockup_border(white_ratio={white_ratio:.2f})"


def _check_sharpness(rgb: np.ndarray) -> float:
    """Вычисляет резкость изображения (дисперсия Лапласиана) для отсева мыльных превьюшек."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def evaluate_image_quality(
    image_path: Path,
    profile: CategoryPrintProfile,
    title_or_desc: str = "",
    allow_smaller_telegram_sticker: bool = False,
) -> QualityGateResult:
    """
    Проверяет скачанный файл и определяет его точный тип:
    - 'pack_sheet': цельный лист-стикерпак (от 4 до 22 чётких стикеров на одном листе)
    - 'single_sticker': одиночный стикер / авто-наклейка (ровно 1 цельный силуэт без мусора)
    - 'banner': прямоугольный постер / баннер (для категории 04_Баннеры_и_Вывески)
    """
    meta_low = (title_or_desc or "").lower()
    for bad_kw in BAD_META_KEYWORDS:
        if bad_kw in meta_low:
            return QualityGateResult(passed=False, reason=f"bad_meta:{bad_kw}")

    try:
        pil_img = Image.open(image_path)
        pil_img = ImageOps.exif_transpose(pil_img)
    except Exception as exc:
        return QualityGateResult(passed=False, reason=f"corrupt_image:{exc}")

    w, h = pil_img.size
    longest = max(w, h)
    shortest = min(w, h)

    aspect = float(longest) / float(max(1, shortest))
    if aspect > 3.2:
        return QualityGateResult(
            passed=False,
            reason=f"bad_aspect_ratio:{aspect:.2f}",
            width_px=w,
            height_px=h,
        )

    # Вычисляем pHash и проверяем на дубликаты
    try:
        phash_hex = str(imagehash.phash(pil_img.convert("RGB")))
    except Exception as exc:
        return QualityGateResult(passed=False, reason=f"phash_err:{exc}")

    is_dup, matched = is_phash_duplicate(phash_hex)
    if is_dup:
        return QualityGateResult(
            passed=False,
            reason=f"duplicate_phash:{matched}",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    # Для стикеров 512x512 (Telegram / ВК / Pinterest стикеры) плавно увеличиваем в 2 раза (1024px),
    # чтобы контур реза строился в высоком разрешении
    if (allow_smaller_telegram_sticker or (500 <= longest < 600 and shortest >= 380)) and 450 <= longest < 900:
        rgba_tg = pil_img.convert("RGBA")
        scale = 2.0
        nw, nh = int(round(w * scale)), int(round(h * scale))
        upscaled = rgba_tg.resize((nw, nh), Image.Resampling.LANCZOS)
        arr_a = np.array(upscaled)[:, :, 3]
        is_trans_edge = float(np.mean(np.concatenate([arr_a[0, :], arr_a[-1, :], arr_a[:, 0], arr_a[:, -1]]) < 30)) >= 0.75
        pad = 48
        bg_col = (255, 255, 255, 0) if is_trans_edge else (255, 255, 255, 255)
        canvas = Image.new("RGBA", (nw + pad * 2, nh + pad * 2), bg_col)
        canvas.paste(upscaled, (pad, pad), upscaled if is_trans_edge else None)
        png_up_path = image_path.with_suffix(".png")
        if not is_trans_edge:
            canvas = canvas.convert("RGB")
        canvas.save(png_up_path, format="PNG")
        image_path = png_up_path
        pil_img = canvas
        w, h = pil_img.size
        longest, shortest = max(w, h), min(w, h)

    if longest < profile.min_single_px or shortest < int(profile.min_single_px * 0.65):
        return QualityGateResult(
            passed=False,
            reason=f"low_res:{w}x{h}<{profile.min_single_px}",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    orig_rgb, alpha = load_original_image(image_path)

    # Проверка резкости (отсев размытых/шакальных апскейлов)
    sharpness = _check_sharpness(orig_rgb)
    if sharpness < 65.0:
        return QualityGateResult(
            passed=False,
            reason=f"blurry_image(sharpness={sharpness:.1f})",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    # Если это категория "04_Баннеры_и_Вывески"
    if profile.keep_background:
        if longest < profile.min_sheet_px:
            return QualityGateResult(
                passed=False,
                reason=f"banner_low_res:{w}x{h}",
                phash_hex=phash_hex,
                width_px=w,
                height_px=h,
            )
        return QualityGateResult(
            passed=True,
            reason="ok_banner",
            detected_mode="banner",
            expected_contours=1,
            phash_hex=phash_hex,
            prepared_input_path=image_path,
            width_px=w,
            height_px=h,
        )

    # 1. СТРОГАЯ ПРОВЕРКА ЧИСТОТЫ ФОНА (только чистый белый лист или прозрачный PNG!)
    is_clean_border, is_trans, border_reason = _has_clean_white_or_transparent_border(orig_rgb, alpha)
    if not is_clean_border:
        return QualityGateResult(
            passed=False,
            reason=border_reason,
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    # 2. АНАЛИЗ СИЛУЭТОВ ЧЕРЕЗ РОДНОЙ АЛГОРИТМ JPEG2EPS (1-в-1 как при резке!)
    fg_mask = extract_foreground_mask(orig_rgb, alpha, bg_tolerance=12.0, keep_background=False)
    silhouette = fill_external_silhouette(fg_mask)

    total_px = float(h * w)
    fg_px = float(np.count_nonzero(silhouette))
    fg_ratio = fg_px / total_px

    if fg_ratio < 0.10:
        return QualityGateResult(
            passed=False,
            reason=f"too_small_object({fg_ratio:.2f})",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )
    if fg_ratio > 0.90:
        return QualityGateResult(
            passed=False,
            reason=f"full_bleed_not_sticker({fg_ratio:.2f})",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    # Проверяем, не обрезан ли стикер краем картинки (силуэт не должен прилипать к рамке кадра)
    edge_touch_ratio = max(
        float(np.mean(silhouette[0, :] > 0)),
        float(np.mean(silhouette[-1, :] > 0)),
        float(np.mean(silhouette[:, 0] > 0)),
        float(np.mean(silhouette[:, -1] > 0)),
    )
    if edge_touch_ratio > 0.05:
        return QualityGateResult(
            passed=False,
            reason=f"cropped_at_frame_edge({edge_touch_ratio:.2f})",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    # 3. ОПРЕДЕЛЯЕМ ТИП МАКЕТА: ЦЕЛЬНЫЙ ЛИСТ-СТИКЕРПАК (3..24 стикера) ИЛИ ОДИНОЧНЫЙ СТИКЕР (1 стикер)
    # Сначала проверяем в масштабе печатного листа 250 мм (кант 1.6 мм = 0.64% стороны):
    offset_sheet_px = max(2, int(round(float(longest) * 0.0064)))
    k_sheet = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (offset_sheet_px * 2 + 1, offset_sheet_px * 2 + 1)
    )
    sheet_dilated = cv2.dilate(silhouette, k_sheet, iterations=1)
    sheet_filled = fill_external_silhouette(sheet_dilated)

    num_sheet_lbl, _, sheet_stats, _ = cv2.connectedComponentsWithStats(sheet_filled, connectivity=8)
    sheet_stickers = []
    sheet_fragments = 0
    total_sheet_fg = max(1.0, float(np.count_nonzero(sheet_filled)))
    for lbl in range(1, num_sheet_lbl):
        cw = int(sheet_stats[lbl, cv2.CC_STAT_WIDTH])
        ch = int(sheet_stats[lbl, cv2.CC_STAT_HEIGHT])
        area = float(sheet_stats[lbl, cv2.CC_STAT_AREA])
        if area >= total_px * 0.008 and cw >= 50 and ch >= 50:
            sheet_stickers.append((cw, ch, area))
        elif area >= 120:
            sheet_fragments += 1

    max_sheet_island_share = (
        max(s[2] for s in sheet_stickers) / total_sheet_fg if sheet_stickers else 1.0
    )

    # СЦЕНАРИЙ Б: ЦЕЛЬНЫЙ ЛИСТ-СТИКЕРПАК (от 3 до 24 сопоставимых стикеров на одном листе, где ни один не занимает >55% всей площади)
    if 3 <= len(sheet_stickers) <= 24 and max_sheet_island_share < 0.55:
        if longest < profile.min_sheet_px or shortest < 540:
            return QualityGateResult(
                passed=False,
                reason=f"sheet_res_too_low({w}x{h}<{profile.min_sheet_px})",
                phash_hex=phash_hex,
                width_px=w,
                height_px=h,
            )
        if sheet_fragments > 7:
            return QualityGateResult(
                passed=False,
                reason=f"dirty_sheet_islands(stickers={len(sheet_stickers)},fragments={sheet_fragments})",
                phash_hex=phash_hex,
                width_px=w,
                height_px=h,
            )
        return QualityGateResult(
            passed=True,
            reason=f"ok_pack_sheet({len(sheet_stickers)}_stickers)",
            detected_mode="pack_sheet",
            expected_contours=len(sheet_stickers),
            phash_hex=phash_hex,
            prepared_input_path=image_path,
            width_px=w,
            height_px=h,
        )

    # СЦЕНАРИЙ А: ОДИНОЧНЫЙ СТИКЕР / НАКЛЕЙКА (1 цельный объект на листе с кантом 2.6 мм на 100 мм)
    px_per_mm_single = float(longest) / 100.0
    offset_single_px = max(3, int(round(2.3 * px_per_mm_single)))
    k_single = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (offset_single_px * 2 + 1, offset_single_px * 2 + 1)
    )
    dilated_for_single = cv2.dilate(silhouette, k_single, iterations=1)
    dilated_filled = fill_external_silhouette(dilated_for_single)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(dilated_filled, connectivity=8)

    major_islands = []
    minor_fragments = 0
    for lbl in range(1, num_labels):
        cw = int(stats[lbl, cv2.CC_STAT_WIDTH])
        ch = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        area = float(stats[lbl, cv2.CC_STAT_AREA])
        if area >= total_px * 0.008 and cw >= 65 and ch >= 65:
            major_islands.append((cw, ch, area))
        elif area >= 80:
            minor_fragments += 1

    if minor_fragments > 2:
        return QualityGateResult(
            passed=False,
            reason=f"fragmented_noise_or_watermark(fragments={minor_fragments})",
            phash_hex=phash_hex,
            width_px=w,
            height_px=h,
        )

    num_major = len(major_islands)
    if num_major == 1 and max_sheet_island_share >= 0.55:
        contours, _ = cv2.findContours(dilated_filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return QualityGateResult(passed=False, reason="no_contour", phash_hex=phash_hex)
        main_cnt = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(main_cnt)
        hull_area = max(1.0, float(cv2.contourArea(hull)))
        solidity = float(cv2.contourArea(main_cnt)) / hull_area

        if solidity < 0.42:
            return QualityGateResult(
                passed=False,
                reason=f"spindly_or_broken_single_shape(solidity={solidity:.2f})",
                phash_hex=phash_hex,
                width_px=w,
                height_px=h,
            )

        return QualityGateResult(
            passed=True,
            reason="ok_single_sticker",
            detected_mode="single_sticker",
            expected_contours=1,
            phash_hex=phash_hex,
            prepared_input_path=image_path,
            width_px=w,
            height_px=h,
        )

    return QualityGateResult(
        passed=False,
        reason=f"ambiguous_island_count(sheet={len(sheet_stickers)},single={num_major})",
        phash_hex=phash_hex,
        width_px=w,
        height_px=h,
    )
