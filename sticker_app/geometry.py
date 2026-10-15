"""
Геометрические преобразования и вычисления системы координат стикербомбинга.
Единственный источник истины — миллиметры (mm).
Пиксели (px) существуют только для экранной отрисовки через коэффициент scale.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple, Union

Point2D = Tuple[float, float]
Polygon2D = List[Point2D]
BBox2D = Tuple[float, float, float, float]  # min_x, min_y, max_x, max_y


def mm_to_px(val_mm: float, scale: float) -> float:
    """Переводит физический размер в миллиметрах в экранные пиксели."""
    return float(val_mm) * float(scale)


def px_to_mm(val_px: float, scale: float) -> float:
    """Переводит экранные пиксели в физические миллиметры."""
    if scale == 0:
        raise ValueError("Масштаб scale не может быть равен 0")
    return float(val_px) / float(scale)


def rotate_point(
    x: float,
    y: float,
    cx: float,
    cy: float,
    angle_deg: float,
) -> Point2D:
    """
    Поворачивает точку (x, y) вокруг центра (cx, cy) на угол angle_deg по часовой стрелке.
    В системе координат экрана ось X направлена вправо, ось Y направлена вниз.
    """
    # Нормализуем угол к диапазону [0, 360)
    norm_angle = angle_deg % 360.0
    if norm_angle < 0:
        norm_angle += 360.0

    if abs(norm_angle) < 1e-9:
        return float(x), float(y)
    elif abs(norm_angle - 90.0) < 1e-9:
        dx, dy = x - cx, y - cy
        return float(cx - dy), float(cy + dx)
    elif abs(norm_angle - 180.0) < 1e-9:
        dx, dy = x - cx, y - cy
        return float(cx - dx), float(cy - dy)
    elif abs(norm_angle - 270.0) < 1e-9:
        dx, dy = x - cx, y - cy
        return float(cx + dy), float(cy - dx)

    rad = math.radians(norm_angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    dx = x - cx
    dy = y - cy

    rx = cx + dx * cos_a - dy * sin_a
    ry = cy + dx * sin_a + dy * cos_a

    return float(rx), float(ry)


def get_sticker_polygon(
    cx_mm: float,
    cy_mm: float,
    w_mm: float,
    h_mm: float,
    angle_deg: float = 0.0,
) -> Polygon2D:
    """
    Вычисляет 4 вершины повернутого прямоугольника стикера (в мм).
    Порядок вершин: Top-Left, Top-Right, Bottom-Right, Bottom-Left.
    Поворот выполняется вокруг геометрического центра (cx_mm, cy_mm).
    """
    half_w = float(w_mm) / 2.0
    half_h = float(h_mm) / 2.0

    unrotated_corners = [
        (cx_mm - half_w, cy_mm - half_h),  # TL
        (cx_mm + half_w, cy_mm - half_h),  # TR
        (cx_mm + half_w, cy_mm + half_h),  # BR
        (cx_mm - half_w, cy_mm + half_h),  # BL
    ]

    if abs(angle_deg % 360.0) < 1e-9:
        return [(float(x), float(y)) for x, y in unrotated_corners]

    return [rotate_point(x, y, cx_mm, cy_mm, angle_deg) for x, y in unrotated_corners]


def get_rotated_bbox(
    cx_mm: float,
    cy_mm: float,
    w_mm: float,
    h_mm: float,
    angle_deg: float = 0.0,
) -> BBox2D:
    """
    Вычисляет габаритный ограничивающий прямоугольник (bounding box) повернутого стикера.
    Возвращает кортеж (min_x, min_y, max_x, max_y) в миллиметрах.
    """
    poly = get_sticker_polygon(cx_mm, cy_mm, w_mm, h_mm, angle_deg)
    xs = [pt[0] for pt in poly]
    ys = [pt[1] for pt in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _extract_object_geometry(
    object_mask: Union[Dict[str, Any], Any],
    safe_margin_mm: float | None = None,
) -> Tuple[float, float, float, float]:
    """
    Извлекает параметры геометрии (W, H, R, Margin) из словаря или Pydantic-объекта.
    """
    if isinstance(object_mask, dict):
        w = float(object_mask["width_mm"])
        h = float(object_mask["height_mm"])
        r = float(object_mask.get("corner_radius_mm", 0.0))
        default_margin = float(object_mask.get("safe_margin_mm", 0.0))
    else:
        w = float(getattr(object_mask, "width_mm"))
        h = float(getattr(object_mask, "height_mm"))
        r = float(getattr(object_mask, "corner_radius_mm", 0.0))
        default_margin = float(getattr(object_mask, "safe_margin_mm", 0.0))

    margin = default_margin if safe_margin_mm is None else float(safe_margin_mm)
    return w, h, r, margin


def is_point_inside_rounded_rect(
    x: float,
    y: float,
    w_mm: float,
    h_mm: float,
    corner_radius_mm: float,
    margin_mm: float = 0.0,
    eps: float = 1e-7,
) -> bool:
    """
    Проверяет аналитически, находится ли точка (x, y) внутри скруглённого прямоугольника
    с учетом отступа margin_mm.
    """
    x_min = margin_mm
    x_max = w_mm - margin_mm
    y_min = margin_mm
    y_max = h_mm - margin_mm
    r = max(0.0, corner_radius_mm - margin_mm)

    # Точка вне габаритного прямоугольника
    if x < x_min - eps or x > x_max + eps or y < y_min - eps or y > y_max + eps:
        return False

    if r <= 0:
        return True

    # 4 угла со скруглением
    # Верхний левый угол
    if x < x_min + r and y < y_min + r:
        dx = x - (x_min + r)
        dy = y - (y_min + r)
        return (dx * dx + dy * dy) <= (r * r + eps)

    # Верхний правый угол
    if x > x_max - r and y < y_min + r:
        dx = x - (x_max - r)
        dy = y - (y_min + r)
        return (dx * dx + dy * dy) <= (r * r + eps)

    # Нижний левый угол
    if x < x_min + r and y > y_max - r:
        dx = x - (x_min + r)
        dy = y - (y_max - r)
        return (dx * dx + dy * dy) <= (r * r + eps)

    # Нижний правый угол
    if x > x_max - r and y > y_max - r:
        dx = x - (x_max - r)
        dy = y - (y_max - r)
        return (dx * dx + dy * dy) <= (r * r + eps)

    return True


def is_sticker_inside_object(
    sticker_poly: Sequence[Union[Point2D, Sequence[float], Dict[str, float]]],
    object_mask: Union[Dict[str, Any], Any],
    safe_margin_mm: float | None = None,
) -> bool:
    """
    Проверяет, целиком ли полигон стикера помещается внутрь маски объекта.
    Поскольку скруглённый прямоугольник крышки строго выпуклый, выпуклый полигон стикера
    полностью лежит внутри объекта тогда и только тогда, когда все его вершины лежат внутри.
    """
    w, h, r, margin = _extract_object_geometry(object_mask, safe_margin_mm)

    for pt in sticker_poly:
        if isinstance(pt, dict):
            px, py = float(pt["x"]), float(pt["y"])
        else:
            px, py = float(pt[0]), float(pt[1])

        if not is_point_inside_rounded_rect(px, py, w, h, r, margin_mm=margin):
            return False

    return True
