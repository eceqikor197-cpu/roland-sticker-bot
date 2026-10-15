"""
Pydantic-модели для API каталога веб-сервиса стикербомбинга.
Строгое соблюдение контракта: никаких внутренних путей файловой системы в публичных ответах.
"""

from __future__ import annotations

from typing import List, Optional, Union
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
    aspect: float = Field(1.0, description="Пропорция растра (ширина / высота)")
    aspect_ratio: float = Field(1.0, description="Пропорция растра (алиас для обратной совместимости)")
    alpha_coverage: float = Field(0.0, description="Доля непрозрачных пикселей (0.0..1.0)")
    tags: List[str] = Field(default_factory=list, description="Список тегов макета")
    palette: List[str] = Field(default_factory=list, description="5 доминирующих HEX-цветов")
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


class TagInfo(BaseModel):
    """Информация о теге каталога с частотой использования."""

    name: str = Field(..., description="Название тега")
    count: int = Field(..., ge=0, description="Частота использования среди доступных стикеров")


class TasteProfileRequest(BaseModel):
    """Запрос на вычисление вкусового профиля."""

    liked: List[str] = Field(default_factory=list, description="Список ID понравившихся стикеров")
    disliked: List[str] = Field(default_factory=list, description="Список ID непонравившихся стикеров")


class TasteProfileResponse(BaseModel):
    """Ответ с вектором вкусового профиля и топ-тегами."""

    profile_vector: List[float] = Field(..., description="L2-нормированный вектор вкусового профиля")
    top_tags: List[str] = Field(default_factory=list, description="Топ-5 ассоциативных тегов пользователя для UI")


class RecommendConstraints(BaseModel):
    """Ограничения диверсификации для рекомендаций."""

    max_per_topic: int = Field(3, ge=1, le=50, description="Максимум стикеров из одной темы")
    min_unique_topics: int = Field(5, ge=1, le=50, description="Минимум уникальных тем в выдаче")


class RecommendRequest(BaseModel):
    """Запрос на получение персональных рекомендаций."""

    profile_vector: Optional[List[float]] = Field(None, description="Вектор вкусового профиля")
    liked: List[str] = Field(default_factory=list, description="Список ID лайкнутых стикеров")
    disliked: List[str] = Field(default_factory=list, description="Список ID дизлайкнутых стикеров")
    count: int = Field(20, ge=1, le=100, description="Количество рекомендаций")
    constraints: Optional[RecommendConstraints] = Field(default_factory=RecommendConstraints, description="Ограничения диверсификации")
    include_liked: bool = Field(False, description="Включать ли уже лайкнутые стикеры в выдачу")


class RecommendedSticker(StickerSummary):
    """Карточка рекомендованного стикера с оценкой соответствия."""

    score: float = Field(0.0, description="Оценка соответствия MMR")


class RecommendResponse(BaseModel):
    """Ответ с персональными рекомендациями."""

    items: List[RecommendedSticker] = Field(default_factory=list, description="Список рекомендованных стикеров")
    total: int = Field(..., description="Количество возвращенных рекомендаций")
    is_cold_start: bool = Field(False, description="Флаг режима холодного старта")


class ObjectItem(BaseModel):
    """Описание геометрии объекта оклейки."""

    id: str = Field(..., description="Уникальный идентификатор объекта")
    title: str = Field(..., description="Человекочитаемое название объекта")
    width_mm: float = Field(..., gt=0, description="Физическая ширина в миллиметрах")
    height_mm: float = Field(..., gt=0, description="Физическая высота в миллиметрах")
    corner_radius_mm: float = Field(..., ge=0, description="Радиус скругления углов в миллиметрах")
    mask_svg_path_mm: str = Field(..., description="SVG path маски контура в миллиметрах")
    safe_margin_mm: float = Field(..., ge=0, description="Безопасный отступ от края в миллиметрах")
    notes: Optional[str] = Field(None, description="Примечания и статус замера")
    verified: bool = Field(False, description="Подтверждены ли размеры физическим замером")


class ObjectsListResponse(BaseModel):
    """Список поддерживаемых объектов оклейки."""

    items: List[ObjectItem] = Field(default_factory=list, description="Список объектов")
    total: int = Field(..., description="Общее количество объектов")


class DesignPlacement(BaseModel):
    """Параметры размещения одного стикера на объекте."""

    asset_id: str = Field(..., min_length=1, description="Идентификатор макета стикера")
    x_mm: float = Field(..., description="Положение геометрического центра по X в миллиметрах")
    y_mm: float = Field(..., description="Положение геометрического центра по Y в миллиметрах")
    width_mm: float = Field(..., ge=15.0, le=150.0, description="Ширина стикера в мм (от 15 до 150 мм)")
    rotation_deg: float = Field(0.0, ge=-360.0, le=360.0, description="Угол поворота по часовой стрелке в градусах")
    z: int = Field(0, ge=0, description="Порядковый номер слоя (z-index)")
    locked: bool = Field(False, description="Флаг блокировки от изменений")


class DesignData(BaseModel):
    """Полная структура сохранённой композиции/дизайна."""

    schema_version: str = Field("1.0", description="Версия схемы дизайна")
    object_id: str = Field(..., min_length=1, description="Идентификатор целевого объекта оклейки")
    placements: List[DesignPlacement] = Field(
        default_factory=list,
        max_length=150,
        description="Список размещений стикеров (до 150 шт.)",
    )
    created_at: Optional[Union[float, str]] = Field(None, description="Время создания (timestamp или ISO)")


class DesignValidationResult(BaseModel):
    """Результат валидации композиции стикербомбинга."""

    valid: bool = Field(..., description="Соответствует ли дизайн всем правилам и ограничениям")
    placements_count: int = Field(..., description="Количество стикеров в дизайне")
    errors: List[str] = Field(default_factory=list, description="Список критических ошибок (выход за пределы)")
    warnings: List[str] = Field(default_factory=list, description="Список предупреждений (нарушение safe_margin)")

