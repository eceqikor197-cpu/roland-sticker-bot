---
name: s0_recon
description: "Этап S0: Разведка схемы harvester.db, контракт данных, контракт координат, инспекция базы и генератор синтетического каталога."
---

# Этап S0: Разведка, контракт данных и тестовая БД

## Контекст и правила
- Читай `AGENTS.md` (особенно раздел «Правила для веб-сервиса стикербомбинга»).
- Существующие файлы НЕ менять (`telegram_bot.py`, `jpeg2eps.py`, `harvester/*` только на чтение/импорт).
- Базу `harvester.db` открывать ТОЛЬКО в режиме read-only (`sqlite3` URI `mode=ro`).
- Работа ведётся в ветке `feature/s0`.

## Задачи этапа
1. **Контракт данных:**
   - Прочитай `harvester/database.py`, `harvester/processor/eps_pipeline.py` и `harvester/config.py`.
   - Составь `docs/DATA_CONTRACT.md`: структура `harvester.db`, поля `assets`, `topics`, `phashes`, какие поля нужны сервису, какие игнорируются, какие пути абсолютные, как соотносятся EPS, PNG и превью, как работает `parent_asset_id`.
   - Опиши безопасное разрешение путей от `STORAGE_ROOT` с защитой от path traversal.

2. **Инспекция каталога (`scripts/inspect_catalog.py`):**
   - Открывает `harvester.db` read-only (путь через аргумент `--db` или env `HARVESTER_DB_PATH`).
   - Считает число `assets` по `asset_type` и `category_lvl1/lvl2`.
   - Проверяет физическое существование файлов PNG/EPS на диске относительно `STORAGE_ROOT`.
   - Считает распределение `width_mm/height_mm` (min, median, mean, max).
   - Считает долю PNG с альфа-каналом прозрачности (на выборке 200).
   - Проверяет число дублей по `phash_hex`.
   - Выводит топ-30 тем по количеству макетов.
   - Выводит отчёт в консоль и сохраняет в `docs/CATALOG_REPORT.md`.

3. **Генератор синтетического демо-каталога (`scripts/make_demo_catalog.py`):**
   - Создаёт изолированную демо-версию `harvester.db` (схема из `init_db`) с ~40 синтетическими стикерами.
   - Картинки PNG с прозрачным фоном и белым кантом (фигуры и текст через Pillow), разные пропорции и размеры в мм.
   - 4–5 тем в категории `03_Одиночные_Стикеры`, все ассеты с `license = 'own'`.
   - Минимальные валидные EPS файлы с маркером `CutContour`.
   - Реальная БД в автотестах не используется; демо-каталог — основа для всех тестов.

4. **Каркас бэкенда и тестов:**
   - `sticker_app/__init__.py`.
   - `sticker_app/config.py` (`STORAGE_ROOT`, `HARVESTER_DB_PATH`, `APP_DB_PATH`, `CACHE_DIR`, `LICENSE_FILTER`).
   - `tests/app/conftest.py` с фикстурой `demo_catalog`.
   - `tests/app/test_demo_catalog.py` с тестами на структуру каталога, защиту `mode=ro` и прозрачность PNG.

5. **Контракты и журнал:**
   - `docs/COORDINATES.md` (миллиметры как единственный источник истины, начало координат, центр стикера, угол поворота, формат placement).
   - `docs/PROGRESS.md` (журнал статуса этапов).

## Критерий готовности
- `docs/DATA_CONTRACT.md` и `docs/COORDINATES.md` составлены и соответствуют коду.
- `make_demo_catalog.py` создаёт каталог, а `inspect_catalog.py` успешно анализирует его.
- `python -m pytest -q` и `python -m ruff check .` зелёные.
- Результаты зафиксированы в коммитах ветки `feature/s0`.
