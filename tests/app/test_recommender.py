"""
Тесты этапа S3: алгоритмы вкусового профиля, Farthest-Point Sampling (FPS) и MMR ранжирования.
"""

from __future__ import annotations

import numpy as np
import pytest

from sticker_app.build_features import build_features
from sticker_app.recommender import RecommenderEngine
from sticker_app.sync_catalog import sync_catalog
from tests.app.conftest import DemoCatalogContext


@pytest.fixture
def recommender_ready(demo_catalog: DemoCatalogContext) -> RecommenderEngine:
    """Фикстура: синхронизированный каталог, построенные признаки и загруженный RecommenderEngine."""
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
    engine = RecommenderEngine()
    engine.load(harvester_db_path=demo_catalog.db_path, force=True)
    return engine


def test_onboarding_cards_fps(recommender_ready: RecommenderEngine) -> None:
    """
    Проверка Farthest-Point Sampling для онбординга:
    - возвращает n карточек
    - не более max_per_topic из одной темы
    - разнообразие тем
    """
    engine = recommender_ready
    cards = engine.get_onboarding_cards(n=10, max_per_topic=2)

    assert len(cards) == 10
    # Проверка уникальности
    assert len(set(cards)) == len(cards)

    # Проверка ограничения max_per_topic
    topic_counts: dict[str, int] = {}
    for aid in cards:
        topic = engine._get_topic(aid)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    for topic, count in topic_counts.items():
        assert count <= 2, f"Тема {topic} превысила лимит 2 карточек (было {count})"

    # Проверяем, что представлены все темы
    assert len(topic_counts) >= 5


def test_taste_profile_and_top_tags(recommender_ready: RecommenderEngine) -> None:
    """
    Проверка вкусового профиля:
    - лайки в теме 'Ретро Вейв'
    - топ ассоциативных тегов релевантен
    - рекомендации сфокусированы на Ретро Вейв и Киберпанк Неон
    """
    engine = recommender_ready

    # Находим ID двух стикеров из темы 'Ретро Вейв'
    retro_ids = [
        aid for aid, meta in engine.asset_meta.items()
        if meta.get("topic_name") == "Ретро Вейв"
    ][:2]
    assert len(retro_ids) == 2

    # Строим профиль
    profile_vec, top_tags = engine.build_taste_profile(liked_ids=retro_ids)
    assert len(profile_vec) == engine.matrix.shape[1]
    assert np.linalg.norm(profile_vec) == pytest.approx(1.0, rel=1e-3)
    assert len(top_tags) > 0

    # Проверяем, что в топ-тегах есть характерные слова для Ретро Вейв
    tags_str = " ".join(top_tags)
    assert any(term in tags_str for term in ["ретро", "вейв", "retro", "wave", "неон", "синтвейв", "закат", "эстетика"])

    # Запрашиваем рекомендации
    recs = engine.recommend(
        profile_vector=profile_vec.tolist(),
        liked_ids=retro_ids,
        count=10,
        max_per_topic=3,
        min_unique_topics=3,
        include_liked=False,
    )
    assert len(recs) == 10

    # Проверяем, что лайкнутые стикеры исключены
    rec_ids = [aid for aid, _ in recs]
    for lid in retro_ids:
        assert lid not in rec_ids

    # В топе рекомендаций должны доминировать Ретро Вейв и Киберпанк
    rec_topics = [engine._get_topic(aid) for aid in rec_ids[:6]]
    aesthetic_topics = {"Ретро Вейв", "Киберпанк Неон"}
    matching = sum(1 for t in rec_topics if t in aesthetic_topics)
    assert matching >= 4, f"Ожидалось доминирование Ретро/Киберпанк в топе, получено: {rec_topics}"


def test_mmr_diversity_vs_pure_cosine(recommender_ready: RecommenderEngine) -> None:
    """
    Проверка MMR:
    При включении MMR с ограничениями количество уникальных тем в выдаче строго
    больше, чем при чистом косинусном ранжировании без диверсификации.
    """
    engine = recommender_ready

    # Лайкаем 2 стикера из Ретро Вейв
    retro_ids = [
        aid for aid, meta in engine.asset_meta.items()
        if meta.get("topic_name") == "Ретро Вейв"
    ][:2]

    profile_vec, _ = engine.build_taste_profile(liked_ids=retro_ids)

    # 1. Чистое косинусное ранжирование (lambda=1.0, без ограничения на тему)
    pure_recs = engine.recommend(
        profile_vector=profile_vec.tolist(),
        liked_ids=retro_ids,
        count=8,
        max_per_topic=8,
        min_unique_topics=1,
        lambda_param=1.0,
    )
    pure_topics = {engine._get_topic(aid) for aid, _ in pure_recs}

    # 2. MMR ранжирование с диверсификацией (lambda=0.7, max_per_topic=2, min_unique_topics=4)
    mmr_recs = engine.recommend(
        profile_vector=profile_vec.tolist(),
        liked_ids=retro_ids,
        count=8,
        max_per_topic=2,
        min_unique_topics=4,
        lambda_param=0.7,
    )
    mmr_topics = {engine._get_topic(aid) for aid, _ in mmr_recs}

    assert len(mmr_topics) > len(pure_topics), (
        f"MMR уникальных тем ({len(mmr_topics)}) должно быть строго больше, чем при чистом косинусе ({len(pure_topics)})"
    )
    assert len(mmr_topics) >= 4


def test_cold_start_empty_and_single_like(recommender_ready: RecommenderEngine) -> None:
    """
    Проверка холодного старта:
    - пустой запрос (0 лайков) возвращает валидный список разнообразных макетов
    - 1 лайк возвращает валидный список разнообразных макетов
    """
    engine = recommender_ready

    # 1. Пустой запрос
    empty_recs = engine.recommend(
        profile_vector=None,
        liked_ids=[],
        disliked_ids=[],
        count=10,
        max_per_topic=3,
        min_unique_topics=4,
    )
    assert len(empty_recs) == 10
    empty_topics = {engine._get_topic(aid) for aid, _ in empty_recs}
    assert len(empty_topics) >= 4

    # 2. 1 лайк
    single_id = engine.asset_ids[0]
    single_recs = engine.recommend(
        profile_vector=None,
        liked_ids=[single_id],
        count=10,
        max_per_topic=3,
        min_unique_topics=4,
        include_liked=False,
    )
    assert len(single_recs) == 10
    assert single_id not in [aid for aid, _ in single_recs]
    single_topics = {engine._get_topic(aid) for aid, _ in single_recs}
    assert len(single_topics) >= 4


def test_determinism_recommend(recommender_ready: RecommenderEngine) -> None:
    """Проверка детерминированности: одинаковый входной вектор дает идентичный результат."""
    engine = recommender_ready

    profile_vec, _ = engine.build_taste_profile(liked_ids=[engine.asset_ids[0], engine.asset_ids[1]])

    run1 = engine.recommend(profile_vector=profile_vec.tolist(), count=15, lambda_param=0.7)
    run2 = engine.recommend(profile_vector=profile_vec.tolist(), count=15, lambda_param=0.7)

    assert run1 == run2
