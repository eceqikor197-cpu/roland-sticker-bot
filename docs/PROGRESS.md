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

---

### Следующие этапы
- **S1:** FastAPI-бэкенд и доступ к каталогу (read-only `harvester.db`, раздача thumb/preview).
- **S2:** Метаданные, теги и лицензионный фильтр (`app.db`, `sync_catalog`).
- **S3:** Вкусовой профиль и рекомендации (TF-IDF, MMR, холодный старт).
- **S4:** Объекты, геометрия и единицы (Tech Specs макбуков, golden vectors).
- **S5:** Фронтенд: холст-кастомайзер (React, Vite, Konva, Zustand).
- **S6:** Онбординг вкуса и «Собрать с помощью ИИ» (раскладка с MMR).
- **S7:** Экспорт для плоттера (лист резки, карта сборки, спот-цвет CutContour).
- **S8:** Сохранение дизайнов, заказы и связка с плоттером.
- **S9:** Усиление, развёртывание (Docker, Caddy, rate limiting, e2e).
