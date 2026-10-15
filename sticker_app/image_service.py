"""
Сервис масштабирования, кэширования и раздачи изображений макетов.
Поддерживает размеры:
- thumb: до 256 px по большей стороне, WebP (LANCZOS) с сохранением альфа-канала.
- preview: до 1024 px по большей стороне, WebP (LANCZOS) с альфа-каналом.
- full: исходный PNG без пережатия.
Кэширует миниатюры на диске в CACHE_DIR и вычисляет ETag.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

import sticker_app.config as config
from sticker_app.catalog_reader import get_asset_file_path

logger = logging.getLogger("sticker_app.image_service")

SIZE_LIMITS = {
    "thumb": 256,
    "preview": 1024,
}


def compute_etag(file_path: Path) -> str:
    """Генерирует ETag на основе mtime и размера файла."""
    stat = file_path.stat()
    raw = f"{file_path.name}_{stat.st_mtime_ns}_{stat.st_size}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f'"{digest}"'


def get_or_generate_image(
    asset_id: str,
    size: str = "preview",
    cache_dir: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
    """
    Возвращает (file_path, content_type, etag) для запрошенного размера.
    Если файл/макет не найден, возвращает (None, None, None).
    """
    size = size.lower().strip()
    if size not in ("thumb", "preview", "full"):
        raise ValueError(f"Недопустимый размер изображения: '{size}'. Допустимы: thumb, preview, full")

    # 1. Полноразмерный исходник (full)
    if size == "full":
        orig_path, content_type = get_asset_file_path(
            asset_id, file_type="png", db_path=db_path, storage_root=storage_root
        )
        if not orig_path or not orig_path.exists():
            return None, None, None
        return orig_path, content_type, compute_etag(orig_path)

    # 2. Миниатюра (thumb или preview)
    c_dir = (cache_dir or config.CACHE_DIR).resolve()
    target_cache_dir = c_dir / size
    cached_file = target_cache_dir / f"{asset_id}.webp"

    if cached_file.exists():
        return cached_file, "image/webp", compute_etag(cached_file)

    # Исходный PNG
    orig_path, _ = get_asset_file_path(
        asset_id, file_type="png", db_path=db_path, storage_root=storage_root
    )
    if not orig_path or not orig_path.exists():
        return None, None, None

    max_dim = SIZE_LIMITS[size]
    target_cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(orig_path) as img:
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            # Сохраняем в WebP с сохранением альфа-канала
            cached_file_temp = target_cache_dir / f"{asset_id}.tmp.webp"
            img.save(cached_file_temp, format="WEBP", quality=90, method=6)
            cached_file_temp.replace(cached_file)

        return cached_file, "image/webp", compute_etag(cached_file)
    except Exception as exc:
        logger.error("Ошибка генерации превью %s для %s: %s", size, asset_id, exc, exc_info=True)
        return None, None, None
