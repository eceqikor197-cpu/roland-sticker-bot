"""
Модуль загрузки и управления геометрическими описаниями объектов оклейки (MacBook).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional

from sticker_app import config
from sticker_app.models import ObjectItem

logger = logging.getLogger("sticker_app.objects")

_OBJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def list_objects(objects_dir: Optional[Path] = None) -> List[ObjectItem]:
    """Загружает и возвращает список всех поддерживаемых объектов."""
    dir_path = objects_dir or config.OBJECTS_DIR
    if not dir_path.exists():
        logger.warning("Каталог объектов %s не существует", dir_path)
        return []

    objects: List[ObjectItem] = []
    for json_file in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            item = ObjectItem.model_validate(data)
            objects.append(item)
        except Exception as e:
            logger.error("Ошибка чтения объекта из %s: %s", json_file.name, e)
    return objects


def get_object(object_id: str, objects_dir: Optional[Path] = None) -> Optional[ObjectItem]:
    """Возвращает описание объекта по его id или None, если не найден."""
    if not _OBJECT_ID_RE.match(object_id):
        return None

    dir_path = objects_dir or config.OBJECTS_DIR
    file_path = (dir_path / f"{object_id}.json").resolve()

    # Защита от path traversal
    try:
        file_path.relative_to(dir_path.resolve())
    except ValueError:
        return None

    if not file_path.is_file():
        return None

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return ObjectItem.model_validate(data)
    except Exception as e:
        logger.error("Ошибка парсинга объекта %s: %s", object_id, e)
        return None
