#!/usr/bin/env python3
"""
Автотесты конвейера Sticker Harvester 24/7:
- Инициализация БД и загрузка 600+ тем
- Классификация пользовательских тем по 4 категориям
- Работа сплиттера листов-стикерпаков и сборщика авто-паков
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harvester.database import init_db, slugify_folder
from harvester.processor.pack_splitter import detect_and_split_sticker_sheet
from harvester.seeds import build_expanded_Topics, classify_user_topic


def test_seed_topics_count_and_taxonomy() -> None:
    topics = build_expanded_Topics()
    assert len(topics) >= 500, f"Ожидалось >= 500 тем, получено {len(topics)}"
    cats_lvl1 = {t[0] for t in topics}
    assert "01_Стикерпаки" in cats_lvl1
    assert "02_Авто_Наклейки" in cats_lvl1
    assert "03_Одиночные_Стикеры" in cats_lvl1
    assert "04_Баннеры_и_Вывески" in cats_lvl1


def test_user_topic_classifier() -> None:
    c1, c2, _, _ = classify_user_topic("Наклейки на авто Toyota Mark 2 JDM")
    assert c1 == "02_Авто_Наклейки"
    assert "JDM" in c2

    c1_b, _, _, _ = classify_user_topic("Баннер вывеска Шиномонтаж 24")
    assert c1_b == "04_Баннеры_и_Вывески"

    c1_a, c2_a, _, _ = classify_user_topic("Аниме Наруто Акацуки")
    assert c1_a == "01_Стикерпаки"
    assert c2_a == "Аниме_и_Манга"


def test_sticker_sheet_splitter() -> None:
    # Создаём синтетический лист-стикерпак 900x900 с 4 отдельными стикерами
    img = Image.new("RGBA", (900, 900), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    coords = [
        (80, 80, 320, 320),
        (520, 80, 780, 320),
        (80, 520, 320, 780),
        (520, 520, 780, 780),
    ]
    for x0, y0, x1, y1 in coords:
        draw.ellipse([x0, y0, x1, y1], fill=(220, 40, 90, 255), outline=(255, 255, 255, 255), width=8)

    arr = np.array(img, dtype=np.uint8)
    fg_mask = (arr[:, :, 3] > 20).astype(np.uint8) * 255

    is_sheet, items = detect_and_split_sticker_sheet(img, fg_mask, min_item_px=55)
    assert is_sheet is True
    assert len(items) == 4
    for item in items:
        assert max(item.size) >= 550  # Проверка авто-апскейла мелких стикеров


if __name__ == "__main__":
    init_db()
    test_seed_topics_count_and_taxonomy()
    test_user_topic_classifier()
    test_sticker_sheet_splitter()
    print("✅ Все тесты конвейера Sticker Harvester успешно пройдены!")
