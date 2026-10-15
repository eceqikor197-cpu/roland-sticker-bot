"""
Тесты REST API для системы рекомендаций и вкусового профиля (S3).
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sticker_app.build_features import build_features
from sticker_app.main import app
from sticker_app.recommender import get_recommender_engine
from sticker_app.sync_catalog import sync_catalog
from tests.app.conftest import DemoCatalogContext


@pytest.fixture
def recommender_client(demo_catalog: DemoCatalogContext) -> TestClient:
    """Инициализирует тестовый клиент FastAPI с готовыми данными рекомендаций."""
    sync_catalog(
        harvester_db_path=demo_catalog.db_path,
        storage_root=demo_catalog.storage_root,
        dry_run=False,
    )
    build_features(
        harvester_db_path=demo_catalog.db_path,
        dry_run=False,
        verbose=False,
    )
    # Перезагружаем движок рекомендаций для подхвата базы
    engine = get_recommender_engine()
    engine.load(harvester_db_path=demo_catalog.db_path, force=True)

    return TestClient(app)


def test_api_onboarding_cards(recommender_client: TestClient) -> None:
    """Проверка GET /api/v1/onboarding/cards."""
    # 1. Запрос n=10: при 5 темах в каталоге гарантирует ровно <= 2 на тему
    resp = recommender_client.get("/api/v1/onboarding/cards?n=10")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data, list)
    assert len(data) == 10

    # Проверка структуры карточки StickerSummary
    card = data[0]
    assert "asset_id" in card
    assert "title" in card
    assert "topic_name" in card
    assert "thumb_url" in card
    assert "preview_url" in card
    assert "tags" in card
    assert "palette" in card
    assert "aspect" in card

    # Проверка ограничения: не более 2 стикеров из одной темы
    topic_counts: dict[str, int] = {}
    for c in data:
        t = c["topic_name"]
        topic_counts[t] = topic_counts.get(t, 0) + 1

    for t, count in topic_counts.items():
        assert count <= 2, f"Тема '{t}' превысила лимит 2 карточек (было {count})"
    assert len(topic_counts) == 5

    # 2. Запрос n=12 по умолчанию (при 5 темах равномерно расширяет лимит до 3)
    resp12 = recommender_client.get("/api/v1/onboarding/cards?n=12")
    assert resp12.status_code == 200
    data12 = resp12.json()
    assert len(data12) == 12
    counts12: dict[str, int] = {}
    for c in data12:
        t = c["topic_name"]
        counts12[t] = counts12.get(t, 0) + 1
    for t, count in counts12.items():
        assert count <= 3, f"Тема '{t}' превысила сбалансированный лимит 3 (было {count})"


def test_api_taste_profile(recommender_client: TestClient) -> None:
    """Проверка POST /api/v1/taste/profile."""
    cards_resp = recommender_client.get("/api/v1/onboarding/cards?n=5")
    assert cards_resp.status_code == 200
    cards = cards_resp.json()
    liked_ids = [cards[0]["asset_id"], cards[1]["asset_id"]]
    disliked_ids = [cards[2]["asset_id"]]

    payload = {
        "liked": liked_ids,
        "disliked": disliked_ids,
    }
    resp = recommender_client.post("/api/v1/taste/profile", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert "profile_vector" in data
    assert "top_tags" in data

    vec = np.array(data["profile_vector"], dtype=np.float32)
    assert len(vec) > 0
    assert np.linalg.norm(vec) == pytest.approx(1.0, rel=1e-3)
    assert isinstance(data["top_tags"], list)


def test_api_recommend(recommender_client: TestClient) -> None:
    """Проверка POST /api/v1/recommend с профилем и ограничениями MMR."""
    cards_resp = recommender_client.get("/api/v1/onboarding/cards?n=5")
    cards = cards_resp.json()
    liked_ids = [cards[0]["asset_id"], cards[1]["asset_id"]]

    # Получаем профиль
    profile_resp = recommender_client.post("/api/v1/taste/profile", json={"liked": liked_ids})
    profile_vector = profile_resp.json()["profile_vector"]

    # Запрашиваем рекомендации
    payload = {
        "profile_vector": profile_vector,
        "liked": liked_ids,
        "count": 10,
        "constraints": {
            "max_per_topic": 2,
            "min_unique_topics": 4,
        },
        "include_liked": False,
    }
    resp = recommender_client.post("/api/v1/recommend", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 10
    assert len(data["items"]) == 10
    assert data["is_cold_start"] is False

    # Проверка полей RecommendedSticker
    first_item = data["items"][0]
    assert "score" in first_item
    assert "asset_id" in first_item
    assert "thumb_url" in first_item

    # Проверка, что лайкнутые исключены
    rec_ids = [item["asset_id"] for item in data["items"]]
    for lid in liked_ids:
        assert lid not in rec_ids

    # Проверка ограничений темы
    topic_counts: dict[str, int] = {}
    for item in data["items"]:
        t = item["topic_name"]
        topic_counts[t] = topic_counts.get(t, 0) + 1

    for t, count in topic_counts.items():
        assert count <= 2, f"Тема '{t}' превысила max_per_topic 2 (было {count})"
    assert len(topic_counts) >= 4


def test_api_recommend_cold_start(recommender_client: TestClient) -> None:
    """Проверка POST /api/v1/recommend в режиме холодного старта (без лайков)."""
    payload = {
        "count": 8,
        "constraints": {
            "max_per_topic": 2,
            "min_unique_topics": 4,
        },
    }
    resp = recommender_client.post("/api/v1/recommend", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 8
    assert data["is_cold_start"] is True
    assert len(data["items"]) == 8

    unique_topics = {item["topic_name"] for item in data["items"]}
    assert len(unique_topics) >= 4
