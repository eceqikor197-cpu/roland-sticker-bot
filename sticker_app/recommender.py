"""
Модуль рекомендательной системы и вкусового профиля (S3).
Реализует:
- Farthest-Point Sampling (FPS) для разнообразных карточек онбординга.
- Построение вкусового профиля: normalized(mean(liked) - 0.5 * mean(disliked)) и топ-5 ассоциативных тегов.
- Maximal Marginal Relevance (MMR) ранжирование с параметром lambda=0.7 и ограничениями по темам.
- Обработку холодного старта (Cold Start) при < 2 лайков.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

import sticker_app.config as config
from sticker_app.features import (
    FeatureVocabulary,
    blob_to_vector,
)

logger = logging.getLogger("sticker_app.recommender")


class RecommenderEngine:
    """
    Движок рекомендаций.
    Кэширует матрицу векторов макетов и метаданные в оперативной памяти.
    """

    def __init__(self) -> None:
        self.asset_ids: List[str] = []
        self.matrix: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self.asset_id_to_idx: Dict[str, int] = {}
        self.asset_meta: Dict[str, Dict[str, Any]] = {}
        self.vocab: Optional[FeatureVocabulary] = None
        self._is_loaded: bool = False

    def load(
        self,
        app_db_path: Optional[Path] = None,
        harvester_db_path: Optional[Path] = None,
        force: bool = False,
    ) -> None:
        """
        Загружает векторы признаков и метаданные из app.db и harvester.db в память.
        """
        if self._is_loaded and not force:
            return

        a_path = (app_db_path or config.APP_DB_PATH).resolve()
        h_path = (harvester_db_path or config.HARVESTER_DB_PATH).resolve()

        if not a_path.exists():
            logger.warning("app.db не найдена: %s. Рекомендательный движок пуст.", a_path)
            self._reset()
            return

        # 1. Загружаем словарь признаков и векторы из app.db
        app_conn = sqlite3.connect(a_path.as_posix(), timeout=15.0)
        app_conn.row_factory = sqlite3.Row
        try:
            vocab_row = app_conn.execute(
                "SELECT vocabulary_json FROM feature_vocabularies ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if vocab_row:
                self.vocab = FeatureVocabulary.from_json(vocab_row["vocabulary_json"])

            feat_rows = app_conn.execute(
                "SELECT asset_id, vector FROM asset_features ORDER BY asset_id ASC"
            ).fetchall()
        finally:
            app_conn.close()

        if not feat_rows:
            logger.warning("В asset_features нет записей. Выполните python -m sticker_app.build_features.")
            self._reset()
            return

        asset_ids: List[str] = []
        vectors: List[np.ndarray] = []
        for r in feat_rows:
            aid = str(r["asset_id"])
            vec = blob_to_vector(r["vector"])
            # Проверяем L2-норму вектора
            norm = np.linalg.norm(vec)
            if norm > 1e-6:
                vec = vec / norm
            asset_ids.append(aid)
            vectors.append(vec)

        self.asset_ids = asset_ids
        self.matrix = np.vstack(vectors).astype(np.float32)
        self.asset_id_to_idx = {aid: i for i, aid in enumerate(asset_ids)}

        # 2. Загружаем метаданные макетов (тема, категория, теги)
        self.asset_meta = {}
        if h_path.exists():
            h_uri = f"file:{h_path.as_posix()}?mode=ro"
            h_conn = sqlite3.connect(h_uri, uri=True, timeout=15.0)
            h_conn.row_factory = sqlite3.Row
            try:
                h_conn.execute(f"ATTACH DATABASE '{a_path.as_posix()}' AS app;")
                meta_rows = h_conn.execute(
                    """
                    SELECT
                        a.asset_id,
                        a.category_lvl1,
                        a.category_lvl2,
                        a.folder_slug,
                        a.topic_name,
                        a.title,
                        m.tags_json,
                        m.palette_json
                    FROM assets a
                    JOIN app.asset_meta m ON a.asset_id = m.asset_id
                    WHERE m.usable_in_service = 1 AND m.takedown = 0
                    """
                ).fetchall()

                for mr in meta_rows:
                    aid = str(mr["asset_id"])
                    tags = []
                    if mr["tags_json"]:
                        try:
                            tags = json.loads(mr["tags_json"])
                        except Exception:
                            tags = []
                    self.asset_meta[aid] = {
                        "category_lvl1": str(mr["category_lvl1"] or ""),
                        "category_lvl2": str(mr["category_lvl2"] or ""),
                        "folder_slug": str(mr["folder_slug"] or ""),
                        "topic_name": str(mr["topic_name"] or ""),
                        "title": str(mr["title"] or ""),
                        "tags": tags,
                    }
            finally:
                h_conn.close()

        self._is_loaded = True
        logger.info(
            "RecommenderEngine загружен: %d макетов, размерность вектора: %d",
            len(self.asset_ids),
            self.matrix.shape[1] if len(self.asset_ids) > 0 else 0,
        )

    def _reset(self) -> None:
        self.asset_ids = []
        self.matrix = np.empty((0, 0), dtype=np.float32)
        self.asset_id_to_idx = {}
        self.asset_meta = {}
        self.vocab = None
        self._is_loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded and len(self.asset_ids) > 0

    # -------------------------------------------------------------------------
    # 1. Onboarding Cards: Farthest-Point Sampling (FPS)
    # -------------------------------------------------------------------------

    def get_onboarding_cards(
        self,
        n: int = 12,
        max_per_topic: int = 2,
    ) -> List[str]:
        """
        Выбирает n максимально разнообразных макетов с помощью Farthest-Point Sampling.
        Ограничение: не более max_per_topic стикеров из одной темы.
        """
        if not self.is_loaded:
            return []

        n_items = len(self.asset_ids)
        if n_items <= n:
            return list(self.asset_ids)

        selected_indices: List[int] = []
        topic_counts: Dict[str, int] = {}

        # Начальная точка: детерминированно выбираем индекс 0 (или наиболее удаленный от среднего)
        # Для абсолютной стабильности: индекс 0
        start_idx = 0
        selected_indices.append(start_idx)
        start_topic = self._get_topic(self.asset_ids[start_idx])
        topic_counts[start_topic] = 1

        # Инициализируем расстояния: d(i, j) = 1.0 - CosSim(i, j)
        # CosSim(i, j) = matrix @ matrix[start_idx]
        sims = self.matrix @ self.matrix[start_idx]
        min_dists = 1.0 - sims
        # Клиппируем расстояния в [0.0, 2.0]
        min_dists = np.clip(min_dists, 0.0, 2.0)

        while len(selected_indices) < n:
            # Находим индекс с максимальным min_dists среди допустимых кандидатов
            sorted_candidates = np.argsort(-min_dists)
            best_idx: Optional[int] = None

            for cand_idx in sorted_candidates:
                cand_idx = int(cand_idx)
                if cand_idx in selected_indices:
                    continue
                cand_topic = self._get_topic(self.asset_ids[cand_idx])
                if topic_counts.get(cand_topic, 0) >= max_per_topic:
                    continue
                best_idx = cand_idx
                break

            if best_idx is None:
                # Если с ограничением max_per_topic кандидаты исчерпаны, берем без ограничения
                for cand_idx in sorted_candidates:
                    cand_idx = int(cand_idx)
                    if cand_idx not in selected_indices:
                        best_idx = cand_idx
                        break

            if best_idx is None:
                break

            selected_indices.append(best_idx)
            sel_topic = self._get_topic(self.asset_ids[best_idx])
            topic_counts[sel_topic] = topic_counts.get(sel_topic, 0) + 1

            # Обновляем минимальные расстояния до выбранного множества
            new_sims = self.matrix @ self.matrix[best_idx]
            new_dists = np.clip(1.0 - new_sims, 0.0, 2.0)
            min_dists = np.minimum(min_dists, new_dists)

        return [self.asset_ids[idx] for idx in selected_indices]

    # -------------------------------------------------------------------------
    # 2. Taste Profile
    # -------------------------------------------------------------------------

    def build_taste_profile(
        self,
        liked_ids: Sequence[str],
        disliked_ids: Sequence[str] = (),
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Вычисляет вектор вкусового профиля:
        normalized(mean(liked) - 0.5 * mean(disliked))
        и топ-5 ассоциативных тегов.
        """
        if not self.is_loaded:
            return np.empty(0, dtype=np.float32), []

        dim = self.matrix.shape[1]

        liked_vectors = [
            self.matrix[self.asset_id_to_idx[aid]]
            for aid in liked_ids
            if aid in self.asset_id_to_idx
        ]
        disliked_vectors = [
            self.matrix[self.asset_id_to_idx[aid]]
            for aid in disliked_ids
            if aid in self.asset_id_to_idx
        ]

        if not liked_vectors and not disliked_vectors:
            # Пустой запрос: возвращаем средний вектор каталога
            centroid = np.mean(self.matrix, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 1e-6:
                centroid = centroid / norm
            return centroid.astype(np.float32), []

        if liked_vectors:
            mean_liked = np.mean(liked_vectors, axis=0)
        else:
            mean_liked = np.zeros(dim, dtype=np.float32)

        if disliked_vectors:
            mean_disliked = np.mean(disliked_vectors, axis=0)
        else:
            mean_disliked = np.zeros(dim, dtype=np.float32)

        # Формула профиля
        profile_vec = mean_liked - 0.5 * mean_disliked
        norm = np.linalg.norm(profile_vec)
        if norm > 1e-6:
            profile_vec = (profile_vec / norm).astype(np.float32)
        else:
            profile_vec = np.zeros(dim, dtype=np.float32)

        # Вычисление топ-5 ассоциативных тегов
        top_tags = self._extract_top_tags(liked_ids, disliked_ids, limit=5)

        return profile_vec, top_tags

    def _extract_top_tags(
        self,
        liked_ids: Sequence[str],
        disliked_ids: Sequence[str],
        limit: int = 5,
    ) -> List[str]:
        """Извлекает топ-5 тегов с положительным весом с учетом штрафа за дизлайки."""
        tag_scores: Dict[str, float] = {}

        for aid in liked_ids:
            meta = self.asset_meta.get(aid)
            if not meta:
                continue
            for t in meta.get("tags", []):
                # Вес тега масштабируется на его IDF (если словарь доступен)
                idf = 1.0
                if self.vocab and t in self.vocab.tag_to_id:
                    idf = float(self.vocab.idf[self.vocab.tag_to_id[t]])
                tag_scores[t] = tag_scores.get(t, 0.0) + idf

        for aid in disliked_ids:
            meta = self.asset_meta.get(aid)
            if not meta:
                continue
            for t in meta.get("tags", []):
                idf = 1.0
                if self.vocab and t in self.vocab.tag_to_id:
                    idf = float(self.vocab.idf[self.vocab.tag_to_id[t]])
                tag_scores[t] = tag_scores.get(t, 0.0) - 0.5 * idf

        # Оставляем только теги с положительным весом
        positive_tags = [(t, s) for t, s in tag_scores.items() if s > 0.0]
        # Сортируем по весу и детерминированно по имени тега
        positive_tags.sort(key=lambda x: (-x[1], x[0]))
        return [t for t, _ in positive_tags[:limit]]

    # -------------------------------------------------------------------------
    # 3. Recommendations: MMR Ranking & Cold Start
    # -------------------------------------------------------------------------

    def recommend(
        self,
        profile_vector: Optional[Sequence[float]] = None,
        liked_ids: Sequence[str] = (),
        disliked_ids: Sequence[str] = (),
        count: int = 20,
        max_per_topic: int = 3,
        min_unique_topics: int = 5,
        include_liked: bool = False,
        lambda_param: float = 0.7,
    ) -> List[Tuple[str, float]]:
        r"""
        Ранжирует макеты с диверсификацией через Maximal Marginal Relevance (MMR).
        MMR = argmax_{Di in R \ S} [lambda * Sim(Di, Q) - (1 - lambda) * max_{Dj in S} Sim(Di, Dj)]
        при lambda = 0.7.

        Возвращает список пар (asset_id, mmr_score).
        """
        if not self.is_loaded:
            return []

        # 1. Определение вектора запроса Q и режима Cold Start
        is_cold_start = len(liked_ids) < 2 and (
            profile_vector is None or len(profile_vector) == 0
        )

        if profile_vector is not None and len(profile_vector) == self.matrix.shape[1]:
            q_vec = np.array(profile_vector, dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm > 1e-6:
                q_vec = q_vec / q_norm
            else:
                q_vec, _ = self.build_taste_profile(liked_ids, disliked_ids)
        else:
            q_vec, _ = self.build_taste_profile(liked_ids, disliked_ids)

        # 2. Множество исключаемых макетов
        excluded_ids: Set[str] = set(disliked_ids)
        if not include_liked:
            excluded_ids.update(liked_ids)

        candidate_indices = [
            i for i, aid in enumerate(self.asset_ids)
            if aid not in excluded_ids
        ]

        if not candidate_indices:
            return []

        # Регулировка lambda при холодном старте для большего разнообразия
        effective_lambda = lambda_param
        if is_cold_start:
            effective_lambda = 0.5 if len(liked_ids) == 1 else 0.3

        # Сходство кандидатов с профилем Q: Sim(Di, Q) = matrix[i] @ q_vec
        cand_idx_arr = np.array(candidate_indices, dtype=np.int64)
        sim_to_query = self.matrix[cand_idx_arr] @ q_vec

        # Отобранное множество S
        selected_indices: List[int] = []
        selected_scores: List[float] = []
        topic_counts: Dict[str, int] = {}
        unique_topics: Set[str] = set()

        # max_sim_to_selected хранит max_{Dj in S} Sim(Di, Dj) для каждого кандидата
        max_sim_to_selected = np.full(len(candidate_indices), -1.0, dtype=np.float32)

        target_count = min(count, len(candidate_indices))

        while len(selected_indices) < target_count:
            remaining_slots = target_count - len(selected_indices)
            topics_needed = min_unique_topics - len(unique_topics)

            # Вычисляем MMR оценку для каждого оставшегося кандидата
            if len(selected_indices) == 0:
                mmr_scores = sim_to_query.copy()
            else:
                mmr_scores = (
                    effective_lambda * sim_to_query
                    - (1.0 - effective_lambda) * max_sim_to_selected
                )

            # Сортируем кандидатов по убыванию MMR оценки
            sorted_pos = np.argsort(-mmr_scores)
            chosen_pos: Optional[int] = None

            for pos in sorted_pos:
                cand_idx = candidate_indices[pos]
                if cand_idx in selected_indices:
                    continue

                topic = self._get_topic(self.asset_ids[cand_idx])
                count_in_topic = topic_counts.get(topic, 0)

                # Проверка ограничения max_per_topic
                if count_in_topic >= max_per_topic:
                    continue

                # Проверка ограничения min_unique_topics:
                # Если оставшихся слотов столько же или меньше, чем нужных уникальных тем,
                # требуем, чтобы тема была новой!
                if remaining_slots <= topics_needed and topic in unique_topics:
                    continue

                chosen_pos = int(pos)
                break

            if chosen_pos is None:
                # Ослабляем ограничение min_unique_topics, оставляя только max_per_topic
                for pos in sorted_pos:
                    cand_idx = candidate_indices[pos]
                    if cand_idx in selected_indices:
                        continue
                    topic = self._get_topic(self.asset_ids[cand_idx])
                    if topic_counts.get(topic, 0) >= max_per_topic:
                        continue
                    chosen_pos = int(pos)
                    break

            if chosen_pos is None:
                # Если всё исчерпано, берем любого оставшегося кандидата
                for pos in sorted_pos:
                    cand_idx = candidate_indices[pos]
                    if cand_idx not in selected_indices:
                        chosen_pos = int(pos)
                        break

            if chosen_pos is None:
                break

            best_global_idx = candidate_indices[chosen_pos]
            selected_indices.append(best_global_idx)
            selected_scores.append(float(mmr_scores[chosen_pos]))

            topic = self._get_topic(self.asset_ids[best_global_idx])
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            unique_topics.add(topic)

            # Обновляем max_sim_to_selected для всех кандидатов
            new_item_sims = self.matrix[cand_idx_arr] @ self.matrix[best_global_idx]
            max_sim_to_selected = np.maximum(max_sim_to_selected, new_item_sims)

        results: List[Tuple[str, float]] = []
        for idx, score in zip(selected_indices, selected_scores):
            results.append((self.asset_ids[idx], round(score, 4)))

        return results

    def _get_topic(self, asset_id: str) -> str:
        meta = self.asset_meta.get(asset_id)
        if meta and meta.get("topic_name"):
            return meta["topic_name"]
        if meta and meta.get("folder_slug"):
            return meta["folder_slug"]
        return "default_topic"


# Глобальный singleton движка рекомендаций
engine = RecommenderEngine()


def get_recommender_engine() -> RecommenderEngine:
    """Возвращает инициализированный движок рекомендаций."""
    global engine
    if not engine.is_loaded:
        engine.load()
    return engine
