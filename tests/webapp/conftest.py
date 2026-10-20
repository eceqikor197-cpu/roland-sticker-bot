"""Общие фикстуры тестов мини-приложения."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image


def make_sticker_png(path: Path, w: int = 900, h: int = 400) -> Path:
    """Цветной эллипс на белом фоне: фон вырезается, силуэт гладкий."""
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.ellipse(img, (w // 2, h // 2), (int(w * 0.44), int(h * 0.4)), 0, 0, 360, (200, 30, 60), -1)
    cv2.circle(img, (w // 2, h // 2), int(h * 0.15), (20, 120, 220), -1)
    Image.fromarray(img).save(path)
    return path


@pytest.fixture
def sticker_png(tmp_path: Path) -> Path:
    return make_sticker_png(tmp_path / "sticker.png")


@pytest.fixture
def job_factory(tmp_path: Path):
    """Создаёт задачу в tmp jobs/ и в storage, как это делает бот при загрузке."""
    from bot.jobs import save_json_file
    from storage import save_job

    jobs_dir = tmp_path / "jobs"

    def _make(job_id: str, user_id: int, w: int = 900, h: int = 400, **extra) -> Path:
        job_dir = jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        make_sticker_png(job_dir / "input.png", w, h)
        meta = {
            "job_id": job_id,
            "user_id": user_id,
            "orig_stem": f"sticker_{job_id}",
            "input_filename": "input.png",
            "created_at": time.time(),
            "updated_at": time.time(),
            **extra,
        }
        save_json_file(job_dir / "meta.json", meta)
        save_job(meta)
        return job_dir

    _make.jobs_dir = jobs_dir
    return _make
