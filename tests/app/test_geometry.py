"""
Тесты геометрических функций на основе общих эталонных векторов tests/golden/geometry.json.
Строгое соблюдение контракта: погрешность не более 0.01 мм.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from sticker_app.geometry import (
    get_rotated_bbox,
    get_sticker_polygon,
    is_sticker_inside_object,
    mm_to_px,
    px_to_mm,
    rotate_point,
)

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "golden" / "geometry.json"


@pytest.fixture(scope="module")
def golden_data():
    assert GOLDEN_PATH.is_file(), f"Файл {GOLDEN_PATH} не найден"
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_file_structure(golden_data):
    assert golden_data["version"] == "1.0"
    assert golden_data["tolerance_mm"] <= 0.01
    assert len(golden_data["conversions"]) >= 30
    assert len(golden_data["rotations"]) >= 10
    assert len(golden_data["cases"]) >= 30


def test_unit_conversions(golden_data):
    tol = golden_data["tolerance_mm"]
    for item in golden_data["conversions"]:
        val_mm = item["val_mm"]
        scale = item["scale"]
        expected_px = item["expected_px"]
        expected_mm = item["expected_mm"]

        actual_px = mm_to_px(val_mm, scale)
        assert actual_px == pytest.approx(expected_px, abs=tol)

        if scale > 0:
            actual_mm = px_to_mm(actual_px, scale)
            assert actual_mm == pytest.approx(expected_mm, abs=tol)


def test_px_to_mm_zero_scale():
    with pytest.raises(ValueError, match="не может быть равен 0"):
        px_to_mm(100.0, 0.0)


def test_point_rotations(golden_data):
    tol = golden_data["tolerance_mm"]
    for item in golden_data["rotations"]:
        rx, ry = rotate_point(
            item["x"], item["y"], item["cx"], item["cy"], item["angle_deg"]
        )
        assert rx == pytest.approx(item["expected_rx"], abs=tol)
        assert ry == pytest.approx(item["expected_ry"], abs=tol)


def test_complex_cases(golden_data):
    tol = golden_data["tolerance_mm"]
    for case in golden_data["cases"]:
        sticker = case["sticker"]
        obj = case["object"]
        expected = case["expected"]
        scale = case["scale"]

        cx, cy = sticker["cx_mm"], sticker["cy_mm"]
        w, h = sticker["w_mm"], sticker["h_mm"]
        angle = sticker["angle_deg"]

        # 1. Проверка полигона стикера
        poly = get_sticker_polygon(cx, cy, w, h, angle)
        assert len(poly) == 4
        for actual_pt, exp_pt in zip(poly, expected["polygon_mm"]):
            assert actual_pt[0] == pytest.approx(exp_pt[0], abs=tol)
            assert actual_pt[1] == pytest.approx(exp_pt[1], abs=tol)

        # 2. Проверка Bounding Box
        bbox = get_rotated_bbox(cx, cy, w, h, angle)
        for actual_b, exp_b in zip(bbox, expected["bbox_mm"]):
            assert actual_b == pytest.approx(exp_b, abs=tol)

        # 3. Проверка пересчета координат центра в пиксели
        cx_px = mm_to_px(cx, scale)
        cy_px = mm_to_px(cy, scale)
        assert cx_px == pytest.approx(expected["center_px"][0], abs=tol)
        assert cy_px == pytest.approx(expected["center_px"][1], abs=tol)

        # 4. Проверка попадания внутрь маски объекта
        inside = is_sticker_inside_object(poly, obj, safe_margin_mm=0.0)
        assert inside == expected["is_inside"], f"Failed is_inside for case {case['id']}: {case['description']}"

        # 5. Проверка попадания внутрь безопасной зоны объекта
        inside_safe = is_sticker_inside_object(poly, obj, safe_margin_mm=obj["safe_margin_mm"])
        assert inside_safe == expected["is_inside_safe"], f"Failed is_inside_safe for case {case['id']}: {case['description']}"


def test_point_formats_in_polygon():
    obj = {"width_mm": 300.0, "height_mm": 200.0, "corner_radius_mm": 8.0, "safe_margin_mm": 4.0}
    # Поддержка словарей {'x': ..., 'y': ...}
    poly_dict = [
        {"x": 100.0, "y": 100.0},
        {"x": 120.0, "y": 100.0},
        {"x": 120.0, "y": 120.0},
        {"x": 100.0, "y": 120.0},
    ]
    assert is_sticker_inside_object(poly_dict, obj) is True

    # Поддержка списков [x, y]
    poly_list = [
        [100.0, 100.0],
        [120.0, 100.0],
        [120.0, 120.0],
        [100.0, 120.0],
    ]
    assert is_sticker_inside_object(poly_list, obj) is True
