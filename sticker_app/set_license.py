"""
CLI управления лицензиями и takedown макетов каталога.
python -m sticker_app.set_license --asset ID --license <own|licensed|cc0|unknown> [--reason REASON]
python -m sticker_app.set_license --topic SLUG --license <own|licensed|cc0|unknown>

Фиксирует изменения в таблице license_audit и пересчитывает usable_in_service в app.db.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sticker_app.config as config
from sticker_app.db import get_app_db, init_db

logger = logging.getLogger("sticker_app.set_license")

ALLOWED_LICENSES = {"own", "licensed", "cc0"}
VALID_LICENSES = {"own", "licensed", "cc0", "unknown"}


def set_license(
    asset_id: Optional[str] = None,
    topic_slug: Optional[str] = None,
    new_license: Optional[str] = None,
    takedown: Optional[int] = None,
    reason: str = "",
    changed_by: str = "cli",
    app_db_path: Optional[Path] = None,
    harvester_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Устанавливает лицензию и/или takedown для отдельного стикера или всей темы.
    """
    if not asset_id and not topic_slug:
        raise ValueError("Необходимо указать либо --asset ID, либо --topic SLUG")

    if new_license is not None:
        new_license = new_license.strip().lower()
        if new_license not in VALID_LICENSES:
            raise ValueError(
                f"Недопустимая лицензия '{new_license}'. Допустимо: {', '.join(sorted(VALID_LICENSES))}"
            )

    if takedown is not None and takedown not in (0, 1):
        raise ValueError("Параметр --takedown должен быть 0 или 1")

    a_path = (app_db_path or config.APP_DB_PATH).resolve()
    h_path = (harvester_db_path or config.HARVESTER_DB_PATH).resolve()

    init_db(a_path)

    # Определяем список целевых asset_id
    target_ids: List[str] = []
    if asset_id:
        target_ids.append(asset_id.strip())
    elif topic_slug:
        if not h_path.exists():
            raise FileNotFoundError(f"harvester.db не найден: {h_path}")
        h_conn = sqlite3.connect(f"file:{h_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = h_conn.execute(
                "SELECT asset_id FROM assets WHERE folder_slug = ?",
                (topic_slug.strip(),),
            ).fetchall()
            target_ids = [str(r[0]) for r in rows]
        finally:
            h_conn.close()

    if not target_ids:
        return {"updated": 0, "target_ids": [], "audited": 0}

    now = time.time()
    audited_count = 0
    updated_count = 0

    conn = get_app_db(a_path)
    try:
        with conn:
            for aid in target_ids:
                row = conn.execute(
                    "SELECT license, takedown, tags_json, palette_json, aspect, alpha_coverage, is_square_ok FROM asset_meta WHERE asset_id = ?",
                    (aid,),
                ).fetchone()

                if row:
                    old_license = str(row["license"] or "unknown")
                    cur_takedown = int(row["takedown"] or 0)
                else:
                    old_license = "unknown"
                    cur_takedown = 0

                effective_license = new_license if new_license is not None else old_license
                effective_takedown = takedown if takedown is not None else cur_takedown

                # Пересчет usable_in_service
                if effective_takedown == 1:
                    new_usable = 0
                elif not config.LICENSE_FILTER:
                    new_usable = 1
                elif effective_license in ALLOWED_LICENSES:
                    new_usable = 1
                else:
                    new_usable = 0

                # Обновляем или вставляем запись в asset_meta
                conn.execute(
                    """
                    INSERT INTO asset_meta (
                        asset_id, license, usable_in_service, takedown,
                        tags_json, palette_json, aspect, alpha_coverage,
                        is_square_ok, updated_at
                    ) VALUES (?, ?, ?, ?, '[]', '[]', 1.0, 0.0, 1, ?)
                    ON CONFLICT(asset_id) DO UPDATE SET
                        license = excluded.license,
                        usable_in_service = excluded.usable_in_service,
                        takedown = excluded.takedown,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        aid,
                        effective_license,
                        new_usable,
                        effective_takedown,
                        now,
                    ),
                )
                updated_count += 1

                # Запись в license_audit при изменении лицензии или takedown
                if effective_license != old_license or (takedown is not None and effective_takedown != cur_takedown):
                    audit_reason = reason
                    if takedown is not None and effective_takedown != cur_takedown:
                        audit_reason = f"takedown={effective_takedown}. {reason}".strip()
                    conn.execute(
                        """
                        INSERT INTO license_audit (
                            asset_id, old_license, new_license, changed_by, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            aid,
                            old_license,
                            effective_license,
                            changed_by,
                            audit_reason,
                            now,
                        ),
                    )
                    audited_count += 1

        return {
            "updated": updated_count,
            "audited": audited_count,
            "target_ids": target_ids,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI разметки лицензий и такдауна макетов")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--asset", type=str, help="ID отдельного макета (asset_id)")
    group.add_argument("--topic", type=str, help="Слаг темы (folder_slug) для пакетного обновления")

    parser.add_argument(
        "--license",
        type=str,
        default=None,
        choices=["own", "licensed", "cc0", "unknown"],
        help="Новый тип лицензии",
    )
    parser.add_argument(
        "--takedown",
        type=int,
        choices=[0, 1],
        default=None,
        help="Статус такдауна: 1 (заблокировать) или 0 (разблокировать)",
    )
    parser.add_argument("--reason", type=str, default="", help="Причина изменения для аудита")
    parser.add_argument("--changed-by", type=str, default="cli", help="Имя оператора/пользователя")
    parser.add_argument("--app-db", type=Path, default=None, help="Путь к app.db")
    parser.add_argument("--harvester-db", type=Path, default=None, help="Путь к harvester.db")

    args = parser.parse_args()

    if args.license is None and args.takedown is None:
        print("Ошибка: необходимо указать либо --license, либо --takedown", file=sys.stderr)
        sys.exit(1)

    res = set_license(
        asset_id=args.asset,
        topic_slug=args.topic,
        new_license=args.license,
        takedown=args.takedown,
        reason=args.reason,
        changed_by=args.changed_by,
        app_db_path=args.app_db,
        harvester_db_path=args.harvester_db,
    )

    target_desc = f"макет '{args.asset}'" if args.asset else f"тема '{args.topic}'"
    print(f"Готово: обновлено {res['updated']} записей для {target_desc}.")
    print(f"Записей в license_audit: {res['audited']}.")


if __name__ == "__main__":
    main()
