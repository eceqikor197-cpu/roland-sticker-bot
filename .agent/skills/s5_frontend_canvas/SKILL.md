---
name: s5_frontend_canvas
description: "Этап S5: Фронтенд кастомайзера на React + TypeScript + Vite + react-konva + Zustand (холст, Transformer, слои, каталог, Drag-and-Drop, undo/redo)."
---

# Этап S5: Фронтенд: холст-кастомайзер

## Контекст и правила
- Читай `AGENTS.md`, `docs/COORDINATES.md` и сгенерированный `docs/openapi.json`.
- Стек: **Vite + React + TypeScript + react-konva + Zustand + openapi-typescript + vitest**.
- **Не использовать Fabric.js.** Только `react-konva`.
- Единственный источник истины в сторе — **миллиметры (`mm`)**.
- Пиксели вычисляются исключительно на этапе передачи координат в Konva Stage через коэффициент `scale = stage_width_px / object.width_mm`.
- Работа ведётся в директории `web/` в ветке `feature/s5`.

## Задачи этапа
1. **Каркас приложения (`web/`):**
   - Инициализация проекта Vite (React + TypeScript).
   - Генерация типов из OpenAPI: скрипт `npm run gen:api` на основе `docs/openapi.json`.
   - Клиент API и dev-прокси в `vite.config.ts` на бэкенд `:8000`.

2. **Хранилище состояния (`src/store/designStore.ts` на Zustand):**
   - Состояние: `{ object, placements: Placement[], selectionId, history: HistoryState }`.
   - Координаты строго в `mm`: `x_mm, y_mm, width_mm, rotation_deg, z, locked`.
   - Полная поддержка `undo/redo` (глубина истории до 100 шагов).
   - Автосохранение в `localStorage` с версией схемы (`schema_version: "1.0"`).

3. **Интерактивный холст (`src/canvas/`):**
   - Слои Konva:
     1. `BackgroundLayer`: контур объекта (скруглённый прямоугольник крышки MacBook).
     2. `StickersLayer`: группа стикеров с маской обрезки `clipFunc` по форме объекта.
     3. `OverlayLayer`: рамка выделения, Transformer, направляющие линейки.
   - Трансформер (`Konva.Transformer`):
     - Свободное перемещение стикера.
     - Пропорциональное масштабирование (ограничение ширины от 15.0 до 150.0 мм).
     - Поворот вокруг центра стикера с шагом привязки 15° при зажатом `Shift`.
     - Предупреждающая подсветка контура, если стикер выходит за границу маски.
   - Загрузка картинок: запрашивать `size=thumb` или `size=preview` (не `full`).

4. **Панель каталога стикеров (`src/components/Catalog/`):**
   - Поиск по названию, фильтрация по темам и тегам.
   - Добавление стикера в центр холста по клику.
   - HTML5 Drag-and-Drop: перетаскивание карточки из каталога на холст с позиционированием в точке броска.

5. **Панель слоёв и горячие клавиши:**
   - Изменение порядка `z` (на передний/задний план, выше/ниже).
   - Блокировка стикера (`locked`), дублирование (`Cmd/Ctrl+D`), удаление (`Delete/Backspace`).
   - `Cmd/Ctrl+Z` (Undo), `Cmd/Ctrl+Shift+Z` (Redo).
   - Стрелки клавиатуры для микросдвига: шаг 1 мм, с `Shift` — 10 мм.

6. **Индикаторы:**
   - Линейка в мм, масштаб экрана (Zoom), счётчик стикеров.
   - Приблизительный процент покрытия крышки ноутбука стикерами.

7. **Тесты:**
   - `vitest`: юнит-тесты на `web/src/geometry/index.ts` по эталону `tests/golden/geometry.json`.
   - Тесты store на undo/redo и сериализацию в мм.

## Критерий готовности
- Можно вручную собрать композицию из каталога, повернуть/масштабировать стикеры, перезагрузить страницу и получить точную копию из `localStorage`.
- Golden-тесты геометрии проходят на TypeScript.
- `npm run lint`, `npm run build`, `npm run test` (vitest) и `pytest` зелёные.
- Результаты зафиксированы в ветке `feature/s5`, обновлён `docs/PROGRESS.md`.
