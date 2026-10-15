#!/usr/bin/env python3
"""
Скрипт инспекции каталога harvester.db (режим только для чтения).
Анализирует распределение макетов, категорий, физических размеров,
наличие файлов на диске и альфа-канала.
Формирует сводный отчёт в консоль и в docs/CATALOG_REPORT.md.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Инспекция базы harvester.db в режиме read-only.")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("HARVESTER_DB_PATH", BASE_DIR / "bot_workspace" / "harvester" / "harvester.db")),
        help="Путь к файлу harvester.db",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(os.getenv("STORAGE_ROOT", BASE_DIR / "bot_workspace" / "harvester" / "staging")),
        help="Корневая директория хранилища макетов (STORAGE_ROOT)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=BASE_DIR / "docs" / "CATALOG_REPORT.md",
        help="Путь к файлу markdown-отчёта",
    )
    parser.add_argument(
        "--sample-alpha",
        type=int,
        default=200,
        help="Размер выборки для проверки альфа-канала (по умолчанию 200)",
    )
    return parser.parse_args()


def open_db_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Файл базы данных не найден: {db_path}")
    # Подключение strictly read-only через URI
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def inspect_catalog(
    conn: sqlite3.Connection,
    storage_root: Path,
    sample_alpha_size: int = 200,
) -> Dict[str, Any]:
    # 1. Общее число макетов
    total_assets = conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"]

    # 2. По asset_type
    by_type_rows = conn.execute(
        """
        SELECT asset_type, COUNT(*) AS count
        FROM assets
        GROUP BY asset_type
        ORDER BY count DESC
        """
    ).fetchall()
    by_type = {r["asset_type"]: r["count"] for r in by_type_rows}

    # 3. По category_lvl1 / category_lvl2
    by_cat_rows = conn.execute(
        """
        SELECT category_lvl1, category_lvl2, COUNT(*) AS count
        FROM assets
        GROUP BY category_lvl1, category_lvl2
        ORDER BY category_lvl1 ASC, count DESC
        """
    ).fetchall()
    by_categories = [
        {
            "cat1": r["category_lvl1"],
            "cat2": r["category_lvl2"],
            "count": r["count"],
        }
        for r in by_cat_rows
    ]

    # 4. Проверка существования файлов на диске (PNG/JPG и EPS)
    asset_rows = conn.execute(
        """
        SELECT asset_id, rel_folder_path, png_filename, eps_filename,
               local_png_path, local_eps_path, width_mm, height_mm, phash_hex
        FROM assets
        """
    ).fetchall()

    existing_png_count = 0
    existing_eps_count = 0
    all_existing_png_paths: List[Path] = []
    widths_mm: List[float] = []
    heights_mm: List[float] = []

    for r in asset_rows:
        widths_mm.append(float(r["width_mm"]))
        heights_mm.append(float(r["height_mm"]))

        # Проверка PNG
        png_found = False
        if r["rel_folder_path"] and r["png_filename"]:
            cand = storage_root / r["rel_folder_path"] / r["png_filename"]
            if cand.is_file():
                existing_png_count += 1
                all_existing_png_paths.append(cand)
                png_found = True

        if not png_found and r["local_png_path"]:
            cand_local = Path(r["local_png_path"])
            if cand_local.is_file():
                existing_png_count += 1
                all_existing_png_paths.append(cand_local)

        # Проверка EPS
        eps_found = False
        if r["rel_folder_path"] and r["eps_filename"]:
            cand_eps = storage_root / r["rel_folder_path"] / r["eps_filename"]
            if cand_eps.is_file():
                existing_eps_count += 1
                eps_found = True

        if not eps_found and r["local_eps_path"]:
            cand_eps_local = Path(r["local_eps_path"])
            if cand_eps_local.is_file():
                existing_eps_count += 1

    # 5. Статистика размеров (width_mm / height_mm)
    def compute_stats(values: List[float]) -> Dict[str, float]:
        if not values:
            return {"min": 0.0, "median": 0.0, "max": 0.0, "mean": 0.0}
        s_vals = sorted(values)
        return {
            "min": round(s_vals[0], 2),
            "median": round(statistics.median(s_vals), 2),
            "max": round(s_vals[-1], 2),
            "mean": round(statistics.mean(s_vals), 2),
        }

    width_stats = compute_stats(widths_mm)
    height_stats = compute_stats(heights_mm)

    # 6. Проверка альфа-канала на выборке
    alpha_checked = 0
    alpha_with_transparency = 0
    import random

    sample_paths = (
        random.sample(all_existing_png_paths, min(sample_alpha_size, len(all_existing_png_paths)))
        if all_existing_png_paths
        else []
    )

    for p in sample_paths:
        alpha_checked += 1
        try:
            with Image.open(p) as img:
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    # Проверяем реальное наличие полупрозрачных или прозрачных пикселей
                    alpha_channel = img.convert("RGBA").getchannel("A")
                    min_a, max_a = alpha_channel.getextrema()
                    if min_a < 255:
                        alpha_with_transparency += 1
                elif "transparency" in img.info:
                    alpha_with_transparency += 1
        except Exception:
            pass

    alpha_share = (
        round(alpha_with_transparency / alpha_checked, 4) if alpha_checked > 0 else 0.0
    )

    # 7. Дубликаты по phash
    dup_rows = conn.execute(
        """
        SELECT phash_hex, COUNT(*) AS cnt
        FROM assets
        WHERE phash_hex != ''
        GROUP BY phash_hex
        HAVING cnt > 1
        """
    ).fetchall()
    dup_groups = len(dup_rows)
    dup_total_assets = sum(r["cnt"] for r in dup_rows)

    # 8. Топ-30 тем
    top_topics_rows = conn.execute(
        """
        SELECT topic_name, category_lvl1, category_lvl2, COUNT(*) AS count
        FROM assets
        GROUP BY topic_name, category_lvl1, category_lvl2
        ORDER BY count DESC
        LIMIT 30
        """
    ).fetchall()
    top_topics = [
        {
            "topic_name": r["topic_name"],
            "category_lvl1": r["category_lvl1"],
            "category_lvl2": r["category_lvl2"],
            "count": r["count"],
        }
        for r in top_topics_rows
    ]

    return {
        "total_assets": total_assets,
        "by_type": by_type,
        "by_categories": by_categories,
        "existing_png_count": existing_png_count,
        "existing_eps_count": existing_eps_count,
        "width_stats": width_stats,
        "height_stats": height_stats,
        "alpha_checked": alpha_checked,
        "alpha_with_transparency": alpha_with_transparency,
        "alpha_share": alpha_share,
        "dup_groups": dup_groups,
        "dup_total_assets": dup_total_assets,
        "top_topics": top_topics,
    }


def format_markdown_report(report_data: Dict[str, Any], db_path: Path, storage_root: Path) -> str:
    lines: List[str] = []
    lines.append("# Отчёт инспекции каталога harvester.db")
    lines.append("")
    lines.append(f"- **Путь к БД:** `{db_path}`")
    lines.append(f"- **STORAGE_ROOT:** `{storage_root}`")
    lines.append(f"- **Всего макетов (assets):** {report_data['total_assets']}")
    lines.append("")

    lines.append("## 1. Распределение по типам макетов (asset_type)")
    lines.append("")
    lines.append("| Тип макета | Количество | Доля |")
    lines.append("|---|---|---|")
    total = max(1, report_data["total_assets"])
    if report_data["by_type"]:
        for atype, cnt in report_data["by_type"].items():
            pct = round(cnt / total * 100, 1)
            lines.append(f"| `{atype}` | {cnt} | {pct}% |")
    else:
        lines.append("| *(нет записей)* | 0 | 0% |")
    lines.append("")

    lines.append("## 2. Наличие файлов на диске")
    lines.append("")
    png_cnt = report_data["existing_png_count"]
    eps_cnt = report_data["existing_eps_count"]
    png_pct = round(png_cnt / total * 100, 1) if report_data["total_assets"] else 0.0
    eps_pct = round(eps_cnt / total * 100, 1) if report_data["total_assets"] else 0.0
    lines.append(f"- Существующих файлов изображений (PNG/JPG): **{png_cnt}** ({png_pct}%)")
    lines.append(f"- Существующих файлов EPS для плоттера: **{eps_cnt}** ({eps_pct}%)")
    lines.append("")

    lines.append("## 3. Физические размеры (мм)")
    lines.append("")
    w = report_data["width_stats"]
    h = report_data["height_stats"]
    lines.append("| Параметр | Min (мм) | Median (мм) | Mean (мм) | Max (мм) |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| Ширина (width_mm) | {w['min']} | {w['median']} | {w['mean']} | {w['max']} |")
    lines.append(f"| Высота (height_mm) | {h['min']} | {h['median']} | {h['mean']} | {h['max']} |")
    lines.append("")

    lines.append("## 4. Проверка прозрачности (альфа-канал)")
    lines.append("")
    lines.append(f"- Проверено изображений на диске: **{report_data['alpha_checked']}**")
    lines.append(f"- С прозрачным фоном: **{report_data['alpha_with_transparency']}**")
    alpha_pct = round(report_data["alpha_share"] * 100, 1)
    lines.append(f"- Доля с прозрачностью: **{alpha_pct}%**")
    lines.append("")

    lines.append("## 5. Дедупликация по pHash")
    lines.append("")
    lines.append(f"- Групп дубликатов по идентичному pHash: **{report_data['dup_groups']}**")
    lines.append(f"- Всего макетов в группах дублей: **{report_data['dup_total_assets']}**")
    lines.append("")

    lines.append("## 6. Категории (category_lvl1 / category_lvl2)")
    lines.append("")
    lines.append("| Категория 1 | Категория 2 | Количество |")
    lines.append("|---|---|---|")
    if report_data["by_categories"]:
        for c in report_data["by_categories"]:
            lines.append(f"| {c['cat1']} | {c['cat2']} | {c['count']} |")
    else:
        lines.append("| *(нет записей)* | - | 0 |")
    lines.append("")

    lines.append("## 7. Топ-30 тем по числу макетов")
    lines.append("")
    lines.append("| № | Тема | Категория 1 | Категория 2 | Макетов |")
    lines.append("|---|---|---|---|---|")
    if report_data["top_topics"]:
        for i, t in enumerate(report_data["top_topics"], 1):
            lines.append(f"| {i} | {t['topic_name']} | {t['category_lvl1']} | {t['category_lvl2']} | {t['count']} |")
    else:
        lines.append("| - | *(нет записей)* | - | - | 0 |")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    print(f"[*] Открытие harvester.db (mode=ro): {args.db}")
    print(f"[*] STORAGE_ROOT: {args.storage_root}")

    try:
        conn = open_db_readonly(args.db)
    except Exception as e:
        print(f"[!] Ошибка открытия базы: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        data = inspect_catalog(conn, args.storage_root, sample_alpha_size=args.sample_alpha)
    finally:
        conn.close()

    report_md = format_markdown_report(data, args.db, args.storage_root)

    # Вывод в консоль
    print("\n" + report_md + "\n")

    # Сохранение в файл
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_md, encoding="utf-8")
    print(f"[+] Отчёт сохранён в: {args.report}")


if __name__ == "__main__":
    main()
