"""
Общие фикстуры pytest для пакета tests/app/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import pytest

from scripts.make_demo_catalog import create_demo_catalog
import sticker_app.config as app_config


@dataclass
class DemoCatalogContext:
    catalog_dir: Path
    db_path: Path
    storage_root: Path
    previews_dir: Path


@pytest.fixture
def demo_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[DemoCatalogContext, None, None]:
    """Создаёт изолированный синтетический каталог для тестов."""
    cat_dir = tmp_path / "test_demo_catalog"
    db_path, storage_root = create_demo_catalog(cat_dir)
    previews_dir = cat_dir / "previews"

    # Подменяем пути в sticker_app.config
    monkeypatch.setattr(app_config, "HARVESTER_DB_PATH", db_path)
    monkeypatch.setattr(app_config, "STORAGE_ROOT", storage_root)
    monkeypatch.setattr(app_config, "APP_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(app_config, "CACHE_DIR", tmp_path / "cache")

    ctx = DemoCatalogContext(
        catalog_dir=cat_dir,
        db_path=db_path,
        storage_root=storage_root,
        previews_dir=previews_dir,
    )
    yield ctx
