-- Migration 002: Recommender feature vectors and vocabulary
-- Хранение векторов признаков макетов (Уровень A / B) и словаря признаков

CREATE TABLE IF NOT EXISTS asset_features (
    asset_id TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_vocabularies (
    model_version TEXT PRIMARY KEY,
    vocabulary_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
