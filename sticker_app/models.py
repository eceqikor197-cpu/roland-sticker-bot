"""
Pydantic-модели для API каталога веб-сервиса стикербомбинга.
Строгое соблюдение контракта: никаких внутренних путей файловой системы в публичных ответах.
"""

from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field


class PaginationMeta(BaseModel):
    """Метаданные пагинации."""

    page: int = Field(..., ge=1, description="Номер текущей страницы (1-based)")
    page_size: int = Field(..., ge=1, le=100, description="Количество элементов на странице")
    total_items: int = Field(..., ge=0, description="Общее число элементов по фильтру")
    total_pages: int = Field(..., ge=0, description="Общее число страниц")
    has_next: bool = Field(..., description="Есть ли следующая страница")
    has_prev: bool = Field(..., description="Есть ли предыдущая страница")


class StickerSummary(BaseModel):
    """Краткая информация о стикере для карточек в каталоге."""

    asset_id: str = Field(..., description="Уникальный идентификатор стикера")
    title: str = Field(..., description="Название стикера")
    topic_name: str = Field(..., description="Название темы")
    folder_slug: str = Field(..., description="Слаг темы")
    category_lvl1: str = Field(..., description="Основная категория (уровень 1)")
    category_lvl2: str = Field(..., description="Подкатегория (уровень 2)")
    asset_type: str = Field(..., description="Тип макета (single_sticker, pack_sheet, etc.)")
    width_mm: float = Field(..., description="Физическая ширина в миллиметрах")
    height_mm: float = Field(..., description="Физическая высота в миллиметрах")
    aspect_ratio: float = Field(..., description="Пропорция растра (ширина / высота)")
    license: str = Field(..., description="Тип лицензии (own, licensed, cc0, unknown)")
    usable_in_service: bool = Field(..., description="Доступен ли макет для использования в сервисе")
    thumb_url: str = Field(..., description="URL миниатюры (до 256px, WebP)")
    preview_url: str = Field(..., description="URL превью (до 1024px, WebP)")


class StickerDetail(StickerSummary):
    """Детальная информация о макете стикера."""

    width_px: int = Field(..., description="Ширина исходного растра в пикселях")
    height_px: int = Field(..., description="Высота исходного растра в пикселях")
    offset_mm: float = Field(..., description="Отступ контура реза CutContour в мм")
    perf_cut_mode: str = Field(..., description="Режим перфорации/сквозного реза (die_cut, sheet_frame, none)")
    num_contours: int = Field(..., description="Число контуров CutContour")
    source_name: str = Field(..., description="Источник происхождения макета")
    created_at: float = Field(..., description="Время добавления (unix timestamp)")
    parent_asset_id: str = Field("", description="ID родительского листа, если стикер из пака")
    full_image_url: str = Field(..., description="URL полноразмерного исходного изображения")


class StickerListResponse(BaseModel):
    """Ответ со списком стикеров и пагинацией."""

    items: List[StickerSummary] = Field(default_factory=list, description="Список стикеров")
    meta: PaginationMeta = Field(..., description="Пагинация")


class TopicInfo(BaseModel):
    """Информация о теме каталога."""

    topic_id: int = Field(..., description="ID темы в базе")
    topic_name: str = Field(..., description="Название темы")
    folder_slug: str = Field(..., description="Слаг темы")
    category_lvl1: str = Field(..., description="Категория 1-го уровня")
    category_lvl2: str = Field(..., description="Подкатегория 2-го уровня")
    stickers_count: int = Field(..., ge=0, description="Количество доступных стикеров в теме")


class CategoryLevel2(BaseModel):
    """Подкатегория 2-го уровня с темами."""

    category_lvl2: str = Field(..., description="Название подкатегории")
    topics: List[TopicInfo] = Field(default_factory=list, description="Темы подкатегории")


class CategoryTree(BaseModel):
    """Иерархия категорий каталога (дерево)."""

    category_lvl1: str = Field(..., description="Основная категория 1-го уровня")
    subcategories: List[CategoryLevel2] = Field(default_factory=list, description="Список подкатегорий")


class HealthResponse(BaseModel):
    """Ответ проверки работоспособности сервиса."""

    status: str = Field("ok", description="Статус сервиса")
    version: str = Field("0.1.0", description="Версия API")
