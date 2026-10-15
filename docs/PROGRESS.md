# Прогресс разработки веб-сервиса стикербомбинга

## Журнал этапов

### Этап S0: Разведка, контракт данных и тестовая БД
- **Статус:** Завершён (ветка `feature/s0`)
- **Сделано:**
  - Обновлён `AGENTS.md` правилами для веб-сервиса стикербомбинга (Часть 2).
  - Составлен `docs/DATA_CONTRACT.md` со структурой `harvester.db`, полями, правилами путей и защитой от path traversal.
  - Составлен `docs/COORDINATES.md` с контрактом системы координат и физических единиц (мм как единственный источник истины).
  - Написан `scripts/inspect_catalog.py`: строго read-only доступ к `harvester.db`, расчёт распределений, проверка наличия файлов и альфа-канала, вывод отчёта в консоль и в `docs/CATALOG_REPORT.md`.
  - Написан `scripts/make_demo_catalog.py`: генератор синтетической БД `harvester.db` с 40 стикерами (прозрачный PNG с подложкой, EPS с маркером CutContour, превью, 5 тем в категории 03_Одиночные_Стикеры, лицензия own).
  - Создан каркас пакета: `sticker_app/__init__.py`, `sticker_app/config.py` (с параметрами `STORAGE_ROOT`, `HARVESTER_DB_PATH`, `APP_DB_PATH`, `LICENSE_FILTER`).
  - Создан тестовый модуль `tests/app/`: `conftest.py` с фикстурой `demo_catalog` и `test_demo_catalog.py` (4 теста).
  - Сгенерирован начальный отчёт инспекции `docs/CATALOG_REPORT.md`.
- **Проверено:**
  - `python scripts/make_demo_catalog.py` — успешно создаёт базу и файлы.
  - `python scripts/inspect_catalog.py` — успешно инспектирует базу и формирует отчёт.
  - `pytest tests/app/ -q` — 4 passed.
  - `pytest -q` — 106 passed, 2 xfailed.
  - `ruff check .` — All checks passed.
- **Осталось / открытые вопросы:**
  - Реальная БД `bot_workspace/harvester/harvester.db` на текущий момент содержит 0 макетов (код парсера есть, но боевая база хранится на сервере crybaby или в rclone). Для инспекции боевой базы пользователь может скопировать реальный `harvester.db` и запустить `python scripts/inspect_catalog.py --db <путь> --storage-root <путь>`.
  - Переход к этапу S1 (FastAPI бэкенд, catalog_reader, раздача thumb/preview).

### Этап S1: Бэкенд: доступ к каталогу и раздача изображений
- **Статус:** Завершён (ветка `feature/s1`)
- **Сделано:**
  - Создан модуль Pydantic-моделей `sticker_app/models.py` (`PaginationMeta`, `StickerSummary`, `StickerDetail`, `StickerListResponse`, `TopicInfo`, `CategoryLevel2`, `CategoryTree`, `HealthResponse`). В публичных моделях полностью отсутствуют внутренние файловые пути.
  - Создан модуль чтения каталога `sticker_app/catalog_reader.py`: строго read-only подключение к `harvester.db`, фильтрация (`asset_type`, категории, темы, полнотекстовый поиск, лицензионный фильтр), пагинация, безопасное разрешение путей с защитой от path traversal (`resolve_asset_path`, `PathTraversalError`).
  - Создан сервис изображений `sticker_app/image_service.py`: масштабирование (LANCZOS) и сохранение альфа-прозрачности, форматы WebP (`thumb` до 256px, `preview` до 1024px) и исходный PNG (`full`), кэширование на диске в `CACHE_DIR`, генерация ETag.
  - Создано FastAPI приложение `sticker_app/main.py`: эндпоинты `/api/v1/health`, `/api/v1/stickers`, `/api/v1/stickers/{asset_id}`, `/api/v1/stickers/{asset_id}/image`, `/api/v1/categories`, поддержка HTTP-кэширования (`Cache-Control: public, max-age=86400`, `ETag`, `304 Not Modified`), строгий CORS из `config.CORS_ORIGINS`.
  - Создан скрипт генерации схемы OpenAPI `scripts/export_openapi.py`, экспортирован актуальный `docs/openapi.json`.
  - Добавлены зависимости `fastapi`, `uvicorn`, `pydantic` в `requirements.txt` и `httpx` в `requirements-dev.txt`.
  - Написаны тесты в `tests/app/test_api_catalog.py` (11 тестов: health, пагинация, фильтрация, поиск, детали, 404, thumb/preview/full, сохранение альфа-канала, кэш, ETag/304, защита от path traversal, дерево категорий, фильтр лицензий).
- **Проверено:**
  - `python scripts/export_openapi.py` — OpenAPI контракт сгенерирован в `docs/openapi.json`.
  - Дымовой тест live-сервера `uvicorn sticker_app.main:app` — отвечает `200 OK` на `/api/v1/health`.
  - `pytest tests/app/test_api_catalog.py -v` — 11 passed.
  - `pytest -q` — 117 passed, 2 xfailed.
  - `ruff check .` — All checks passed.
- **Осталось / открытые вопросы:**
  - Переход к этапу S2: собственная база `app.db`, миграции, обогащение метаданных (теги, палитры, aspect, alpha_coverage) и CLI управления лицензиями.

### Этап S2: Метаданные, теги и лицензионный фильтр (app.db)
- **Статус:** Завершён (ветка `feature/s2`)
- **Сделано:**
  - Создан модуль подключения и миграций базы данных сервиса `sticker_app/db.py`: режим WAL (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`), автоматическое выполнение нумерованных миграций из `sticker_app/migrations/`.
  - Создана начальная миграция `sticker_app/migrations/001_initial.sql`: таблицы `asset_meta` (с индексами на `usable_in_service` и `takedown`) и таблица журнала аудита `license_audit`.
  - Создан модуль очистки и извлечения тегов `sticker_app/tag_extractor.py`: удаление служебных префиксов (`03_`, `01_`), фильтрация пунктуации и обширного словаря стоп-слов (RU/EN), приведение к нижнему регистру, дедупликация с приоритетом по специфичности (title -> topic_name -> folder_slug -> category_lvl2 -> search_queries -> category_lvl1), максимум 12 тегов на макет.
  - Создан модуль анализа растра `sticker_app/raster_meta.py`: расчёт пропорций `aspect`, вычисление `alpha_coverage` на миниатюре 256px, извлечение 5 доминирующих HEX-цветов (квантизация Pillow с отсечением прозрачного фона), флаг `is_square_ok`.
  - Создана CLI-команда синхронизации `python -m sticker_app.sync_catalog`: поддержка `--dry-run` и `--limit`, полная идемпотентность, гарантированное сохранение ранее выставленных ручных лицензий и статуса takedown, расчёт `usable_in_service` с учётом `LICENSE_FILTER`.
  - Создан CLI разметки лицензий и такдауна `python -m sticker_app.set_license`: поддержка отдельного макета (`--asset`) и пакета темы (`--topic`), фиксация каждого изменения в `license_audit`, пересчёт `usable_in_service`.
  - Интеграция с каталогом и API (`sticker_app/catalog_reader.py`, `sticker_app/main.py`, `sticker_app/models.py`): монтирование `app.db` через SQLite `ATTACH DATABASE`, JOIN с `app.asset_meta`, публичные эндпоинты отдают только макеты с `usable_in_service = 1`, добавлены поля `tags`, `palette`, `aspect`, `alpha_coverage` в карточки и детали стикеров.
  - Добавлен эндпоинт `GET /api/v1/tags`: получение списка популярных тегов с частотой использования среди доступных макетов.
  - Обновлена OpenAPI-схема в `docs/openapi.json`.
  - Создан тестовый модуль `tests/app/test_sync_meta.py` (7 тестов: миграции, очистка тегов, идемпотентность синхронизации, сохранение ручных лицензий, пакетное обновление темы, фильтр лицензий и takedown, API тегов и обогащенных метаданных).
- **Примеры обогащённых макетов (10 образцов из demo_catalog):**
  1. `demo_01_01_bfecf7` (Ретро Вейв / Неоновый Закат): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `неоновый, закат, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  2. `demo_01_02_0d9052` (Ретро Вейв / Пальма 80-х): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `пальма, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid, palm`.
  3. `demo_01_03_414d77` (Ретро Вейв / Синтвейв Сетка): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `синтвейв, сетка, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  4. `demo_01_04_fbb1a3` (Ретро Вейв / Кассета VHS): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `кассета, vhs, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  5. `demo_01_05_415f1c` (Ретро Вейв / Аркадный Автомат): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `аркадный, автомат, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  6. `demo_01_06_f2ecf9` (Ретро Вейв / Неоновое Солнце): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `неоновое, солнце, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  7. `demo_01_07_b032cb` (Ретро Вейв / Делориан Скетч): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `делориан, скетч, ретро, вейв, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid`.
  8. `demo_01_08_f2ead8` (Ретро Вейв / Вейв Значок): aspect=1.0, alpha=0.5601, palette: `["#FFBE0B", "#FFF6DF", "#FFBE0D", "#FFBA02", "#FFBE06"]`, теги: `вейв, значок, ретро, retro, wave, эстетика, synthwave, 80s, sunset, neon, grid, palm`.
  9. `demo_02_01_7b66d7` (Киберпанк Неон / Кибер Череп): aspect=2.0, alpha=0.7617, palette: `["#FF4D6D", "#FFFFFF", "#FF4D6E", "#FF4567", "#FF98AB"]`, теги: `кибер, череп, киберпанк, неон, cyberpunk, neon, эстетика, sci, fi, skull, visor, glitch`.
  10. `demo_02_02_9af01c` (Киберпанк Неон / Неоновый Визор): aspect=2.0, alpha=0.7617, palette: `["#FF4D6D", "#FFFFFF", "#FF4D6E", "#FF4567", "#FF98AB"]`, теги: `неоновый, визор, киберпанк, неон, cyberpunk, neon, эстетика, sci, fi, skull, visor, glitch`.
- **Проверено:**
  - `python -m sticker_app.sync_catalog` — успешно отрабатывает на синтетическом каталоге, сохраняет 80 макетов, идемпотентно.
  - `python -m sticker_app.set_license` — успешно обновляет одиночные макеты и пакетно по теме, логирует аудит.
  - `pytest tests/app/` — 22 passed.
  - `pytest -q` — 124 passed, 2 xfailed.
  - `ruff check .` — All checks passed.
- **Осталось / открытые вопросы:**
  - Реализовано в этапе S3.

### Этап S3: Вкусовой профиль и система рекомендаций без аккаунтов
- **Статус:** Завершён (ветка `feature/s3`)
- **Сделано:**
  - Создана миграция `sticker_app/migrations/002_features.sql`: таблицы `asset_features` (хранение L2-нормированных векторов в BLOB float32) и `feature_vocabularies` (словарь модели).
  - Создан модуль построения признаков `sticker_app/features.py`:
    - Архитектура Уровня A: нормированная конкатенация 4 блоков с весами:
      - TF-IDF по тегам со сглаженным IDF ($\ln((N+1)/(DF+1)) + 1.0$), вес 0.5.
      - One-hot вектор подкатегории и темы, вес 0.2.
      - Цветовая 11-корзинная HSV-гистограмма по палитре (8 корзин оттенка + 3 ахроматических: черный/серый/белый), вес 0.2.
      - Геометрические признаки (`aspect_norm` и `alpha_coverage`), вес 0.1.
    - Итоговый вектор L2-нормирован ($||V||_2 = 1.0$), что позволяет рассчитывать косинусное сходство через быстрое скалярное произведение ($A \cdot B$).
    - Создан интерфейс `EmbeddingProvider` и базовый `LevelAEmbeddingProvider` с прозрачным отбоем (fallback) при отсутствии нейросетевых зависимостей (Уровень B).
  - Создана CLI-команда построения векторов `python -m sticker_app.build_features`: расчет векторов для всех доступных макетов и сохранение словаря в `app.db`.
  - Создан движок рекомендаций `sticker_app/recommender.py`:
    - Кэширование матрицы векторов в памяти процесса (`RecommenderEngine`).
    - Farthest-Point Sampling (FPS) для карточек онбординга (`GET /api/v1/onboarding/cards`) с ограничением $\le 2$ на тему и адаптивным балансом.
    - Построение вкусового профиля (`POST /api/v1/taste/profile`): $P = \text{norm}(\text{mean}(liked) - 0.5 \cdot \text{mean}(disliked))$ и извлечение топ-5 ассоциативных тегов.
    - Ранжирование с диверсификацией Maximal Marginal Relevance (MMR, $\lambda=0.7$) по формуле $\arg\max [\lambda \cdot Sim(D_i, Q) - (1-\lambda) \cdot \max_{D_j \in S} Sim(D_i, D_j)]$ с ограничениями `max_per_topic` и `min_unique_topics`.
    - Обработка холодного старта (Cold Start при $< 2$ лайков): подмешивание разнообразных тем, автоснижение $\lambda$ до $0.3 \dots 0.5$.
  - Созданы новые Pydantic-модели в `sticker_app/models.py` (`TasteProfileRequest`, `TasteProfileResponse`, `RecommendConstraints`, `RecommendRequest`, `RecommendedSticker`, `RecommendResponse`) и реализованы эндпоинты в `sticker_app/main.py`.
  - Обновлена OpenAPI-схема в `docs/openapi.json`.
  - Создан скрипт оценки `scripts/eval_recommender.py`: расчет Intra-List Similarity (ILS), энтропии тем и Precision@K со сравнением Pure Cosine vs MMR.
  - Составлена подробная математическая и архитектурная документация в `docs/RECOMMENDER.md`.
  - Созданы тесты: `tests/app/test_features.py` (6 тестов) и `tests/app/test_recommender.py` (5 тестов), `tests/app/test_api_recommender.py` (4 теста).
- **Результаты оценки (scripts/eval_recommender.py):**
  - При переходе от Pure Cosine к MMR ($\lambda=0.7$):
    - ILS (попарное сходство внутри выдачи) снижается с ~0.43 до ~0.17 (разнообразие возрастает более чем в 2 раза).
    - Количество уникальных тем в топ-10 возрастает с 2 до 5 (полный охват тем).
    - Энтропия тем возрастает с 0.971 до 2.171.
    - В холодном старте (0 лайков) возвращается 5 уникальных тем с ILS 0.1735.
- **Проверено:**
  - `python scripts/eval_recommender.py` — расчет метрик и вывод отчета успешны.
  - `python scripts/export_openapi.py` — OpenAPI контракт актуализирован.
  - `pytest tests/app/ -v` — 37 passed.
  - `pytest -q` — 139 passed, 2 xfailed.
  - `ruff check .` — All checks passed.
- **Осталось / открытые вопросы:**
  - Реализовано в этапе S4.

### Этап S4: Объекты оклейки, геометрия и единицы измерения
- **Статус:** Завершён (ветка `feature/s4`)
- **Сделано:**
  - Создан каталог объектов оклейки `objects/`:
    - `objects/macbook_air_13.json` (304.1 × 214.9 мм, $R=8.0$ мм, safe_margin=4.0 мм)
    - `objects/macbook_air_15.json` (340.4 × 237.6 мм, $R=9.0$ мм, safe_margin=4.0 мм)
    - `objects/macbook_pro_14.json` (312.6 × 221.2 мм, $R=8.0$ мм, safe_margin=4.0 мм)
    - `objects/macbook_pro_16.json` (355.7 × 248.1 мм, $R=9.0$ мм, safe_margin=4.0 мм)
    - Все объекты снабжены точной SVG-маской контура (`mask_svg_path_mm`), флагом `verified: false` до физического замера линейкой.
  - Создан модуль загрузки объектов `sticker_app/objects.py` с защитой от path traversal и Pydantic-модели `ObjectItem`, `ObjectsListResponse` в `sticker_app/models.py`.
  - Реализованы эндпоинты `GET /api/v1/objects` и `GET /api/v1/objects/{id}` с валидацией и 404-ответом.
  - Созданы зеркальные модули геометрии на двух языках:
    - Python: `sticker_app/geometry.py`
    - TypeScript: `web/src/geometry/index.ts`
    - Функции: `mm_to_px`, `px_to_mm`, `rotate_point` (вращение по часовой стрелке при оси Y вниз), `get_sticker_polygon`, `get_rotated_bbox`, `is_point_inside_rounded_rect`, `is_sticker_inside_object` (аналитическая проверка попадания полигона в скруглённый прямоугольник крышки ноутбука с дугами углов и безопасным отступом).
  - Сгенерированы общие эталонные векторы `tests/golden/geometry.json`:
    - 42 кейса конверсий `px <-> mm` по разным масштабам (1.0, 2.54, 3.7795, 10.0, 0.5, 4.0).
    - 12 кейсов вращения отдельных точек на углы 0°, 90°, 180°, 270°, 360°, 37°, -15°, 359°, 45°, 720°.
    - 36 комплексных кейсов стикеров на 4 моделях MacBook (в центре, с углами поворота, на краях, в скруглённых углах с выходом за границу дуги, с нарушением safe margin).
  - Написаны зеркальные кросс-платформенные тесты, читающие **один и тот же** `tests/golden/geometry.json`:
    - Python: `tests/app/test_geometry.py` (6 тестов)
    - TypeScript: `web/src/geometry/index.test.ts` (5 тестов)
    - Максимальная погрешность между Python и TypeScript: $\le 0.01$ мм.
  - Создана схема дизайна `design.schema.json` (Draft 2020-12):
    - `schema_version`: "1.0"
    - `object_id`: string
    - `placements`: массив до 150 стикеров, ширина 15.0–150.0 мм, координаты центра, rotation, z, locked.
  - Создан модуль валидации дизайна `sticker_app/design.py` (`validate_design_data`, `check_design_boundaries`) и эндпоинт `POST /api/v1/design/validate`.
  - Актуализирована спецификация `docs/openapi.json`.
  - Написаны тесты: `tests/app/test_objects_api.py` (7 тестов), `tests/app/test_geometry.py` (6 тестов), `tests/app/test_design_schema.py` (10 тестов).
- **Проверено:**
  - `node --experimental-strip-types --test web/src/geometry/index.test.ts` — 5 passed (все 36 комплексных кейсов, вращения и конверсии).
  - `pytest tests/app/` — 60 passed.
  - `pytest -q` — 162 passed, 2 xfailed.
  - `ruff check .` — All checks passed.
  - `python scripts/export_openapi.py` — OpenAPI контракт актуализирован.
- **Осталось / открытые вопросы:**
  - Реальный замер линейкой крышек MacBook для подтверждения `verified: true` перед боевым запуском на плоттер (сейчас `verified: false`).
  - Переход к этапу S5: Фронтенд кастомайзера на React + TypeScript + Vite + react-konva + Zustand.

---

### Следующие этапы
- **S5:** Фронтенд: холст-кастомайзер (React, Vite, Konva, Zustand).
- **S6:** Онбординг вкуса и «Собрать с помощью ИИ» (раскладка с MMR).
- **S7:** Экспорт для плоттера (лист резки, карта сборки, спот-цвет CutContour).
- **S8:** Сохранение дизайнов, заказы и связка с плоттером.
- **S9:** Усиление, развёртывание (Docker, Caddy, rate limiting, e2e).

