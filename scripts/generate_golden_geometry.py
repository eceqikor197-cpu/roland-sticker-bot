"""
Скрипт генерации эталонных тестовых векторов tests/golden/geometry.json.
Используется обоими тестовыми наборами: pytest (Python) и vitest (TypeScript).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from sticker_app.geometry import (
    get_rotated_bbox,
    get_sticker_polygon,
    is_sticker_inside_object,
    mm_to_px,
    px_to_mm,
    rotate_point,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
GOLDEN_FILE = ROOT_DIR / "tests" / "golden" / "geometry.json"


def round_float(val: float, decimals: int = 6) -> float:
    return round(float(val), decimals)


def build_golden_data() -> Dict[str, Any]:
    conversions: List[Dict[str, Any]] = []
    # Конверсии px <-> mm
    scales = [1.0, 2.54, 3.7795275591, 10.0, 0.5, 4.0]
    test_mm_values = [0.0, 10.0, 25.4, 50.0, 100.0, 304.1, 214.9]

    for scale in scales:
        for val_mm in test_mm_values:
            px = mm_to_px(val_mm, scale)
            back_mm = px_to_mm(px, scale)
            conversions.append(
                {
                    "val_mm": round_float(val_mm, 4),
                    "scale": round_float(scale, 6),
                    "expected_px": round_float(px, 6),
                    "expected_mm": round_float(back_mm, 4),
                }
            )

    # Вращения отдельных точек rotate_point
    rotations: List[Dict[str, Any]] = []
    rotation_specs = [
        (10.0, 20.0, 10.0, 10.0, 0.0),
        (10.0, 20.0, 10.0, 10.0, 90.0),
        (10.0, 20.0, 10.0, 10.0, 180.0),
        (10.0, 20.0, 10.0, 10.0, 270.0),
        (10.0, 20.0, 10.0, 10.0, 360.0),
        (10.0, 20.0, 10.0, 10.0, 37.0),
        (10.0, 20.0, 10.0, 10.0, -15.0),
        (10.0, 20.0, 10.0, 10.0, 359.0),
        (0.0, 0.0, 0.0, 0.0, 45.0),
        (50.0, 50.0, 0.0, 0.0, 90.0),
        (100.0, 150.0, 50.0, 50.0, -90.0),
        (15.0, 25.0, 30.0, 40.0, 720.0),
    ]

    for x, y, cx, cy, angle in rotation_specs:
        rx, ry = rotate_point(x, y, cx, cy, angle)
        rotations.append(
            {
                "x": round_float(x, 4),
                "y": round_float(y, 4),
                "cx": round_float(cx, 4),
                "cy": round_float(cy, 4),
                "angle_deg": round_float(angle, 4),
                "expected_rx": round_float(rx, 6),
                "expected_ry": round_float(ry, 6),
            }
        )

    # Комплексные кейсы стикеров на объектах (минимум 30 разнородных)
    objects = {
        "air_13": {"width_mm": 304.1, "height_mm": 214.9, "corner_radius_mm": 8.0, "safe_margin_mm": 4.0},
        "air_15": {"width_mm": 340.4, "height_mm": 237.6, "corner_radius_mm": 9.0, "safe_margin_mm": 4.0},
        "pro_14": {"width_mm": 312.6, "height_mm": 221.2, "corner_radius_mm": 8.0, "safe_margin_mm": 4.0},
        "pro_16": {"width_mm": 355.7, "height_mm": 248.1, "corner_radius_mm": 9.0, "safe_margin_mm": 4.0},
    }

    cases_raw = [
        # air_13: центр с разными углами
        ("air_13", 152.05, 107.45, 60.0, 40.0, 0.0, 3.7795275591, "Air 13: центр, 0 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, 90.0, 3.7795275591, "Air 13: центр, 90 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, 180.0, 3.7795275591, "Air 13: центр, 180 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, 270.0, 3.7795275591, "Air 13: центр, 270 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, 37.0, 3.7795275591, "Air 13: центр, 37 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, -15.0, 3.7795275591, "Air 13: центр, -15 градусов"),
        ("air_13", 152.05, 107.45, 60.0, 40.0, 359.0, 3.7795275591, "Air 13: центр, 359 градусов"),

        # Разные масштабы scale
        ("air_13", 100.0, 100.0, 50.0, 30.0, 15.0, 1.0, "Масштаб 1.0"),
        ("air_13", 100.0, 100.0, 50.0, 30.0, 15.0, 2.54, "Масштаб 2.54"),
        ("air_13", 100.0, 100.0, 50.0, 30.0, 15.0, 10.0, "Масштаб 10.0"),

        # Попадание внутрь скругленного угла (внутри безопасной зоны)
        ("air_13", 25.0, 25.0, 20.0, 20.0, 0.0, 3.7795275591, "Верхний левый квадрант, внутри"),
        ("air_13", 279.1, 25.0, 20.0, 20.0, 0.0, 3.7795275591, "Верхний правый квадрант, внутри"),
        ("air_13", 25.0, 189.9, 20.0, 20.0, 0.0, 3.7795275591, "Нижний левый квадрант, внутри"),
        ("air_13", 279.1, 189.9, 20.0, 20.0, 0.0, 3.7795275591, "Нижний правый квадрант, внутри"),

        # Выход в срезанный скруглением угол (внутри bbox, но вне окружности угла R=8)
        # Центр угла (8, 8). Точка (1, 1): dist^2 = (1-8)^2 + (1-8)^2 = 49 + 49 = 98 > 64.
        ("air_13", 2.0, 2.0, 2.0, 2.0, 0.0, 3.7795275591, "Стикер в самом углу (0,0) вне скругления"),
        ("air_13", 302.1, 2.0, 2.0, 2.0, 0.0, 3.7795275591, "Стикер в правом верхнем углу вне скругления"),
        ("air_13", 2.0, 212.9, 2.0, 2.0, 0.0, 3.7795275591, "Стикер в левом нижнем углу вне скругления"),
        ("air_13", 302.1, 212.9, 2.0, 2.0, 0.0, 3.7795275591, "Стикер в правом нижнем углу вне скругления"),

        # Выход за прямолинейные границы объекта
        ("air_13", 10.0, 107.45, 30.0, 30.0, 0.0, 3.7795275591, "Выход за левую границу (min_x = -5)"),
        ("air_13", 295.0, 107.45, 30.0, 30.0, 0.0, 3.7795275591, "Выход за правую границу (max_x = 310)"),
        ("air_13", 152.05, 10.0, 30.0, 30.0, 0.0, 3.7795275591, "Выход за верхнюю границу (min_y = -5)"),
        ("air_13", 152.05, 205.0, 30.0, 30.0, 0.0, 3.7795275591, "Выход за нижнюю границу (max_y = 220)"),

        # Касание границы (внутри маски, но нарушает safe_margin = 4.0)
        ("air_13", 16.0, 107.45, 30.0, 30.0, 0.0, 3.7795275591, "Касание границы: min_x = 1.0 (нарушает safe_margin)"),
        ("air_13", 288.1, 107.45, 30.0, 30.0, 0.0, 3.7795275591, "Касание границы: max_x = 303.1 (нарушает safe_margin)"),

        # Вращение стикера вблизи границы, вызывающее выход угла наружу
        ("air_13", 20.0, 107.45, 30.0, 30.0, 45.0, 3.7795275591, "Повернутый на 45 град стикер выходит за левый край"),
        ("air_13", 284.1, 107.45, 30.0, 30.0, 45.0, 3.7795275591, "Повернутый на 45 град стикер выходит за правый край"),

        # air_15: 340.4 x 237.6, R=9
        ("air_15", 170.2, 118.8, 80.0, 50.0, 0.0, 3.7795275591, "Air 15: центр, 0 градусов"),
        ("air_15", 170.2, 118.8, 80.0, 50.0, 37.0, 2.54, "Air 15: центр, 37 градусов, scale 2.54"),
        ("air_15", 335.0, 118.8, 30.0, 30.0, 0.0, 1.0, "Air 15: выход за правую границу"),

        # pro_14: 312.6 x 221.2, R=8
        ("pro_14", 156.3, 110.6, 70.0, 45.0, -15.0, 3.7795275591, "Pro 14: центр, -15 градусов"),
        ("pro_14", 156.3, 110.6, 70.0, 45.0, 359.0, 10.0, "Pro 14: центр, 359 градусов, scale 10.0"),
        ("pro_14", 156.3, 5.0, 20.0, 20.0, 0.0, 1.0, "Pro 14: выход за верхний край"),

        # pro_16: 355.7 x 248.1, R=9
        ("pro_16", 177.85, 124.05, 100.0, 60.0, 0.0, 3.7795275591, "Pro 16: центр, большой стикер"),
        ("pro_16", 177.85, 124.05, 100.0, 60.0, 90.0, 3.7795275591, "Pro 16: центр, поворот 90 градусов"),
        ("pro_16", 20.0, 20.0, 15.0, 15.0, 0.0, 3.7795275591, "Pro 16: малый стикер в углу"),
        ("pro_16", 2.0, 2.0, 15.0, 15.0, 0.0, 3.7795275591, "Pro 16: стикер в углу вне скругления"),
    ]

    cases: List[Dict[str, Any]] = []
    for idx, (obj_key, cx, cy, w, h, angle, scale, desc) in enumerate(cases_raw, start=1):
        obj = objects[obj_key]
        poly = get_sticker_polygon(cx, cy, w, h, angle)
        bbox = get_rotated_bbox(cx, cy, w, h, angle)
        inside = is_sticker_inside_object(poly, obj, safe_margin_mm=0.0)
        inside_safe = is_sticker_inside_object(poly, obj, safe_margin_mm=obj["safe_margin_mm"])
        cx_px = mm_to_px(cx, scale)
        cy_px = mm_to_px(cy, scale)

        cases.append(
            {
                "id": f"case_{idx:02d}_{obj_key}",
                "description": desc,
                "scale": round_float(scale, 6),
                "sticker": {
                    "cx_mm": round_float(cx, 4),
                    "cy_mm": round_float(cy, 4),
                    "w_mm": round_float(w, 4),
                    "h_mm": round_float(h, 4),
                    "angle_deg": round_float(angle, 4),
                },
                "object": {
                    "width_mm": round_float(obj["width_mm"], 4),
                    "height_mm": round_float(obj["height_mm"], 4),
                    "corner_radius_mm": round_float(obj["corner_radius_mm"], 4),
                    "safe_margin_mm": round_float(obj["safe_margin_mm"], 4),
                },
                "expected": {
                    "polygon_mm": [[round_float(p[0], 6), round_float(p[1], 6)] for p in poly],
                    "bbox_mm": [round_float(b, 6) for b in bbox],
                    "center_px": [round_float(cx_px, 6), round_float(cy_px, 6)],
                    "is_inside": inside,
                    "is_inside_safe": inside_safe,
                },
            }
        )

    return {
        "version": "1.0",
        "tolerance_mm": 0.01,
        "conversions": conversions,
        "rotations": rotations,
        "cases": cases,
    }


def main():
    data = build_golden_data()
    GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {GOLDEN_FILE} with {len(data['cases'])} complex cases, "
          f"{len(data['rotations'])} rotation cases, {len(data['conversions'])} conversion cases.")


if __name__ == "__main__":
    main()
