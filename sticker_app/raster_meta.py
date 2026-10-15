"""
Модуль извлечения геометрических характеристик и палитры из растровых изображений стикеров.
Вычисляет:
- aspect (отношение ширины к высоте)
- alpha_coverage (доля непрозрачных пикселей на миниатюре 256px)
- palette (до 5 доминирующих HEX-цветов без влияния прозрачного фона)
- is_square_ok (подходит ли макет под квадратный/круглый формат)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger("sticker_app.raster_meta")


@dataclass
class RasterMetadata:
    aspect: float = 1.0
    alpha_coverage: float = 0.0
    palette: List[str] = field(default_factory=list)
    is_square_ok: int = 1


def analyze_raster(
    png_path: Optional[Path] = None,
    width_px: int = 0,
    height_px: int = 0,
) -> RasterMetadata:
    """
    Анализирует растровое изображение макета.
    При отсутствии файла рассчитывает пропорции из width_px/height_px.
    """
    if height_px > 0 and width_px > 0:
        fallback_aspect = round(width_px / height_px, 4)
    else:
        fallback_aspect = 1.0
    is_square = 1 if 0.8 <= fallback_aspect <= 1.25 else 0

    if not png_path or not png_path.exists():
        return RasterMetadata(
            aspect=fallback_aspect,
            alpha_coverage=0.0,
            palette=[],
            is_square_ok=is_square,
        )

    try:
        with Image.open(png_path) as img:
            orig_w, orig_h = img.size
            aspect = round(orig_w / orig_h, 4) if orig_h > 0 else fallback_aspect
            is_square = 1 if 0.8 <= aspect <= 1.25 else 0

            # Миниатюра до 256px по длинной стороне согласно спецификации
            img_thumb = img.copy()
            img_thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)

            if img_thumb.mode != "RGBA":
                img_thumb = img_thumb.convert("RGBA")

            arr = np.array(img_thumb)
            alpha = arr[:, :, 3]
            # Непрозрачными считаем пиксели с alpha >= 32
            visible_mask = alpha >= 32
            total_pixels = alpha.size
            visible_count = int(np.count_nonzero(visible_mask))

            alpha_cov = (
                round(float(visible_count) / float(total_pixels), 4)
                if total_pixels > 0
                else 0.0
            )

            palette: List[str] = []
            if visible_count > 0:
                rgb_pixels = arr[visible_mask, :3]
                # Ограничиваем выборку для скорости
                if len(rgb_pixels) > 5000:
                    rng = np.random.default_rng(42)
                    indices = rng.choice(len(rgb_pixels), 5000, replace=False)
                    rgb_pixels = rgb_pixels[indices]

                dummy = Image.fromarray(rgb_pixels.reshape(1, -1, 3))
                quantized = dummy.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
                raw_pal = quantized.getpalette() or []
                color_counts = sorted(
                    quantized.getcolors() or [],
                    reverse=True,
                    key=lambda x: x[0],
                )

                for _cnt, idx in color_counts:
                    if idx * 3 + 2 < len(raw_pal):
                        r, g, b = raw_pal[idx * 3 : (idx + 1) * 3]
                        hex_val = f"#{r:02X}{g:02X}{b:02X}"
                        if hex_val not in palette:
                            palette.append(hex_val)
                    if len(palette) >= 5:
                        break

            return RasterMetadata(
                aspect=aspect,
                alpha_coverage=alpha_cov,
                palette=palette,
                is_square_ok=is_square,
            )

    except Exception as exc:
        logger.warning("Ошибка при анализе растра %s: %s", png_path, exc)
        return RasterMetadata(
            aspect=fallback_aspect,
            alpha_coverage=0.0,
            palette=[],
            is_square_ok=is_square,
        )
