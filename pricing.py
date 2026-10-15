"""Модуль расчета раскладки и стоимости (nesting & pricing) для плоттера Roland VP-540.

Цены материалов и чернил (ООО «ЗЕНОН-СПБ») и коэффициенты розницы загружаются из pricing.toml.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict
import tomllib

PRICING_CONFIG_PATH = Path(__file__).resolve().parent / "pricing.toml"

_DEFAULT_CONFIG: Dict[str, Any] = {
    "roll": {
        "default_width_mm": 1370.0,
        "margin_mm": 10.0,
        "default_gap_mm": 3.0,
    },
    "a4": {
        "width_mm": 210.0,
        "height_mm": 297.0,
        "usable_width_mm": 200.0,
        "usable_height_mm": 287.0,
        "area_m2": 0.06237,
    },
    "costs": {
        "zenon": {
            "material_rub_per_m2": 75.18,
            "ink_rub_per_m2": 54.82,
            "total_rub_per_m2": 130.0,
            "min_cost_rub": 5,
            "min_a4_sheet_cost_rub": 8,
        }
    },
    "retail": {
        "min_base_unit_rub": 15.0,
        "cm2_rate": 0.38,
        "min_retail_rub": 100,
        "min_a4_retail_rub": 250,
        "a4_discount": 0.85,
        "discounts": {
            "qty_10": 0.90,
            "qty_25": 0.80,
            "qty_50": 0.70,
            "qty_100": 0.60,
        },
    },
}


def load_pricing_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Загружает конфигурацию цен из TOML-файла или возвращает значения по умолчанию."""
    target_path = Path(path) if path else PRICING_CONFIG_PATH
    if target_path.is_file():
        try:
            with open(target_path, "rb") as f:
                data = tomllib.load(f)
                return data
        except Exception:
            pass
    return _DEFAULT_CONFIG


def calculate_nesting_and_price(
    item_w_mm: float,
    item_h_mm: float,
    qty: int = 1,
    roll_width_mm: float = 1370.0,
    gap_mm: float = 3.0,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Рассчитывает оптимальную раскладку тиража на рулоне плоттера Roland VP-540,

    вместимость на лист А4 (210×297 мм), расход материала и чернил по ценам ЗЕНОН-СПБ.
    """
    cfg = config or load_pricing_config()
    roll_cfg = cfg.get("roll", _DEFAULT_CONFIG["roll"])
    a4_cfg = cfg.get("a4", _DEFAULT_CONFIG["a4"])
    costs_zenon = cfg.get("costs", {}).get("zenon", _DEFAULT_CONFIG["costs"]["zenon"])
    retail_cfg = cfg.get("retail", _DEFAULT_CONFIG["retail"])
    discounts = retail_cfg.get("discounts", _DEFAULT_CONFIG["retail"]["discounts"])

    margin_mm = float(roll_cfg.get("margin_mm", 10.0))
    qty = max(1, int(qty))
    usable_roll_mm = max(50.0, float(roll_width_mm) - margin_mm)

    # Вариант A: без поворота
    cols_a = max(1, int((usable_roll_mm + gap_mm) // (item_w_mm + gap_mm)))
    rows_a = int(math.ceil(qty / float(cols_a)))
    len_a = rows_a * item_h_mm + max(0, rows_a - 1) * gap_mm + margin_mm

    # Вариант B: с поворотом на 90 градусов (если помещается по ширине)
    if item_h_mm <= usable_roll_mm:
        cols_b = max(1, int((usable_roll_mm + gap_mm) // (item_h_mm + gap_mm)))
        rows_b = int(math.ceil(qty / float(cols_b)))
        len_b = rows_b * item_w_mm + max(0, rows_b - 1) * gap_mm + margin_mm
    else:
        cols_b, rows_b, len_b = cols_a, rows_a, float("inf")

    if len_b < len_a:
        rotated = True
        cols = cols_b
        rows = rows_b
        cell_w_mm, _cell_h_mm = item_h_mm, item_w_mm
        used_len_mm = len_b
    else:
        rotated = False
        cols = cols_a
        rows = rows_a
        cell_w_mm, _cell_h_mm = item_w_mm, item_h_mm
        used_len_mm = len_a

    actual_cols_first_row = min(qty, cols)
    used_width_mm = (
        actual_cols_first_row * cell_w_mm
        + max(0, actual_cols_first_row - 1) * gap_mm
        + margin_mm
    )

    # Расчёт вместимости на стандартный лист А4
    a4_usable_w = float(a4_cfg.get("usable_width_mm", 200.0))
    a4_usable_h = float(a4_cfg.get("usable_height_mm", 287.0))
    a4_cols_1 = max(0, int((a4_usable_w + gap_mm) // (item_w_mm + gap_mm)))
    a4_rows_1 = max(0, int((a4_usable_h + gap_mm) // (item_h_mm + gap_mm)))
    a4_fit_1 = a4_cols_1 * a4_rows_1

    a4_cols_2 = max(0, int((a4_usable_w + gap_mm) // (item_h_mm + gap_mm)))
    a4_rows_2 = max(0, int((a4_usable_h + gap_mm) // (item_w_mm + gap_mm)))
    a4_fit_2 = a4_cols_2 * a4_rows_2

    if a4_fit_2 > a4_fit_1:
        a4_fit_qty = max(1, a4_fit_2)
        a4_cols, a4_rows, a4_rotated = max(1, a4_cols_2), max(1, a4_rows_2), True
    else:
        a4_fit_qty = max(1, a4_fit_1)
        a4_cols, a4_rows, a4_rotated = max(1, a4_cols_1), max(1, a4_rows_1), False

    # Площадь по занятому прямоугольнику и по всей ширине рулона
    net_area_m2 = (used_width_mm / 1000.0) * (used_len_mm / 1000.0)
    roll_area_m2 = (roll_width_mm / 1000.0) * (used_len_mm / 1000.0)
    eff_area_m2 = net_area_m2 if qty < cols else roll_area_m2

    # Себестоимость ЗЕНОН-СПБ
    mat_rate = float(costs_zenon.get("material_rub_per_m2", 75.18))
    ink_rate = float(costs_zenon.get("ink_rub_per_m2", 54.82))
    material_cost_rub = round(eff_area_m2 * mat_rate, 1)
    ink_cost_rub = round(eff_area_m2 * ink_rate, 1)
    raw_cost_rub = material_cost_rub + ink_cost_rub
    min_cost = int(costs_zenon.get("min_cost_rub", 5))
    cost_rub = max(min_cost, int(round(raw_cost_rub)))

    # Себестоимость 1 листа А4
    a4_area = float(a4_cfg.get("area_m2", 0.06237))
    total_rate = float(costs_zenon.get("total_rub_per_m2", 130.0))
    min_a4_sheet_cost = int(costs_zenon.get("min_a4_sheet_cost_rub", 8))
    a4_sheet_cost_rub = max(min_a4_sheet_cost, int(round(a4_area * total_rate)))

    # Рекомендуемая розничная цена
    single_area_cm2 = (item_w_mm * item_h_mm) / 100.0
    cm2_rate = float(retail_cfg.get("cm2_rate", 0.38))
    min_base_unit = float(retail_cfg.get("min_base_unit_rub", 15.0))
    base_unit_rub = max(min_base_unit, single_area_cm2 * cm2_rate)

    if qty >= 100:
        base_unit_rub *= float(discounts.get("qty_100", 0.60))
    elif qty >= 50:
        base_unit_rub *= float(discounts.get("qty_50", 0.70))
    elif qty >= 25:
        base_unit_rub *= float(discounts.get("qty_25", 0.80))
    elif qty >= 10:
        base_unit_rub *= float(discounts.get("qty_10", 0.90))

    min_retail = int(retail_cfg.get("min_retail_rub", 100))
    retail_rub = max(min_retail, int(round((base_unit_rub * qty) / 10.0) * 10))
    unit_rub = round(retail_rub / float(qty), 1)

    a4_disc = float(retail_cfg.get("a4_discount", 0.85))
    min_a4_retail = int(retail_cfg.get("min_a4_retail_rub", 250))
    a4_retail_rub = max(min_a4_retail, int(round((base_unit_rub * a4_fit_qty * a4_disc) / 10.0) * 10))

    return {
        "item_w_mm": round(item_w_mm, 1),
        "item_h_mm": round(item_h_mm, 1),
        "qty": qty,
        "roll_width_mm": round(roll_width_mm, 0),
        "cols": cols,
        "rows": rows,
        "rotated": rotated,
        "used_width_mm": round(used_width_mm, 1),
        "used_length_mm": round(used_len_mm, 1),
        "used_length_m": round(used_len_mm / 1000.0, 2),
        "area_m2": round(eff_area_m2, 3),
        "material_cost_rub": material_cost_rub,
        "ink_cost_rub": ink_cost_rub,
        "cost_rub": cost_rub,
        "retail_rub": retail_rub,
        "unit_rub": unit_rub,
        "a4_fit_qty": a4_fit_qty,
        "a4_cols": a4_cols,
        "a4_rows": a4_rows,
        "a4_rotated": a4_rotated,
        "a4_cost_rub": a4_sheet_cost_rub,
        "a4_retail_rub": a4_retail_rub,
    }
