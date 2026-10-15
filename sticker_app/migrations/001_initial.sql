-- Migration 001: Initial schema for sticker_app (app.db)
-- Таблицы метаданных макетов и аудита лицензий

CREATE TABLE IF NOT EXISTS asset_meta (
    asset_id TEXT PRIMARY KEY,
    license TEXT DEFAULT 'unknown' CHECK(license IN ('own','licensed','cc0','unknown')),
    usable_in_service INTEGER NOT NULL DEFAULT 1,
    takedown INTEGER NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    palette_json TEXT NOT NULL DEFAULT '[]',
    aspect REAL NOT NULL DEFAULT 1.0,
    alpha_coverage REAL NOT NULL DEFAULT 0.0,
    is_square_ok INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_asset_meta_usable ON asset_meta(usable_in_service);
CREATE INDEX IF NOT EXISTS idx_asset_meta_takedown ON asset_meta(takedown);

CREATE TABLE IF NOT EXISTS license_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL,
    old_license TEXT NOT NULL,
    new_license TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    reason TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_license_audit_asset ON license_audit(asset_id);
