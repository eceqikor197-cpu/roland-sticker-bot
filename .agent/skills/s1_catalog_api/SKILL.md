---
name: s1_catalog_api
description: "Этап S1: FastAPI-бэкенд, модуль catalog_reader (read-only), эндпоинты каталога, раздача и кэширование thumb/preview WebP, генерация openapi.json."
---

# Этап S1: Бэкенд: доступ к каталогу и раздача изображений

## Контекст и правила
- Читай `AGENTS.md`, `docs/DATA_CONTRACT.md` и `docs/PROGRESS.md`.
- Существующие файлы НЕ менять (`telegram_bot.py`, `jpeg2eps.py`, `harvester/*`).
- Базу `harvester.db` открывать ТОЛЬКО в режиме read-only (`sqlite3` URI `mode=ro`).
- Разрешённые зависимости: `fastapi`, `uvicorn`, `pydantic`, `pillow` (уже есть в проекте). Больше ничего без согласования.
- Работа ведётся в ветке `feature/s1`.

## Задачи этапа
1. **Модуль чтения каталога (`sticker_app/catalog_reader.py`):**
   - Только чтение `harvester.db` через SQLite URI `mode=ro`.
   - Функции:
     - `list_stickers(filters, page=1, page_size=30) -> StickerListResponse`
     - `get_sticker(asset_id) -> Optional[StickerDetail]`
     - `list_categories() -> List[CategoryInfo]`
     - `list_topics(category_lvl1, category_lvl2) -> List[TopicInfo]`
   - Фильтры: `asset_type` (по умолчанию `single_sticker`), `category_lvl1`, `category_lvl2`, `topic_name` / `folder_slug`, поиск по тексту в `title` / `topic_name`.
   - Ответы строго через Pydantic-модели (`StickerSummary`, `StickerDetail`, `PaginationMeta`).
   - НИКАКИХ путей файловой системы в публичных Pydantic-моделях!

2. **Безопасное сопоставление путей (Path Traversal Protection):**
   - Не использовать `local_*_path` напрямую.
   - Построить путь как `(STORAGE_ROOT / rel_folder_path / png_filename).resolve()`.
   - Проверить `path.is_relative_to(STORAGE_ROOT.resolve())`. Если путь выходит за пределы `STORAGE_ROOT`, генерировать ошибку безопасности.
   - Если файла нет на диске, стикер исключается из публичной выдачи, но логируется `logger.warning`.

3. **HTTP эндпоинты (`sticker_app/main.py`):**
   - `GET /api/v1/health` — проверка работоспособности (`{"status": "ok", "version": "0.1.0"}`).
   - `GET /api/v1/stickers` — пагинация (`page`, `page_size`), фильтры, текстовый поиск.
   - `GET /api/v1/stickers/{asset_id}` — детальная информация о макете (размеры в мм, пропорции aspect ratio, категория, тема).
   - `GET /api/v1/stickers/{asset_id}/image?size=thumb|preview|full` — раздача изображений:
     - `thumb`: до 256 px по большей стороне, формат WebP с альфа-каналом (LANCZOS).
     - `preview`: до 1024 px по большей стороне, формат WebP с альфа-каналом.
     - `full`: исходный PNG файл без пережатия.
     - Кэширование `thumb` и `preview` на диске в `CACHE_DIR`.
     - Заголовки `Cache-Control: public, max-age=86400`, `ETag`, поддержка `If-None-Match` (ответ 304 Not Modified).
   - `GET /api/v1/categories` — дерево доступных категорий и тем.

4. **Лицензионный фильтр:**
   - По умолчанию (`LICENSE_FILTER=off`) отдавать все макеты.
   - При `LICENSE_FILTER=on` учитывать `usable_in_service` (в S1 заложить флаг в запросах к базе).

5. **Безопасность CORS:**
   - Разрешать origins строго из конфигурации `CORS_ORIGINS` (`config.py`), никаких `allow_origins=["*"]`.

6. **Генерация OpenAPI контракта:**
   - Скрипт `scripts/export_openapi.py` или команда экспорта схемы FastAPI в `docs/openapi.json`.

7. **Тесты (`tests/app/test_api_catalog.py`):**
   - Запуск через `TestClient(app)` на фикстуре `demo_catalog`:
     - Проверка health-эндпоинта.
     - Пагинация и фильтрация в `/api/v1/stickers`.
     - Получение отдельного стикера и 404 на несуществующий ID.
     - Раздача `size=thumb`: Content-Type `image/webp`, сохранение альфа-прозрачности, размер не превышает 256px, повторный запрос возвращает закэшированный файл.
     - Защита от path traversal (попытка подложить `../` в базу).
     - Проверка работы `ETag` и `304 Not Modified`.

## Критерий готовности
- `uvicorn sticker_app.main:app` успешно отвечает на запросы с демо-каталогом.
- Сгенерирован актуальный `docs/openapi.json`.
- `python -m pytest -q` и `python -m ruff check .` зелёные.
- Результаты зафиксированы в коммитах ветки `feature/s1`, обновлён `docs/PROGRESS.md`.
