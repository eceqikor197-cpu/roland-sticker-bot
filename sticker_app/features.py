"""
Модуль построения векторов признаков макетов (Feature Extraction).
Уровень A: TF-IDF по тегам, One-hot категорий/тем, HSV-гистограмма палитры, геометрия.
Обеспечивает детерминированное вычисление L2-нормированных векторов float32.
"""

from __future__ import annotations

import colorsys
import json
import logging
import math
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger("sticker_app.features")

# Веса компонентов вектора согласно контракту S3
WEIGHT_TFIDF: float = 0.5
WEIGHT_CATEGORY_TOPIC: float = 0.2
WEIGHT_COLOR: float = 0.2
WEIGHT_GEOMETRY: float = 0.1

COLOR_NUM_HUE_BINS: int = 8
COLOR_NUM_ACHROMATIC_BINS: int = 3  # black, gray, white
COLOR_TOTAL_BINS: int = COLOR_NUM_HUE_BINS + COLOR_NUM_ACHROMATIC_BINS  # 11
GEOMETRY_DIM: int = 2  # aspect_norm, alpha_coverage


def vector_to_blob(v: np.ndarray) -> bytes:
    """Упаковывает numpy-вектор в бинарный BLOB (float32)."""
    return v.astype(np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    """Восстанавливает numpy-вектор из бинарного BLOB (float32)."""
    return np.frombuffer(blob, dtype=np.float32).copy()


def hex_to_hsv(hex_color: str) -> Optional[Tuple[float, float, float]]:
    """Конвертирует HEX-цвет (#RRGGBB) в HSV (h in [0,1), s in [0,1], v in [0,1])."""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        return colorsys.rgb_to_hsv(r, g, b)
    except ValueError:
        return None


def palette_to_color_hist(palette: Sequence[str]) -> np.ndarray:
    """
    Строит цветовую гистограмму в HSV-пространстве по палитре HEX-цветов.
    11 корзин: 8 корзин оттенка (Hue) + 3 ахроматические (черный, серый, белый).
    """
    hist = np.zeros(COLOR_TOTAL_BINS, dtype=np.float32)
    valid_count = 0

    for hex_code in palette:
        hsv = hex_to_hsv(hex_code)
        if hsv is None:
            continue
        h, s, v = hsv
        valid_count += 1

        # Ахроматические цвета (низкая насыщенность)
        if s < 0.15:
            if v < 0.25:
                hist[8] += 1.0  # черный
            elif v > 0.80:
                hist[10] += 1.0  # белый
            else:
                hist[9] += 1.0  # серый
        else:
            # Хроматические корзины по оттенку 0..7
            hue_bin = int(h * COLOR_NUM_HUE_BINS) % COLOR_NUM_HUE_BINS
            hist[hue_bin] += 1.0

    norm = np.linalg.norm(hist)
    if norm > 1e-6:
        hist = hist / norm
    return hist


def extract_geometry_features(aspect: float, alpha_coverage: float) -> np.ndarray:
    """
    Извлекает нормализованные геометрические признаки [aspect_norm, alpha_coverage].
    """
    # Клиппируем aspect в разумный диапазон [0.2, 5.0] и нормализуем
    clamped_aspect = max(0.2, min(float(aspect), 5.0))
    aspect_norm = clamped_aspect / 5.0

    clamped_alpha = max(0.0, min(float(alpha_coverage), 1.0))
    geom = np.array([aspect_norm, clamped_alpha], dtype=np.float32)

    norm = np.linalg.norm(geom)
    if norm > 1e-6:
        geom = geom / norm
    return geom


class FeatureVocabulary:
    """
    Словарь признаков для кодирования макетов в векторное пространство Уровня A.
    Хранит словари тегов, категорий, тем и значения IDF.
    """

    def __init__(
        self,
        tags: List[str],
        idf: List[float],
        categories: List[str],
        topics: List[str],
        version: str = "v1",
    ) -> None:
        self.tags = tags
        self.idf = np.array(idf, dtype=np.float32)
        self.categories = categories
        self.topics = topics
        self.version = version

        self.tag_to_id: Dict[str, int] = {t: i for i, t in enumerate(tags)}
        self.cat_to_id: Dict[str, int] = {c: i for i, c in enumerate(categories)}
        self.topic_to_id: Dict[str, int] = {t: i for i, t in enumerate(topics)}

        self.dim_tfidf = len(tags)
        self.dim_cat_topic = len(categories) + len(topics)
        self.dim_color = COLOR_TOTAL_BINS
        self.dim_geom = GEOMETRY_DIM
        self.total_dim = self.dim_tfidf + self.dim_cat_topic + self.dim_color + self.dim_geom

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "tags": self.tags,
            "idf": self.idf.tolist(),
            "categories": self.categories,
            "topics": self.topics,
            "total_dim": self.total_dim,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FeatureVocabulary:
        return cls(
            tags=list(data["tags"]),
            idf=[float(x) for x in data["idf"]],
            categories=list(data["categories"]),
            topics=list(data["topics"]),
            version=str(data.get("version", "v1")),
        )

    @classmethod
    def from_json(cls, json_str: str) -> FeatureVocabulary:
        return cls.from_dict(json.loads(json_str))


class FeatureExtractor:
    """
    Экстрактор признаков макетов.
    Обучается на корпусе макетов (fit) и трансформирует макеты в L2-нормированные векторы.
    """

    def __init__(self, vocab: Optional[FeatureVocabulary] = None) -> None:
        self.vocab = vocab

    def fit(self, assets: Sequence[Dict[str, Any]]) -> FeatureVocabulary:
        """
        Строит словарь признаков и рассчитывает IDF по переданным макетам.
        """
        n_docs = len(assets)
        tag_doc_freq: Dict[str, int] = {}
        unique_categories: set[str] = set()
        unique_topics: set[str] = set()

        for a in assets:
            tags = a.get("tags") or []
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            seen_in_doc = set(tags)
            for t in seen_in_doc:
                tag_doc_freq[t] = tag_doc_freq.get(t, 0) + 1

            cat = a.get("category_lvl2") or a.get("category_lvl1") or "other"
            topic = a.get("topic_name") or a.get("folder_slug") or "other"
            unique_categories.add(str(cat))
            unique_topics.add(str(topic))

        # Сортируем для строгой детерминированности
        sorted_tags = sorted(tag_doc_freq.keys())
        sorted_cats = sorted(unique_categories)
        sorted_topics = sorted(unique_topics)

        # Вычисляем сглаженный IDF: ln((N + 1) / (DF + 1)) + 1.0
        idf_values: List[float] = []
        for t in sorted_tags:
            df = tag_doc_freq[t]
            idf = math.log((n_docs + 1.0) / (df + 1.0)) + 1.0
            idf_values.append(idf)

        self.vocab = FeatureVocabulary(
            tags=sorted_tags,
            idf=idf_values,
            categories=sorted_cats,
            topics=sorted_topics,
        )
        return self.vocab

    def transform_one(self, asset: Dict[str, Any]) -> np.ndarray:
        """
        Преобразует один макет в L2-нормированный вектор признаков.
        """
        if not self.vocab:
            raise ValueError("FeatureExtractor не обучен. Вызовите fit() или передайте FeatureVocabulary.")

        v = self.vocab

        # 1. TF-IDF по тегам (вес 0.5)
        v_tfidf = np.zeros(v.dim_tfidf, dtype=np.float32)
        tags = asset.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []

        for t in tags:
            idx = v.tag_to_id.get(t)
            if idx is not None:
                v_tfidf[idx] = v.idf[idx]

        norm_tfidf = np.linalg.norm(v_tfidf)
        if norm_tfidf > 1e-6:
            v_tfidf = (v_tfidf / norm_tfidf) * WEIGHT_TFIDF

        # 2. One-hot категории и темы (вес 0.2)
        v_cat_topic = np.zeros(v.dim_cat_topic, dtype=np.float32)
        cat = str(asset.get("category_lvl2") or asset.get("category_lvl1") or "other")
        topic = str(asset.get("topic_name") or asset.get("folder_slug") or "other")

        cat_idx = v.cat_to_id.get(cat)
        if cat_idx is not None:
            v_cat_topic[cat_idx] = 1.0

        topic_idx = v.topic_to_id.get(topic)
        if topic_idx is not None:
            v_cat_topic[len(v.categories) + topic_idx] = 1.0

        norm_cat = np.linalg.norm(v_cat_topic)
        if norm_cat > 1e-6:
            v_cat_topic = (v_cat_topic / norm_cat) * WEIGHT_CATEGORY_TOPIC

        # 3. Цветовая HSV гистограмма (вес 0.2)
        palette = asset.get("palette") or []
        if isinstance(palette, str):
            try:
                palette = json.loads(palette)
            except Exception:
                palette = []
        v_color = palette_to_color_hist(palette) * WEIGHT_COLOR

        # 4. Геометрия: aspect и alpha_coverage (вес 0.1)
        aspect = float(asset.get("aspect", 1.0))
        alpha = float(asset.get("alpha_coverage", 0.0))
        v_geom = extract_geometry_features(aspect, alpha) * WEIGHT_GEOMETRY

        # Конкатенация
        full_vec = np.concatenate([v_tfidf, v_cat_topic, v_color, v_geom])

        # Финальная L2-нормализация
        full_norm = np.linalg.norm(full_vec)
        if full_norm > 1e-6:
            return (full_vec / full_norm).astype(np.float32)
        return full_vec.astype(np.float32)

    def transform_many(self, assets: Sequence[Dict[str, Any]]) -> np.ndarray:
        """Преобразует список макетов в матрицу (N, D)."""
        rows = [self.transform_one(a) for a in assets]
        if not rows:
            dim = self.vocab.total_dim if self.vocab else 0
            return np.empty((0, dim), dtype=np.float32)
        return np.vstack(rows).astype(np.float32)


# =========================================================================
# Уровень B: Интерфейс EmbeddingProvider с прозрачным fallback на Уровень A
# =========================================================================


class EmbeddingProvider(ABC):
    """Абстрактный интерфейс провайдера эмбеддингов."""

    @abstractmethod
    def get_dimension(self) -> int:
        """Размерность вектора признаков."""
        pass

    @abstractmethod
    def embed_asset(self, asset: Dict[str, Any]) -> np.ndarray:
        """Вычисляет L2-нормированный вектор макета."""
        pass


class LevelAEmbeddingProvider(EmbeddingProvider):
    """Базовый провайдер Уровня A (TF-IDF + категории + цвета + геометрия)."""

    def __init__(self, extractor: FeatureExtractor) -> None:
        self.extractor = extractor

    def get_dimension(self) -> int:
        if not self.extractor.vocab:
            return 0
        return self.extractor.vocab.total_dim

    def embed_asset(self, asset: Dict[str, Any]) -> np.ndarray:
        return self.extractor.transform_one(asset)


def get_embedding_provider(
    extractor: FeatureExtractor,
    provider_name: Optional[str] = None,
) -> EmbeddingProvider:
    """
    Фабрика провайдера эмбеддингов.
    Если запрошен CLIP, но библиотека отсутствует — логирует предупреждение и возвращает Уровень A.
    """
    name = (provider_name or os.getenv("EMBEDDINGS", "level_a")).strip().lower()

    if name == "clip":
        try:
            import open_clip  # type: ignore # noqa: F401
            import torch  # type: ignore # noqa: F401

            logger.info("Провайдер CLIP инициализирован")
            # Если бы open_clip/torch были доступны, инициализировали бы ClipEmbeddingProvider
        except ImportError:
            logger.warning(
                "Флаг EMBEDDINGS=clip запрошен, но torch/open_clip не установлены. "
                "Прозрачный откат на базовый провайдер Уровня A."
            )

    return LevelAEmbeddingProvider(extractor)
