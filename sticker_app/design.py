"""
Модуль валидации и работы со схемами дизайна стикербомбинга (design.schema.json).
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import ValidationError

from sticker_app.geometry import get_sticker_polygon, is_sticker_inside_object
from sticker_app.models import DesignData, ObjectItem
from sticker_app.objects import get_object


class DesignValidationError(ValueError):
    """Ошибка валидации структуры или параметров дизайна."""


def validate_design_data(raw_data: Union[Dict[str, Any], str, bytes]) -> DesignData:
    """
    Валидирует входные данные дизайна (словарь или JSON-строку) на соответствие
    схеме design.schema.json и ограничениям предметной области.
    """
    if isinstance(raw_data, (str, bytes)):
        try:
            data = json.loads(raw_data)
        except Exception as e:
            raise DesignValidationError(f"Некорректный JSON: {e}") from e
    elif isinstance(raw_data, dict):
        data = raw_data
    else:
        raise DesignValidationError("Данные дизайна должны быть словарем или JSON-строкой")

    schema_version = data.get("schema_version")
    if schema_version != "1.0":
        raise DesignValidationError(
            f"Неподдерживаемая версия схемы: '{schema_version}'. Ожидается '1.0'."
        )

    try:
        design = DesignData.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        raise DesignValidationError("; ".join(errors)) from e

    # Проверка конечных чисел координат
    for idx, p in enumerate(design.placements):
        if not math.isfinite(p.x_mm) or not math.isfinite(p.y_mm):
            raise DesignValidationError(f"Размещение #{idx}: координаты x_mm и y_mm должны быть конечными числами")
        if not math.isfinite(p.width_mm):
            raise DesignValidationError(f"Размещение #{idx}: ширина width_mm должна быть конечным числом")
        if not math.isfinite(p.rotation_deg):
            raise DesignValidationError(f"Размещение #{idx}: угол rotation_deg должен быть конечным числом")

    return design


def check_design_boundaries(
    design: DesignData,
    object_item: Optional[ObjectItem] = None,
    default_sticker_height_ratio: float = 1.0,
) -> Tuple[List[str], List[str]]:
    """
    Проверяет расположение стикеров относительно границ целевого объекта.
    Возвращает кортеж (errors, warnings):
    - errors: стикеры, выходящие за физический контур объекта.
    - warnings: стикеры, нарушающие безопасный отступ (safe_margin).
    """
    obj = object_item or get_object(design.object_id)
    if obj is None:
        return [f"Целевой объект '{design.object_id}' не найден в каталоге"], []

    errors: List[str] = []
    warnings: List[str] = []

    for idx, p in enumerate(design.placements):
        h_mm = p.width_mm * default_sticker_height_ratio
        poly = get_sticker_polygon(p.x_mm, p.y_mm, p.width_mm, h_mm, p.rotation_deg)

        if not is_sticker_inside_object(poly, obj, safe_margin_mm=0.0):
            errors.append(
                f"Стикер #{idx} ({p.asset_id}) выходит за пределы контура объекта {obj.id}"
            )
        elif not is_sticker_inside_object(poly, obj, safe_margin_mm=obj.safe_margin_mm):
            warnings.append(
                f"Стикер #{idx} ({p.asset_id}) нарушает безопасную зону отступа ({obj.safe_margin_mm} мм)"
            )

    return errors, warnings
