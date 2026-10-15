---
name: s4_geometry_objects
description: "Этап S4: Объекты оклейки (MacBook), единая геометрия mm<->px на Python и TypeScript, golden-тесты tests/golden/geometry.json, схема дизайна design.schema.json."
---

# Этап S4: Объекты, геометрия и единицы (общий контракт px и мм)

## Контекст и правила
- Читай `AGENTS.md`, `docs/COORDINATES.md`, `docs/PROGRESS.md`.
- **Миллиметры (`mm`) — единственный источник истины.** Пиксели только для UI.
- Позиция стикера — его геометрический центр. Вращение по часовой стрелке.
- Зависимости: для точных геометрических операций полигонов на бэкенде разрешено добавить `shapely` (обосновать в отчёте: быстрое и точное пересечение полигонов с маской объекта).
- Работа ведётся в ветке `feature/s4`.

## Задачи этапа
1. **Каталог объектов (`objects/*.json`):**
   - JSON-файлы описания геометрии объектов с полями:
     `{ id, title, width_mm, height_mm, corner_radius_mm, mask_svg_path_mm, safe_margin_mm, notes, verified: false }`
   - Стартовые объекты по официальным Tech Specs Apple:
     - `macbook_air_13`: 304.1 × 214.9 мм
     - `macbook_air_15`: 340.4 × 237.6 мм
     - `macbook_pro_14`: 312.6 × 221.2 мм
     - `macbook_pro_16`: 355.7 × 248.1 мм
   - До подтверждения человеком замера линейкой флаг `verified = false`.
   - Эндпоинты API: `GET /api/v1/objects` и `GET /api/v1/objects/{id}`.

2. **Модули геометрии (зеркальное поведение на двух языках):**
   - Python: `sticker_app/geometry.py`
   - TypeScript: `web/src/geometry/index.ts`
   - Функции:
     - `mm_to_px(val_mm, scale) -> float`
     - `px_to_mm(val_px, scale) -> float`
     - `rotate_point(x, y, cx, cy, angle_deg) -> (rx, ry)`
     - `get_rotated_bbox(cx_mm, cy_mm, w_mm, h_mm, angle_deg) -> (min_x, min_y, max_x, max_y)`
     - `get_sticker_polygon(cx_mm, cy_mm, w_mm, h_mm, angle_deg) -> List[(x, y)]`
     - `is_sticker_inside_object(sticker_poly, object_mask) -> bool`

3. **Общие тестовые векторы (`tests/golden/geometry.json`):**
   - Минимум 30 разнородных тестовых кейсов:
     - Разные масштабы (`scale`: 1.0, 2.54, 3.779, 10.0).
     - Углы поворота: 0°, 90°, 180°, 270°, 37°, -15°, 359°.
     - Центры стикеров в центре объекта, в углах, со смещением.
     - Проверки попадания внутрь/наружу маски объекта со скруглёнными углами.
   - Тесты Python (`pytest tests/app/test_geometry.py`) и TypeScript (`vitest`) обязаны читать **ОДИН И ТОТ ЖЕ** файл `tests/golden/geometry.json`.
   - Допустимая погрешность: **не более 0.01 мм**.

4. **Схема дизайна (`design.schema.json`):**
   - Валидация структуры дизайна:
     - `schema_version`: "1.0"
     - `object_id`: string
     - `placements`: массив `{ asset_id, x_mm, y_mm, width_mm, rotation_deg, z, locked }`
     - Ограничения: ширина от 15.0 до 150.0 мм, до 150 стикеров в композиции.
     - `created_at`: float/iso.

## Критерий готовности
- Golden-тесты геометрии проходят на Python (и готовы для TS) с погрешностью <= 0.01 мм.
- Эндпоинты `/api/v1/objects` возвращают объекты из папки `objects/`.
- Валидация схемы дизайна отсекает некорректные размеры и координаты.
- `python -m pytest -q` и `python -m ruff check .` зелёные.
- Результаты зафиксированы в ветке `feature/s4`, обновлён `docs/PROGRESS.md`.
