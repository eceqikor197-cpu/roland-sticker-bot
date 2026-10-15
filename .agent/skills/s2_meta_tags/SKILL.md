---
name: s2_meta_tags
description: "Этап S2: Собственная база app.db, миграции, обогащение метаданных (теги, палитры цветов, aspect, alpha_coverage), CLI управления лицензиями и takedown."
---

# Этап S2: Метаданные, теги и лицензионный фильтр (app.db)

## Контекст и правила
- Читай `AGENTS.md`, `docs/DATA_CONTRACT.md` и `docs/PROGRESS.md`.
- `harvester.db` открывать ТОЛЬКО в режиме read-only (`mode=ro`).
- Сервис пишет исключительно в свою собственную базу `app.db`.
- Работа ведётся в ветке `feature/s2`.

## Задачи этапа
1. **База данных сервиса (`sticker_app/db.py`):**
   - SQLite `app.db` в режиме WAL (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`).
   - Простая система нумерованных SQL-миграций в `sticker_app/migrations/` (например, `001_initial.sql`).
   - Таблица `asset_meta`:
     ```sql
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
     ```
   - Таблица аудита лицензий `license_audit`:
     ```sql
     CREATE TABLE IF NOT EXISTS license_audit (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         asset_id TEXT NOT NULL,
         old_license TEXT NOT NULL,
         new_license TEXT NOT NULL,
         changed_by TEXT NOT NULL,
         reason TEXT DEFAULT '',
         created_at REAL NOT NULL
     );
     ```

2. **Команда синхронизации и обогащения (`python -m sticker_app.sync_catalog`):**
   - Идемпотентная команда с поддержкой флагов `--dry-run` и `--limit`.
   - Читает макеты из `harvester.db`. Для каждого макета:
     - **Теги:** Извлечение и нормализация из `category_lvl1`, `category_lvl2`, `topic_name`, `folder_slug`, `title`, поисковых запросов темы `search_queries_json`. Очистка технических префиксов (`03_`, `01_`), разбиение на слова (RU/EN), удаление стоп-слов, нижний регистр, дедупликация, максимум 12 качественных тегов на макет.
     - **Геометрия растра:** `aspect = width_px / height_px`, `alpha_coverage` (доля непрозрачных пикселей на миниатюре 256px).
     - **Палитра:** 5 доминирующих HEX-цветов (квантизация Pillow `image.quantize(colors=5)` или k-means без лишних зависимостей).
     - **Сохранение лицензии:** Если лицензия уже была вручную задана в `asset_meta`, не перезаписывать её дефолтом!
     - **usable_in_service:** Расчёт по правилу:
       `usable_in_service = 1`, если `takedown = 0` И (`LICENSE_FILTER == False` ИЛИ `license IN ('own', 'licensed', 'cc0')`). При `takedown = 1` всегда `0`.

3. **CLI разметки лицензий (`python -m sticker_app.set_license`):**
   - Аргументы: `--asset ID --license <own|licensed|cc0|unknown> [--reason REASON]`
   - Пакетная разметка по теме: `--topic SLUG --license <own|licensed|cc0|unknown>`
   - Фиксация каждого изменения в `license_audit` с пересчётом `usable_in_service`.

4. **Интеграция с API каталога:**
   - Обновить `catalog_reader.py` с JOIN таблицы `asset_meta` из `app.db` (через SQLite `ATTACH DATABASE` или запрос по ID).
   - Публичные эндпоинты отдают только макеты с `usable_in_service = 1`.
   - В ответ `GET /api/v1/stickers` и `GET /api/v1/stickers/{asset_id}` добавляются `tags`, `palette`, `aspect`, `alpha_coverage`.
   - Добавить эндпоинт `GET /api/v1/tags`: список популярных тегов с частотой использования.

5. **Тесты (`tests/app/test_sync_meta.py`):**
   - Идемпотентность запуска `sync_catalog` (повторный запуск не ломает данные).
   - Ручные лицензии не затираются при повторном `sync`.
   - Проверка лицензионного фильтра: при `LICENSE_FILTER=on` макеты `unknown` не попадают в выдачу, при `off` попадают; макеты с `takedown=1` не попадают НИКОГДА.
   - Теги очищены от технических символов и стоп-слов.

## Критерий готовности
- На демо-каталоге `python -m sticker_app.sync_catalog` успешно отрабатывает.
- В отчёте представлены 10 примеров сгенерированных тегов для разных тем.
- Тесты лицензий и takedown зелёные.
- `python -m pytest -q` и `python -m ruff check .` зелёные.
- Результаты зафиксированы в ветке `feature/s2`, обновлён `docs/PROGRESS.md`.
