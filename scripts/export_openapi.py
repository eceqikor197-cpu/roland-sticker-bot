#!/usr/bin/env python3
"""
Скрипт генерации OpenAPI контракта для фронтенда и документации.
Экспортирует схему FastAPI приложения в docs/openapi.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sticker_app.main import app


def export_openapi(target_path: Path) -> None:
    """Генерирует и записывает OpenAPI схему в указанный файл."""
    schema = app.openapi()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[+] OpenAPI схема успешно сохранена в: {target_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Экспорт OpenAPI схемы FastAPI.")
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "docs" / "openapi.json",
        help="Путь для сохранения файла openapi.json",
    )
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
