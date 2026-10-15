"""
FastAPI бэкенд веб-сервиса стикербомбинга.
Реализует REST API для доступа к каталогу макетов, поиска и раздачи изображений.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import sticker_app.config as config
from sticker_app.catalog_reader import (
    get_sticker,
    get_stickers_by_ids,
    list_categories,
    list_popular_tags,
    list_stickers,
)
from sticker_app.image_service import get_or_generate_image
from sticker_app.models import (
    CategoryTree,
    HealthResponse,
    ObjectItem,
    ObjectsListResponse,
    RecommendRequest,
    RecommendResponse,
    RecommendedSticker,
    StickerDetail,
    StickerListResponse,
    StickerSummary,
    TagInfo,
    TasteProfileRequest,
    TasteProfileResponse,
)
from sticker_app.objects import get_object, list_objects
from sticker_app.recommender import get_recommender_engine

logger = logging.getLogger("sticker_app")

app = FastAPI(
    title="Roland Stickerbomb API",
    description="REST API для каталога макетов и кастомайзера стикербомбинга",
    version="0.1.0",
    docs_url=f"{config.API_PREFIX}/docs",
    redoc_url=f"{config.API_PREFIX}/redoc",
    openapi_url=f"{config.API_PREFIX}/openapi.json",
)

# CORS строго из конфигурации, без '*' в allow_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get(
    f"{config.API_PREFIX}/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Проверка работоспособности сервиса",
)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@app.get(
    f"{config.API_PREFIX}/stickers",
    response_model=StickerListResponse,
    tags=["Catalog"],
    summary="Список стикеров каталога с пагинацией и фильтрами",
)
def get_stickers(
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(30, ge=1, le=100, description="Количество элементов на странице"),
    asset_type: Optional[str] = Query("single_sticker", description="Тип макета (single_sticker, pack_sheet)"),
    category_lvl1: Optional[str] = Query(None, description="Фильтр по категории 1 уровня"),
    category_lvl2: Optional[str] = Query(None, description="Фильтр по подкатегории 2 уровня"),
    folder_slug: Optional[str] = Query(None, description="Слаг папки темы"),
    topic_name: Optional[str] = Query(None, description="Название темы"),
    search: Optional[str] = Query(None, description="Поисковый запрос по названию или теме"),
) -> StickerListResponse:
    filters = {
        "asset_type": asset_type,
        "category_lvl1": category_lvl1,
        "category_lvl2": category_lvl2,
        "folder_slug": folder_slug,
        "topic_name": topic_name,
        "search": search,
    }
    return list_stickers(filters=filters, page=page, page_size=page_size)


@app.get(
    f"{config.API_PREFIX}/stickers/{{asset_id}}",
    response_model=StickerDetail,
    tags=["Catalog"],
    summary="Детальная информация о макете",
)
def get_sticker_detail(asset_id: str) -> StickerDetail:
    sticker = get_sticker(asset_id)
    if not sticker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Стикер с ID '{asset_id}' не найден",
        )
    return sticker


@app.get(
    f"{config.API_PREFIX}/stickers/{{asset_id}}/image",
    tags=["Images"],
    summary="Раздача изображений макетов (thumb, preview, full)",
    response_class=FileResponse,
)
def get_sticker_image(
    asset_id: str,
    request: Request,
    size: str = Query("preview", description="Размер: thumb (256px), preview (1024px), full (оригинал)"),
    if_none_match: Optional[str] = Header(None),
) -> Response:
    valid_sizes = {"thumb", "preview", "full"}
    normalized_size = size.lower().strip()
    if normalized_size not in valid_sizes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимый параметр size='{size}'. Допустимо: {', '.join(sorted(valid_sizes))}",
        )

    file_path, content_type, etag = get_or_generate_image(asset_id, size=normalized_size)
    if not file_path or not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Изображение для стикера '{asset_id}' не найдено",
        )

    # Проверка ETag для ответа 304 Not Modified
    client_etag = if_none_match or request.headers.get("if-none-match")
    if client_etag and etag and client_etag.strip('"') == etag.strip('"'):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    headers = {
        "Cache-Control": "public, max-age=86400",
        "ETag": etag or '""',
    }
    return FileResponse(
        path=file_path,
        media_type=content_type or "image/png",
        headers=headers,
    )


@app.get(
    f"{config.API_PREFIX}/categories",
    response_model=List[CategoryTree],
    tags=["Catalog"],
    summary="Иерархия категорий и тем каталога",
)
def get_categories(
    asset_type: Optional[str] = Query("single_sticker", description="Тип макетов"),
) -> List[CategoryTree]:
    return list_categories(asset_type=asset_type or "single_sticker")


@app.get(
    f"{config.API_PREFIX}/tags",
    response_model=List[TagInfo],
    tags=["Catalog"],
    summary="Список популярных тегов каталога с частотой использования",
)
def get_tags(
    limit: int = Query(50, ge=1, le=200, description="Количество возвращаемых тегов"),
) -> List[TagInfo]:
    return list_popular_tags(limit=limit)


@app.get(
    f"{config.API_PREFIX}/onboarding/cards",
    response_model=List[StickerSummary],
    tags=["Recommender"],
    summary="Разнообразный набор карточек для онбординга вкуса (FPS)",
)
def get_onboarding_cards(
    n: int = Query(12, ge=1, le=50, description="Количество карточек для онбординга"),
) -> List[StickerSummary]:
    engine = get_recommender_engine()
    card_ids = engine.get_onboarding_cards(n=n, max_per_topic=2)
    return get_stickers_by_ids(card_ids)


@app.post(
    f"{config.API_PREFIX}/taste/profile",
    response_model=TasteProfileResponse,
    tags=["Recommender"],
    summary="Расчет вкусового профиля по лайкам и дизлайкам",
)
def compute_taste_profile(body: TasteProfileRequest) -> TasteProfileResponse:
    engine = get_recommender_engine()
    profile_vec, top_tags = engine.build_taste_profile(
        liked_ids=body.liked,
        disliked_ids=body.disliked,
    )
    return TasteProfileResponse(
        profile_vector=profile_vec.tolist(),
        top_tags=top_tags,
    )


@app.post(
    f"{config.API_PREFIX}/recommend",
    response_model=RecommendResponse,
    tags=["Recommender"],
    summary="Персональные рекомендации с MMR диверсификацией",
)
def get_recommendations(body: RecommendRequest) -> RecommendResponse:
    engine = get_recommender_engine()
    max_per_topic = body.constraints.max_per_topic if body.constraints else 3
    min_unique_topics = body.constraints.min_unique_topics if body.constraints else 5

    is_cold_start = len(body.liked) < 2 and (
        body.profile_vector is None or len(body.profile_vector) == 0
    )

    scored_items = engine.recommend(
        profile_vector=body.profile_vector,
        liked_ids=body.liked,
        disliked_ids=body.disliked,
        count=body.count,
        max_per_topic=max_per_topic,
        min_unique_topics=min_unique_topics,
        include_liked=body.include_liked,
    )

    ids = [aid for aid, _ in scored_items]
    summaries = get_stickers_by_ids(ids)
    score_map = dict(scored_items)

    recommended = [
        RecommendedSticker(**s.model_dump(), score=score_map.get(s.asset_id, 0.0))
        for s in summaries
    ]

    return RecommendResponse(
        items=recommended,
        total=len(recommended),
        is_cold_start=is_cold_start,
    )


@app.get(
    f"{config.API_PREFIX}/objects",
    response_model=ObjectsListResponse,
    summary="Список поддерживаемых объектов оклейки",
    tags=["Objects"],
)
def api_list_objects() -> ObjectsListResponse:
    """Возвращает список всех доступных объектов оклейки (моделей ноутбуков)."""
    items = list_objects()
    return ObjectsListResponse(items=items, total=len(items))


@app.get(
    f"{config.API_PREFIX}/objects/{{object_id}}",
    response_model=ObjectItem,
    summary="Геометрия объекта оклейки по идентификатору",
    tags=["Objects"],
)
def api_get_object(object_id: str) -> ObjectItem:
    """Возвращает геометрию, размеры и SVG-маску конкретного объекта."""
    obj = get_object(object_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Объект с ID '{object_id}' не найден",
        )
    return obj


