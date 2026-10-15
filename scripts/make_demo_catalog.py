#!/usr/bin/env python3
"""
Генератор синтетического демо-каталога для тестов и локальной разработки.
Создаёт базу harvester.db (по схеме harvester/database.py) и набор из ~40
стикеров (прозрачные PNG с геометрическими фигурами и текстом, EPS с контуром).
Все стикеры имеют лицензию 'own'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw

BASE_DIR = Path(__file__).resolve().parent.parent


DEMO_TOPICS = [
    {
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Эстетика_арт_и_дизайн",
        "topic_name": "Ретро Вейв",
        "folder_slug": "Retro_Wave",
        "search_queries": ["retro wave sticker", "synthwave 80s sunset", "neon grid palm", "vaporwave aesthetic"],
        "stickers": [
            ("Неоновый Закат", "circle", (255, 45, 120, 255), 70.0, 70.0, 600, 600),
            ("Пальма 80-х", "rect", (0, 245, 212, 255), 50.0, 80.0, 500, 800),
            ("Синтвейв Сетка", "triangle", (155, 93, 229, 255), 75.0, 65.0, 750, 650),
            ("Кассета VHS", "badge", (254, 228, 64, 255), 85.0, 55.0, 850, 550),
            ("Аркадный Автомат", "rect", (241, 91, 181, 255), 55.0, 90.0, 550, 900),
            ("Неоновое Солнце", "star", (255, 107, 107, 255), 65.0, 65.0, 650, 650),
            ("Делориан Скетч", "badge", (78, 205, 196, 255), 90.0, 50.0, 900, 500),
            ("Вейв Значок", "circle", (255, 190, 11, 255), 60.0, 60.0, 600, 600),
        ],
    },
    {
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Эстетика_арт_и_дизайн",
        "topic_name": "Киберпанк Неон",
        "folder_slug": "Cyberpunk_Neon",
        "search_queries": ["cyberpunk neon sticker", "sci-fi skull visor", "glitch art sticker", "cyber city samurai"],
        "stickers": [
            ("Кибер Череп", "star", (0, 255, 204, 255), 70.0, 80.0, 700, 800),
            ("Неоновый Визор", "badge", (255, 0, 110, 255), 95.0, 45.0, 950, 450),
            ("Глитч Сердце", "heart", (251, 86, 7, 255), 60.0, 60.0, 600, 600),
            ("Имплант Био", "circle", (131, 56, 236, 255), 65.0, 65.0, 650, 650),
            ("Самурай Маска", "triangle", (58, 134, 255, 255), 75.0, 85.0, 750, 850),
            ("Кибер Процессор", "rect", (0, 255, 150, 255), 60.0, 60.0, 600, 600),
            ("Голограмма Ромб", "diamond", (255, 214, 10, 255), 70.0, 70.0, 700, 700),
            ("Киберпанк Лейбл", "badge", (255, 77, 109, 255), 80.0, 40.0, 800, 400),
        ],
    },
    {
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Мемы_и_юмор",
        "topic_name": "Котики и Мемы",
        "folder_slug": "Cat_Memes",
        "search_queries": ["cat memes stickers", "cute kitten funny", "chonky cat sticker", "cat loaf sticker"],
        "stickers": [
            ("Кот Батон", "badge", (244, 162, 97, 255), 85.0, 55.0, 850, 550),
            ("Кот в Коробке", "rect", (231, 111, 81, 255), 70.0, 70.0, 700, 700),
            ("Мяу Звезда", "star", (233, 196, 106, 255), 65.0, 65.0, 650, 650),
            ("Кусь Сердечко", "heart", (224, 122, 95, 255), 60.0, 60.0, 600, 600),
            ("Кот Детектив", "circle", (129, 178, 154, 255), 65.0, 75.0, 650, 750),
            ("Чонки Кот", "circle", (242, 204, 143, 255), 80.0, 80.0, 800, 800),
            ("Лапка Удачи", "circle", (255, 183, 178, 255), 55.0, 55.0, 550, 550),
            ("Кот Космонавт", "rect", (181, 234, 215, 255), 60.0, 85.0, 600, 850),
        ],
    },
    {
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Игры_и_Гейминг",
        "topic_name": "Пиксель Арт",
        "folder_slug": "Pixel_Art",
        "search_queries": ["pixel art 16bit sticker", "retro game sword potion", "arcade pixel sticker", "game controller pixel"],
        "stickers": [
            ("Пиксельный Меч", "rect", (100, 149, 237, 255), 45.0, 95.0, 450, 950),
            ("Зелье Жизни", "heart", (220, 20, 60, 255), 55.0, 70.0, 550, 700),
            ("Геймпад 16-бит", "badge", (105, 105, 105, 255), 90.0, 55.0, 900, 550),
            ("Пиксельный Череп", "rect", (211, 211, 211, 255), 60.0, 60.0, 600, 600),
            ("Монетка Бонус", "circle", (255, 215, 0, 255), 55.0, 55.0, 550, 550),
            ("Сундук Лута", "rect", (160, 82, 45, 255), 75.0, 65.0, 750, 650),
            ("Пиксельный Кристалл", "diamond", (0, 206, 209, 255), 50.0, 80.0, 500, 800),
            ("1-UP Гриб", "circle", (50, 205, 50, 255), 65.0, 65.0, 650, 650),
        ],
    },
    {
        "category_lvl1": "03_Одиночные_Стикеры",
        "category_lvl2": "Природа_и_Животные",
        "topic_name": "Ботаника и Растения",
        "folder_slug": "Botanical_Plants",
        "search_queries": ["botanical plants stickers", "monstera leaf watercolor", "succulents sticker green", "cactus flora sticker"],
        "stickers": [
            ("Лист Монстеры", "circle", (46, 117, 89, 255), 75.0, 80.0, 750, 800),
            ("Суккулент Роза", "star", (92, 140, 117, 255), 65.0, 65.0, 650, 650),
            ("Кактус в Горшке", "rect", (88, 129, 87, 255), 50.0, 85.0, 500, 850),
            ("Папоротник Ветка", "diamond", (52, 78, 65, 255), 55.0, 95.0, 550, 950),
            ("Эвкалипт Букет", "heart", (163, 177, 138, 255), 60.0, 80.0, 600, 800),
            ("Пальмовый Лист", "triangle", (74, 124, 89, 255), 70.0, 85.0, 700, 850),
            ("Ботанический Ромб", "diamond", (40, 54, 24, 255), 65.0, 65.0, 650, 650),
            ("Цветок Лотоса", "star", (218, 112, 214, 255), 70.0, 70.0, 700, 700),
        ],
    },
]


def _init_harvester_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS harvester_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_lvl1 TEXT NOT NULL,
                category_lvl2 TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                folder_slug TEXT NOT NULL,
                search_queries_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 10,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                harvest_cycles INTEGER NOT NULL DEFAULT 0,
                items_count INTEGER NOT NULL DEFAULT 0,
                packs_count INTEGER NOT NULL DEFAULT 0,
                last_harvested_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                UNIQUE(category_lvl1, category_lvl2, folder_slug)
            );

            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS phashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phash_hex TEXT NOT NULL,
                phash_int INTEGER NOT NULL,
                seg0 INTEGER NOT NULL DEFAULT 0,
                seg1 INTEGER NOT NULL DEFAULT 0,
                seg2 INTEGER NOT NULL DEFAULT 0,
                seg3 INTEGER NOT NULL DEFAULT 0,
                asset_id TEXT DEFAULT '',
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_phashes_hex ON phashes(phash_hex);
            CREATE INDEX IF NOT EXISTS idx_phashes_seg0 ON phashes(seg0);
            CREATE INDEX IF NOT EXISTS idx_phashes_seg1 ON phashes(seg1);
            CREATE INDEX IF NOT EXISTS idx_phashes_seg2 ON phashes(seg2);
            CREATE INDEX IF NOT EXISTS idx_phashes_seg3 ON phashes(seg3);

            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                category_lvl1 TEXT NOT NULL,
                category_lvl2 TEXT NOT NULL,
                folder_slug TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                title TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                license TEXT NOT NULL DEFAULT 'unknown',
                uploaded_by TEXT NOT NULL DEFAULT '',
                phash_hex TEXT NOT NULL,
                width_px INTEGER NOT NULL DEFAULT 0,
                height_px INTEGER NOT NULL DEFAULT 0,
                width_mm REAL NOT NULL DEFAULT 100.0,
                height_mm REAL NOT NULL DEFAULT 100.0,
                offset_mm REAL NOT NULL DEFAULT 2.5,
                perf_cut_mode TEXT NOT NULL DEFAULT 'none',
                num_contours INTEGER NOT NULL DEFAULT 1,
                eps_size_mb REAL NOT NULL DEFAULT 0.0,
                rel_folder_path TEXT NOT NULL,
                eps_filename TEXT NOT NULL,
                png_filename TEXT NOT NULL,
                local_eps_path TEXT NOT NULL,
                local_png_path TEXT NOT NULL,
                local_preview_path TEXT NOT NULL,
                gdrive_folder_path TEXT NOT NULL,
                synced_to_gdrive INTEGER NOT NULL DEFAULT 0,
                in_hot_folder INTEGER NOT NULL DEFAULT 0,
                parent_asset_id TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assets_cat ON assets(category_lvl1, category_lvl2, folder_slug);
            CREATE INDEX IF NOT EXISTS idx_assets_synced ON assets(synced_to_gdrive);
            CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _draw_synthetic_sticker(
    shape: str,
    color: Tuple[int, int, int, int],
    width_px: int,
    height_px: int,
    title: str,
) -> Image.Image:
    """Генерирует PNG-картинку с прозрачным фоном и белым стикерным контуром (подложкой)."""
    img = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = int(min(width_px, height_px) * 0.08)
    inner_box = [margin, margin, width_px - margin, height_px - margin]
    cx, cy = width_px // 2, height_px // 2
    r_x, r_y = (width_px - 2 * margin) // 2, (height_px - 2 * margin) // 2

    # Белая обводка (стикерный кант)
    white = (255, 255, 255, 255)
    border_w = max(4, int(min(width_px, height_px) * 0.04))

    if shape == "circle":
        draw.ellipse(inner_box, fill=color, outline=white, width=border_w)
        # Внутренний акцент
        draw.ellipse(
            [cx - r_x // 2, cy - r_y // 2, cx + r_x // 2, cy + r_y // 2],
            outline=(255, 255, 255, 180),
            width=max(2, border_w // 2),
        )
    elif shape == "star":
        pts = []
        n_points = 5
        for i in range(2 * n_points):
            angle = i * math.pi / n_points - math.pi / 2
            r = r_x if i % 2 == 0 else r_x * 0.45
            pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        draw.polygon(pts, fill=color, outline=white)
    elif shape == "heart":
        # Упрощённый контур сердца через многоугольник / кривые
        pts = [
            (cx, cy + r_y * 0.9),
            (cx - r_x * 0.9, cy),
            (cx - r_x * 0.7, cy - r_y * 0.7),
            (cx - r_x * 0.2, cy - r_y * 0.8),
            (cx, cy - r_y * 0.3),
            (cx + r_x * 0.2, cy - r_y * 0.8),
            (cx + r_x * 0.7, cy - r_y * 0.7),
            (cx + r_x * 0.9, cy),
        ]
        draw.polygon(pts, fill=color, outline=white)
    elif shape == "diamond":
        pts = [(cx, margin), (width_px - margin, cy), (cx, height_px - margin), (margin, cy)]
        draw.polygon(pts, fill=color, outline=white)
    elif shape == "triangle":
        pts = [(cx, margin), (width_px - margin, height_px - margin), (margin, height_px - margin)]
        draw.polygon(pts, fill=color, outline=white)
    elif shape == "badge":
        corner = int(min(width_px, height_px) * 0.2)
        draw.rounded_rectangle(inner_box, radius=corner, fill=color, outline=white, width=border_w)
    else:  # rect
        corner = max(8, int(min(width_px, height_px) * 0.1))
        draw.rounded_rectangle(inner_box, radius=corner, fill=color, outline=white, width=border_w)

    # Простая текстовая подпись по центру
    try:
        # Рисуем короткую метку
        label = title[:12]
        draw.text((cx - len(label) * 4, cy - 6), label, fill=(255, 255, 255, 230))
    except Exception:
        pass

    return img


def _generate_dummy_eps(eps_path: Path, width_mm: float, height_mm: float) -> None:
    """Генерирует минимальный корректный EPS с маркером контура CutContour."""
    mm_to_pt = 72.0 / 25.4
    w_pt = round(width_mm * mm_to_pt, 2)
    h_pt = round(height_mm * mm_to_pt, 2)

    content = f"""%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Roland Demo Generator
%%Title: {eps_path.name}
%%BoundingBox: 0 0 {int(math.ceil(w_pt))} {int(math.ceil(h_pt))}
%%HiResBoundingBox: 0.000 0.000 {w_pt:.3f} {h_pt:.3f}
%%LanguageLevel: 2
%%Pages: 1
%%EndComments
%%BeginProlog
%%EndProlog
%%Page: 1 1
save
% --- Plotter CutContour Spot Color Vector Layer
/CutContour findcmykcustomcolor
1.0 setcustomcolor
0.75 setlinewidth
newpath
0.0 0.0 moveto
{w_pt:.2f} 0.0 lineto
{w_pt:.2f} {h_pt:.2f} lineto
0.0 {h_pt:.2f} lineto
closepath
stroke
restore
showpage
%%EOF
"""
    eps_path.write_text(content, encoding="latin1")


def create_demo_catalog(target_dir: Path) -> Tuple[Path, Path]:
    """
    Создаёт полный демо-каталог в указанной директории.
    Возвращает (db_path, storage_root).
    """
    target_dir = target_dir.resolve()
    db_path = target_dir / "harvester.db"
    storage_root = target_dir / "staging"
    previews_dir = target_dir / "previews"

    storage_root.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    _init_harvester_db(db_path)
    conn = sqlite3.connect(str(db_path))

    now = time.time()
    try:
        for t_idx, topic_data in enumerate(DEMO_TOPICS, 1):
            cat1 = topic_data["category_lvl1"]
            cat2 = topic_data["category_lvl2"]
            topic_name = topic_data["topic_name"]
            folder_slug = topic_data["folder_slug"]
            queries_json = json.dumps(topic_data["search_queries"], ensure_ascii=False)

            # 1. Вставка темы
            conn.execute(
                """
                INSERT OR REPLACE INTO topics (
                    id, category_lvl1, category_lvl2, topic_name, folder_slug,
                    search_queries_json, priority, is_enabled, harvest_cycles,
                    items_count, packs_count, last_harvested_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 10, 1, 1, ?, 0, ?, ?)
                """,
                (
                    t_idx,
                    cat1,
                    cat2,
                    topic_name,
                    folder_slug,
                    queries_json,
                    len(topic_data["stickers"]),
                    now,
                    now,
                ),
            )

            # 2. Создание стикеров для темы
            rel_folder = f"{cat1}/{cat2}/{folder_slug}/Одиночные_Стикеры"
            topic_folder = storage_root / rel_folder
            topic_folder.mkdir(parents=True, exist_ok=True)

            for s_idx, (st_title, shape, color, w_mm, h_mm, w_px, h_px) in enumerate(
                topic_data["stickers"], 1
            ):
                asset_id = f"demo_{t_idx:02d}_{s_idx:02d}_{uuid.uuid4().hex[:6]}"
                file_stem = f"{folder_slug}_Sticker_{asset_id[:8]}"
                png_name = f"{file_stem}.png"
                eps_name = f"{file_stem}.eps"
                preview_jpg_name = f"{asset_id}.jpg"

                png_path = topic_folder / png_name
                eps_path = topic_folder / eps_name
                preview_path = previews_dir / preview_jpg_name

                # Рисуем PNG
                img = _draw_synthetic_sticker(shape, color, w_px, h_px, st_title)
                img.save(png_path, format="PNG", optimize=True)

                # Создаём компактный JPEG превью (256px)
                thumb = img.convert("RGB")
                thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)
                thumb.save(preview_path, format="JPEG", quality=85)

                # Создаём EPS
                _generate_dummy_eps(eps_path, w_mm, h_mm)

                # Вычисляем детерминированный phash_hex
                raw_bytes = png_path.read_bytes()
                h_digest = hashlib.sha256(raw_bytes).hexdigest()
                phash_hex = h_digest[:16]
                p_int = int(phash_hex, 16) & ((1 << 64) - 1)
                if p_int >= (1 << 63):
                    p_int -= 1 << 64

                # Запись в phashes
                conn.execute(
                    """
                    INSERT INTO phashes (phash_hex, phash_int, seg0, seg1, seg2, seg3, asset_id, status, created_at)
                    VALUES (?, ?, 0, 0, 0, 0, ?, 'accepted', ?)
                    """,
                    (phash_hex, p_int, asset_id, now),
                )

                # Запись в assets
                eps_size_mb = round(eps_path.stat().st_size / (1024 * 1024), 3)
                conn.execute(
                    """
                    INSERT INTO assets (
                        asset_id, topic_id, category_lvl1, category_lvl2, folder_slug,
                        topic_name, asset_type, title, source_name, source_url,
                        license, uploaded_by, phash_hex, width_px, height_px,
                        width_mm, height_mm, offset_mm, perf_cut_mode, num_contours,
                        eps_size_mb, rel_folder_path, eps_filename, png_filename,
                        local_eps_path, local_png_path, local_preview_path,
                        gdrive_folder_path, synced_to_gdrive, in_hot_folder,
                        parent_asset_id, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, 'single_sticker', ?, 'synthetic_generator', 'https://demo.local/sticker',
                        'own', 'demo_generator', ?, ?, ?,
                        ?, ?, 2.5, 'die_cut', 1,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        '', 0, 0,
                        '', ?
                    )
                    """,
                    (
                        asset_id,
                        t_idx,
                        cat1,
                        cat2,
                        folder_slug,
                        topic_name,
                        st_title,
                        phash_hex,
                        w_px,
                        h_px,
                        w_mm,
                        h_mm,
                        eps_size_mb,
                        rel_folder,
                        eps_name,
                        png_name,
                        str(eps_path),
                        str(png_path),
                        str(preview_path),
                        now - (t_idx * 100 + s_idx),
                    ),
                )

        conn.commit()
    finally:
        conn.close()

    return db_path, storage_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание синтетического демо-каталога для тестов.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / "bot_workspace" / "demo_catalog",
        help="Директория для создания демо-каталога",
    )
    args = parser.parse_args()

    print(f"[*] Генерация демо-каталога в: {args.output_dir}")
    db_path, storage_root = create_demo_catalog(args.output_dir)
    print(f"[+] База данных: {db_path}")
    print(f"[+] Хранилище: {storage_root}")
    print("[+] Каталог успешно создан (~40 стикеров, 5 тем, license=own).")


if __name__ == "__main__":
    main()
