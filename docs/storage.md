# Архитектура хранилища SQLite (Фаза 6)

## Обзор

Модуль `storage/` переводит хранение состояния Telegram-бота с монолитных JSON-файлов (`users_db.json`, `user_prefs.json`, `admin_state.json`, `jobs/*/meta.json`) на реляционную СУБД SQLite в режиме **WAL (Write-Ahead Logging)**.

### Проблемы предыдущей архитектуры (JSON):
1. **Глобальная блокировка и перезапись целиком:** Любое обновление действия пользователя или настроек приводило к полной перезаписи всего JSON-файла на диске под глобальным `threading.RLock()`.
2. **Взаимное влияние макетов:** Настройки параметров резки (размер, кант, растяжение, тираж, тип реза) сохранялись на уровне пользователя (`user_prefs.json`). При параллельной работе с двумя изображениями изменение параметров в одном макете перезаписывало настройки второго.
3. **Риск повреждения данных:** При аварийном завершении процесса или нехватке памяти во время записи существовал риск повреждения JSON.

---

## Архитектура многопоточности и соединений

- **Режим журнала:** `PRAGMA journal_mode=WAL;` — позволяет параллельное чтение несколькими потоками без блокировки пишущего потока.
- **Синхронизация:** `PRAGMA synchronous=NORMAL;` — оптимальный баланс надёжности и скорости транзакций.
- **Ожидание занятости:** `PRAGMA busy_timeout=15000;` — ожидание освобождения блокировки до 15 секунд при конкурентных записях.
- **Модель потоков:** Одно соединение SQLite на каждый поток выполнения (`threading.local()`), что исключает гонки между потоками в Python и удовлетворяет требованиям безопасной работы.
- **Внешние ключи:** `PRAGMA foreign_keys=ON;`.

---

## Схема таблиц

### 1. `users` — Пользователи и активность
Хранит профили и статистику взаимодействий пользователей с ботом.

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    jobs_count INTEGER NOT NULL DEFAULT 0,
    last_action TEXT NOT NULL DEFAULT '',
    last_job_id TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC);
```

### 2. `prefs` — Настройки по умолчанию пользователя
Хранит параметры по умолчанию для новых макетов конкретного пользователя. Не перезаписывает параметры уже существующих заданий.

```sql
CREATE TABLE IF NOT EXISTS prefs (
    user_id INTEGER PRIMARY KEY,
    offset_mm REAL NOT NULL DEFAULT 2.5,
    size_mm REAL NOT NULL DEFAULT 100.0,
    keep_background INTEGER NOT NULL DEFAULT 0,
    vectorize INTEGER NOT NULL DEFAULT 0,
    perf_cut_mode TEXT NOT NULL DEFAULT 'none',
    perf_cut_passes INTEGER NOT NULL DEFAULT 2,
    nesting_qty INTEGER NOT NULL DEFAULT 1,
    roll_width_mm REAL NOT NULL DEFAULT 1370.0,
    stretch_x REAL NOT NULL DEFAULT 1.0,
    stretch_y REAL NOT NULL DEFAULT 1.0,
    fit_a4_sheet INTEGER NOT NULL DEFAULT 0,
    menu_tab TEXT NOT NULL DEFAULT 'main',
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
```

### 3. `jobs` — Задания и индивидуальные параметры макетов
Хранит метаданные задания и **индивидуальные параметры макета** (изоляция между одновременно обрабатываемыми файлами).

```sql
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    orig_stem TEXT NOT NULL DEFAULT '',
    input_filename TEXT NOT NULL DEFAULT '',
    eps_filename TEXT NOT NULL DEFAULT '',
    offset_mm REAL NOT NULL DEFAULT 2.5,
    size_mm REAL NOT NULL DEFAULT 100.0,
    keep_background INTEGER NOT NULL DEFAULT 0,
    vectorize INTEGER NOT NULL DEFAULT 0,
    perf_cut_mode TEXT NOT NULL DEFAULT 'none',
    perf_cut_passes INTEGER NOT NULL DEFAULT 2,
    nesting_qty INTEGER NOT NULL DEFAULT 1,
    roll_width_mm REAL NOT NULL DEFAULT 1370.0,
    stretch_x REAL NOT NULL DEFAULT 1.0,
    stretch_y REAL NOT NULL DEFAULT 1.0,
    fit_a4_sheet INTEGER NOT NULL DEFAULT 0,
    menu_tab TEXT NOT NULL DEFAULT 'main',
    num_contours INTEGER NOT NULL DEFAULT 1,
    perf_contours INTEGER NOT NULL DEFAULT 0,
    width_mm REAL NOT NULL DEFAULT 100.0,
    height_mm REAL NOT NULL DEFAULT 100.0,
    in_hot_folder INTEGER NOT NULL DEFAULT 0,
    hot_filename TEXT NOT NULL DEFAULT '',
    hot_folder_request_status TEXT NOT NULL DEFAULT '',
    hot_folder_requested_at REAL NOT NULL DEFAULT 0.0,
    pricing_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at DESC);
```

### 4. `admin_state` — Состояние и настройки администратора
Хранит глобальные флаги админки (`live_spy_enabled`, список заблокированных ID `banned_users` и др.).

```sql
CREATE TABLE IF NOT EXISTS admin_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
```

---

## Миграция с JSON-файлов

Скрипт `scripts/migrate_json_to_sqlite.py`:
- Читает существующие `users_db.json`, `user_prefs.json`, `admin_state.json` и `jobs/*/meta.json`.
- Переносит записи в таблицы базы данных без удаления оригинальных файлов.
- Поддерживает флаг `--dry-run` для безопасной предварительной валидации.
