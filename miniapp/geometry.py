"""Геометрия наклейки для мини-приложения без растровой обработки.

Повторяет арифметику convert_image_to_plotter_eps (jpeg2eps.py): размер рисунка по длинной стороне,
растяжение по осям, отступ канта и поля холста. Нужна, чтобы считать размеры и раскладку
мгновенно, не запуская конвертацию. Тот же расчёт портирован в miniapp/static/geometry.js,
совпадение проверяется векторами tests/golden/miniapp_layout.json.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict

MIN_SIZE_MM = 5.0
MAX_SIZE_MM = 1370.0
MIN_STRETCH = 0.1
MAX_STRETCH = 10.0
MAX_OFFSET_MM = 10.0
MAX_NESTING_QTY = 500
PERF_MODES = ("none", "sheet_frame", "die_cut")
ROLL_WIDTHS_MM = (600.0, 1000.0, 1370.0)

# Поле холста под рамку/вырубку PerfCut, как в jpeg2eps.py
EXTRA_PERF_MM = 3.5
# Буфер растра при неравном растяжении, как в jpeg2eps.py
STRETCH_RASTER_CAP_PX = 4500.0


@dataclass
class JobParams:
    """Параметры макета, которые настраиваются в мини-приложении."""

    size_mm: float = 100.0
    stretch_x: float = 1.0
    stretch_y: float = 1.0
    offset_mm: float = 2.5
    keep_background: bool = False
    perf_cut_mode: str = "none"
    perf_cut_passes: int = 2
    nesting_qty: int = 1
    fit_a4_sheet: bool = False
    roll_width_mm: float = 1370.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobParams":
        """Собирает параметры из словаря (meta.json, prefs, тело запроса) и зажимает их в допустимые рамки."""
        d = cls()

        def _f(key: str, default: float) -> float:
            val = data.get(key)
            try:
                return float(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int) -> int:
            val = data.get(key)
            try:
                return int(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        perf = str(data.get("perf_cut_mode") or d.perf_cut_mode)
        if perf not in PERF_MODES:
            perf = "none"
        return cls(
            size_mm=_clamp(_f("size_mm", d.size_mm), MIN_SIZE_MM, MAX_SIZE_MM),
            stretch_x=_clamp(_f("stretch_x", d.stretch_x), MIN_STRETCH, MAX_STRETCH),
            stretch_y=_clamp(_f("stretch_y", d.stretch_y), MIN_STRETCH, MAX_STRETCH),
            offset_mm=_clamp(_f("offset_mm", d.offset_mm), 0.0, MAX_OFFSET_MM),
            keep_background=bool(data.get("keep_background") or False),
            perf_cut_mode=perf,
            perf_cut_passes=int(_clamp(_i("perf_cut_passes", d.perf_cut_passes), 1, 5)),
            nesting_qty=int(_clamp(_i("nesting_qty", d.nesting_qty), 1, MAX_NESTING_QTY)),
            fit_a4_sheet=bool(data.get("fit_a4_sheet") or False),
            roll_width_mm=_clamp(_f("roll_width_mm", d.roll_width_mm), 300.0, 1600.0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def item_geometry(crop_w_px: int, crop_h_px: int, params: JobParams) -> Dict[str, float]:
    """Размеры одной наклейки в мм по рамке силуэта исходника (crop_w_px × crop_h_px).

    Возвращает:
      art_w_mm / art_h_mm   — рисунок без канта;
      cut_w_mm / cut_h_mm   — по контуру реза (рисунок + кант с двух сторон);
      cell_w_mm / cell_h_mm — ячейка холста EPS с полями, по ней считается раскладка (pricing.py);
      pad_mm                — поле холста с каждой стороны.
    """
    ow, oh = max(1, int(crop_w_px)), max(1, int(crop_h_px))
    sx = max(0.05, float(params.stretch_x))
    sy = max(0.05, float(params.stretch_y))
    size = max(1.0, float(params.size_mm))

    base_longest = float(max(ow, oh))
    base_w_mm = ow / base_longest * size
    base_h_mm = oh / base_longest * size
    target_w_mm = base_w_mm * sx
    target_h_mm = base_h_mm * sy

    obj_w, obj_h = ow, oh
    if abs(sx - sy) > 0.005 * max(sx, sy):
        min_s = min(sx, sy)
        rx, ry = sx / min_s, sy / min_s
        max_after = max(ow * rx, oh * ry)
        if max_after > STRETCH_RASTER_CAP_PX:
            cap = STRETCH_RASTER_CAP_PX / max_after
            rx *= cap
            ry *= cap
        obj_w = max(32, int(round(ow * rx)))
        obj_h = max(32, int(round(oh * ry)))

    longest_obj_px = float(max(obj_w, obj_h))
    px_per_mm = obj_w / max(1.0, target_w_mm)
    offset_px = max(0.0, float(params.offset_mm) * px_per_mm)
    extra_px = (EXTRA_PERF_MM if params.perf_cut_mode in ("sheet_frame", "die_cut") else 0.0) * px_per_mm
    pad = int(math.ceil(offset_px + extra_px + max(10.0, longest_obj_px * 0.025)))

    art_w_mm = obj_w / px_per_mm
    art_h_mm = obj_h / px_per_mm
    return {
        "art_w_mm": round(art_w_mm, 2),
        "art_h_mm": round(art_h_mm, 2),
        "target_h_mm": round(target_h_mm, 2),
        "cut_w_mm": round(art_w_mm + 2 * params.offset_mm, 2),
        "cut_h_mm": round(art_h_mm + 2 * params.offset_mm, 2),
        "cell_w_mm": round((obj_w + 2 * pad) / px_per_mm, 3),
        "cell_h_mm": round((obj_h + 2 * pad) / px_per_mm, 3),
        "pad_mm": round(pad / px_per_mm, 3),
    }
