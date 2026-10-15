#!/usr/bin/env python3
"""
Скрипт оценки качества рекомендаций и диверсификации MMR (S3).
python scripts/eval_recommender.py [--db PATH] [--storage-root PATH]

Вычисляет метрики:
1. Intra-List Similarity (ILS) — среднее попарное сходство внутри выдачи (чем ниже, тем выше разнообразие).
2. Topic Diversity — число уникальных тем и энтропия Шеннона по темам в выдаче.
3. Topic Relevance (Precision@K) — доля релевантных тем в топ-K выдаче.
Сравнивает Pure Cosine (lambda=1.0) vs MMR (lambda=0.7).
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np

import sticker_app.config as config
from scripts.make_demo_catalog import create_demo_catalog
from sticker_app.build_features import build_features
from sticker_app.recommender import RecommenderEngine
from sticker_app.sync_catalog import sync_catalog


def compute_intra_list_similarity(engine: RecommenderEngine, asset_ids: List[str]) -> float:
    """Вычисляет среднее попарное косинусное сходство между элементами выдачи."""
    k = len(asset_ids)
    if k < 2:
        return 0.0

    indices = [engine.asset_id_to_idx[aid] for aid in asset_ids if aid in engine.asset_id_to_idx]
    if len(indices) < 2:
        return 0.0

    vectors = engine.matrix[indices]  # (k, D)
    # Попарные скалярные произведения: (k, k)
    sim_matrix = vectors @ vectors.T

    # Суммируем только элементы выше главной диагонали
    triu_indices = np.triu_indices(len(indices), k=1)
    pairwise_sims = sim_matrix[triu_indices]
    return float(np.mean(pairwise_sims))


def compute_topic_entropy(engine: RecommenderEngine, asset_ids: List[str]) -> float:
    """Вычисляет энтропию Шеннона распределения тем в выдаче (H = -sum p*log2(p))."""
    if not asset_ids:
        return 0.0

    topic_counts: Dict[str, int] = {}
    for aid in asset_ids:
        topic = engine._get_topic(aid)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    total = len(asset_ids)
    entropy = 0.0
    for count in topic_counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def compute_topic_relevance(
    engine: RecommenderEngine,
    asset_ids: List[str],
    relevant_topics: Set[str],
) -> float:
    """Вычисляет Precision@K: долю элементов из релевантных тем в топ-K."""
    if not asset_ids:
        return 0.0
    hits = sum(1 for aid in asset_ids if engine._get_topic(aid) in relevant_topics)
    return hits / len(asset_ids)


def evaluate_engine(engine: RecommenderEngine, k: int = 10) -> Dict[str, Any]:
    """
    Проводит сравнительную оценку Pure Cosine vs MMR на тестовых сценариях.
    """
    scenarios = [
        {
            "name": "Ретро / Синтвейв",
            "seed_topic": "Ретро Вейв",
            "relevant_topics": {"Ретро Вейв", "Киберпанк Неон"},
        },
        {
            "name": "Котики и Юмор",
            "seed_topic": "Котики и Мемы",
            "relevant_topics": {"Котики и Мемы"},
        },
        {
            "name": "Ботаника и Растения",
            "seed_topic": "Ботаника и Растения",
            "relevant_topics": {"Ботаника и Растения"},
        },
    ]

    results: List[Dict[str, Any]] = []

    for sc in scenarios:
        seed_ids = [
            aid for aid, meta in engine.asset_meta.items()
            if meta.get("topic_name") == sc["seed_topic"]
        ][:2]

        if not seed_ids:
            continue

        profile_vec, _ = engine.build_taste_profile(liked_ids=seed_ids)

        # 1. Pure Cosine (lambda=1.0, без ограничений по темам)
        pure_recs = engine.recommend(
            profile_vector=profile_vec.tolist(),
            liked_ids=seed_ids,
            count=k,
            max_per_topic=k,
            min_unique_topics=1,
            lambda_param=1.0,
        )
        pure_ids = [aid for aid, _ in pure_recs]
        pure_ils = compute_intra_list_similarity(engine, pure_ids)
        pure_unique = len({engine._get_topic(aid) for aid in pure_ids})
        pure_entropy = compute_topic_entropy(engine, pure_ids)
        pure_rel = compute_topic_relevance(engine, pure_ids, sc["relevant_topics"])

        # 2. MMR (lambda=0.7, max_per_topic=3, min_unique_topics=4)
        mmr_recs = engine.recommend(
            profile_vector=profile_vec.tolist(),
            liked_ids=seed_ids,
            count=k,
            max_per_topic=3,
            min_unique_topics=4,
            lambda_param=0.7,
        )
        mmr_ids = [aid for aid, _ in mmr_recs]
        mmr_ils = compute_intra_list_similarity(engine, mmr_ids)
        mmr_unique = len({engine._get_topic(aid) for aid in mmr_ids})
        mmr_entropy = compute_topic_entropy(engine, mmr_ids)
        mmr_rel = compute_topic_relevance(engine, mmr_ids, sc["relevant_topics"])

        results.append(
            {
                "scenario": sc["name"],
                "pure": {
                    "ils": pure_ils,
                    "unique_topics": pure_unique,
                    "entropy": pure_entropy,
                    "relevance": pure_rel,
                },
                "mmr": {
                    "ils": mmr_ils,
                    "unique_topics": mmr_unique,
                    "entropy": mmr_entropy,
                    "relevance": mmr_rel,
                },
            }
        )

    # 3. Cold Start сценарий (0 лайков)
    cold_recs = engine.recommend(
        profile_vector=None,
        liked_ids=[],
        count=k,
        max_per_topic=3,
        min_unique_topics=4,
    )
    cold_ids = [aid for aid, _ in cold_recs]
    cold_ils = compute_intra_list_similarity(engine, cold_ids)
    cold_unique = len({engine._get_topic(aid) for aid in cold_ids})
    cold_entropy = compute_topic_entropy(engine, cold_ids)

    return {
        "k": k,
        "scenarios": results,
        "cold_start": {
            "ils": cold_ils,
            "unique_topics": cold_unique,
            "entropy": cold_entropy,
        },
    }


def print_report(eval_data: Dict[str, Any]) -> None:
    """Выводит форматированный отчет в консоль."""
    k = eval_data["k"]
    print("\n" + "=" * 80)
    print(f"  ОЦЕНКА РЕКОМЕНДАТЕЛЬНОЙ СИСТЕМЫ (K={k})")
    print("=" * 80)

    print(f"\n{'Сценарий':<22} | {'Метрика':<18} | {'Pure Cosine':<14} | {'MMR (λ=0.7)':<14} | {'Дельта':<10}")
    print("-" * 86)

    for sc in eval_data["scenarios"]:
        name = sc["scenario"]
        p = sc["pure"]
        m = sc["mmr"]

        # ILS (чем ниже, тем разнообразнее)
        ils_delta = m["ils"] - p["ils"]
        print(f"{name:<22} | {'ILS (сходство)':<18} | {p['ils']:<14.4f} | {m['ils']:<14.4f} | {ils_delta:<+10.4f}")

        # Unique Topics
        ut_delta = m["unique_topics"] - p["pure_topics"] if "pure_topics" in p else m["unique_topics"] - p["unique_topics"]
        print(f"{'':<22} | {'Уникальных тем':<18} | {p['unique_topics']:<14} | {m['unique_topics']:<14} | {ut_delta:<+10}")

        # Энтропия
        ent_delta = m["entropy"] - p["entropy"]
        print(f"{'':<22} | {'Энтропия тем (H)':<18} | {p['entropy']:<14.3f} | {m['entropy']:<14.3f} | {ent_delta:<+10.3f}")

        # Релевантность
        rel_delta = m["relevance"] - p["relevance"]
        print(f"{'':<22} | {'Precision@K':<18} | {p['relevance']:<14.2%} | {m['relevance']:<14.2%} | {rel_delta:<+10.2%}")
        print("-" * 86)

    cs = eval_data["cold_start"]
    print("\nХолодный старт (0 лайков):")
    print(f"  Уникальных тем в топ-{k}: {cs['unique_topics']}")
    print(f"  ILS (попарное сходство): {cs['ils']:.4f}")
    print(f"  Энтропия тем (H):        {cs['entropy']:.3f}")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Оценка рекомендаций MMR vs Pure Cosine (S3)")
    parser.add_argument("--db", type=str, default=None, help="Путь к harvester.db")
    parser.add_argument("--storage-root", type=str, default=None, help="Корень хранилища")
    parser.add_argument("--app-db", type=str, default=None, help="Путь к app.db")
    args = parser.parse_args()

    h_path = Path(args.db).resolve() if args.db else config.HARVESTER_DB_PATH
    a_path = Path(args.app_db).resolve() if args.app_db else config.APP_DB_PATH
    s_root = Path(args.storage_root).resolve() if args.storage_root else config.STORAGE_ROOT

    # Проверяем наличие валидных баз данных с макетами
    need_demo = not (h_path.exists() and a_path.exists())
    if not need_demo:
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{h_path.as_posix()}?mode=ro", uri=True)
            cnt = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            conn.close()
            if cnt == 0:
                need_demo = True
        except Exception:
            need_demo = True

    if need_demo:
        print("[!] Локальная база harvester/app.db пуста или не найдена. Создаём изолированный демо-каталог для оценки...")
        tmp_dir = Path(tempfile.mkdtemp(prefix="eval_demo_"))
        h_path, s_root = create_demo_catalog(tmp_dir)
        a_path = tmp_dir / "app.db"

        # Синхронизация и фичи
        sync_catalog(harvester_db_path=h_path, app_db_path=a_path, storage_root=s_root)
        build_features(harvester_db_path=h_path, app_db_path=a_path)

    engine = RecommenderEngine()
    engine.load(app_db_path=a_path, harvester_db_path=h_path, force=True)

    if not engine.is_loaded:
        print("[!] Не удалось загрузить движок рекомендаций. Проверьте базы данных.")
        sys.exit(1)

    eval_data = evaluate_engine(engine, k=10)
    print_report(eval_data)


if __name__ == "__main__":
    main()
