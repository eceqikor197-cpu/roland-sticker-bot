"""Тесты хранилища SQLite (WAL mode), миграции JSON и изоляции макетов — Фаза 6.

Проверяют:
1. Однократную миграцию JSON-файлов в SQLite (включая --dry-run и сохранение исходных файлов).
2. Многопоточную запись и чтение без конфликтов блокировки (WAL-режим + thread-local).
3. Изоляцию параметров макета (size_mm, offset_mm, stretch_x, stretch_y, nesting_qty) между заданиями.
4. Удаление данных пользователя (GDPR / Privacy) из таблиц users, prefs, jobs.
5. Корректность работы таблиц admin_state и обратной совместимости.
"""

from __future__ import annotations

import concurrent.futures
import json
import random
from pathlib import Path


import telegram_bot
from scripts.migrate_json_to_sqlite import migrate
from storage.admin import get_admin_state, is_user_banned, toggle_ban_user, update_admin_state
from storage.database import init_storage, set_custom_db_path
from storage.jobs import (
    get_job,
    list_all_jobs,
    save_job,
    update_job,
)
from storage.prefs import (
    get_user_prefs,
    update_user_prefs,
)
from storage.users import (
    get_all_users,
    get_user,
    record_user_activity,
)


def test_migration_dry_run_and_real_run(tmp_path: Path):
    """Миграция: --dry-run не пишет в БД, реальный прогон переносит все данные, исходные JSON не удаляются."""
    # 1. Создаём структуру папок с JSON-файлами
    json_dir = tmp_path / "legacy_workspace"
    jobs_dir = json_dir / "jobs"
    jobs_dir.mkdir(parents=True)

    users_data = {
        "101": {
            "user_id": 101,
            "first_name": "Алексей",
            "last_name": "Иванов",
            "username": "alex_ivanov",
            "first_seen": 1700000000.0,
            "last_seen": 1700001000.0,
            "jobs_count": 2,
            "last_action": "Загрузка изображения",
            "last_job_id": "job_01",
        },
        "102": {
            "user_id": 102,
            "first_name": "Мария",
            "last_name": "",
            "username": "maria_print",
            "first_seen": 1700000500.0,
            "last_seen": 1700000900.0,
            "jobs_count": 0,
            "last_action": "Старт",
            "last_job_id": "",
        },
    }
    (json_dir / "users_db.json").write_text(json.dumps(users_data, ensure_ascii=False), encoding="utf-8")

    prefs_data = {
        "101": {
            "offset_mm": 3.5,
            "size_mm": 120.0,
            "keep_background": False,
            "perf_cut_mode": "perf_square",
            "perf_cut_passes": 3,
            "nesting_qty": 5,
        }
    }
    (json_dir / "user_prefs.json").write_text(json.dumps(prefs_data, ensure_ascii=False), encoding="utf-8")

    admin_data = {
        "live_spy_enabled": True,
        "banned_users": [999],
    }
    (json_dir / "admin_state.json").write_text(json.dumps(admin_data, ensure_ascii=False), encoding="utf-8")

    # Создаём два каталога заданий с meta.json
    job1_dir = jobs_dir / "job_01"
    job1_dir.mkdir()
    meta1 = {
        "job_id": "job_01",
        "user_id": 101,
        "orig_stem": "sticker_fox",
        "input_filename": "input.png",
        "size_mm": 80.0,
        "offset_mm": 2.0,
        "nesting_qty": 10,
        "pricing": {"retail_rub": 350},
    }
    (job1_dir / "meta.json").write_text(json.dumps(meta1, ensure_ascii=False), encoding="utf-8")

    job2_dir = jobs_dir / "job_02"
    job2_dir.mkdir()
    meta2 = {
        "job_id": "job_02",
        "user_id": 101,
        "orig_stem": "cat_badge",
        "input_filename": "input.jpg",
        "size_mm": 50.0,
        "offset_mm": 1.5,
        "nesting_qty": 1,
    }
    (job2_dir / "meta.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")

    db_path = tmp_path / "migrated.db"
    set_custom_db_path(db_path)
    init_storage(db_path)

    # 2. Проверяем --dry-run
    res_dry = migrate(base_dir=json_dir, db_path=db_path, dry_run=True)
    assert res_dry["users"] == 2
    assert res_dry["prefs"] == 1
    assert res_dry["jobs"] == 2
    assert res_dry["admin_keys"] == 2

    # Проверяем, что в базе по-прежнему ничего нет
    assert len(get_all_users()) == 0
    assert len(list_all_jobs()) == 0

    # 3. Реальный прогон миграции
    res_real = migrate(base_dir=json_dir, db_path=db_path, dry_run=False)
    assert res_real["users"] == 2
    assert res_real["prefs"] == 1
    assert res_real["jobs"] == 2
    assert res_real["admin_keys"] == 2

    # Проверяем данные в SQLite
    users = get_all_users()
    assert "101" in users and users["101"]["first_name"] == "Алексей"
    assert "102" in users and users["102"]["username"] == "maria_print"

    p101 = get_user_prefs(101)
    assert p101["offset_mm"] == 3.5
    assert p101["size_mm"] == 120.0
    assert p101["perf_cut_mode"] == "perf_square"

    adm = get_admin_state()
    assert adm["live_spy_enabled"] is True
    assert 999 in adm["banned_users"]

    j1 = get_job("job_01")
    assert j1 is not None
    assert j1["user_id"] == 101
    assert j1["size_mm"] == 80.0
    assert j1["pricing"]["retail_rub"] == 350

    # 4. Исходные файлы НЕ удалены
    assert (json_dir / "users_db.json").exists()
    assert (json_dir / "user_prefs.json").exists()
    assert (json_dir / "admin_state.json").exists()
    assert (job1_dir / "meta.json").exists()
    assert (job2_dir / "meta.json").exists()


def test_multithreaded_concurrent_writes(tmp_path: Path):
    """Многопоточная запись и чтение без блокировок (WAL mode + thread-local)."""
    db_path = tmp_path / "concurrent.db"
    set_custom_db_path(db_path)
    init_storage(db_path)

    num_threads = 12
    iterations_per_thread = 20

    errors = []

    def worker(worker_id: int):
        try:
            for i in range(iterations_per_thread):
                uid = worker_id * 1000 + i
                # Запись пользователя
                record_user_activity(
                    uid,
                    first_name=f"User_{worker_id}_{i}",
                    action=f"Action {i}",
                )
                # Запись настроек
                update_user_prefs(
                    uid,
                    offset_mm=round(random.uniform(1.0, 5.0), 2),
                    size_mm=round(random.uniform(50.0, 150.0), 1),
                )
                # Запись задания
                job_id = f"job_w{worker_id}_{i}"
                save_job(
                    {
                        "job_id": job_id,
                        "user_id": uid,
                        "orig_stem": f"file_{worker_id}_{i}",
                        "size_mm": 90.0 + i,
                        "pricing": {"cost": i * 10},
                    }
                )
                # Чтение
                u = get_user(uid)
                p = get_user_prefs(uid)
                j = get_job(job_id)
                assert u is not None
                assert p is not None
                assert j is not None
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, tid) for tid in range(num_threads)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Ошибки при параллельной работе с БД: {errors}"

    # Проверяем итоговое количество строк
    all_users = get_all_users()
    all_jobs = list_all_jobs()
    assert len(all_users) == num_threads * iterations_per_thread
    assert len(all_jobs) == num_threads * iterations_per_thread


def test_job_layout_parameter_isolation(tmp_path: Path):
    """Параметры макета (размер, отступ, растяжение, тираж) изолированы для каждого задания."""
    user_id = 777

    # Задаём настройки по умолчанию для пользователя
    update_user_prefs(user_id, size_mm=100.0, offset_mm=2.5, stretch_x=1.0, stretch_y=1.0, nesting_qty=1)

    # Создаём Задание А (наклейка 150 мм, кант 4 мм)
    job_a_meta = {
        "job_id": "job_A",
        "user_id": user_id,
        "orig_stem": "sticker_A",
        "size_mm": 150.0,
        "offset_mm": 4.0,
        "stretch_x": 1.2,
        "stretch_y": 1.0,
        "nesting_qty": 20,
        "perf_cut_mode": "die_cut",
    }
    save_job(job_a_meta)

    # Создаём Задание Б (наклейка 60 мм, кант 1.5 мм)
    job_b_meta = {
        "job_id": "job_B",
        "user_id": user_id,
        "orig_stem": "sticker_B",
        "size_mm": 60.0,
        "offset_mm": 1.5,
        "stretch_x": 1.0,
        "stretch_y": 1.5,
        "nesting_qty": 5,
        "perf_cut_mode": "perf_circle",
    }
    save_job(job_b_meta)

    # Модифицируем Задание А (увеличиваем размер до 180 мм, кант до 5 мм)
    update_job("job_A", size_mm=180.0, offset_mm=5.0, nesting_qty=50)

    # Проверяем, что Задание Б НЕ изменилось
    job_b = get_job("job_B")
    assert job_b is not None
    assert job_b["size_mm"] == 60.0
    assert job_b["offset_mm"] == 1.5
    assert job_b["stretch_y"] == 1.5
    assert job_b["nesting_qty"] == 5
    assert job_b["perf_cut_mode"] == "perf_circle"

    # Проверяем, что Задание А обновилось корректно
    job_a = get_job("job_A")
    assert job_a is not None
    assert job_a["size_mm"] == 180.0
    assert job_a["offset_mm"] == 5.0
    assert job_a["nesting_qty"] == 50
    assert job_a["stretch_x"] == 1.2

    # Проверяем, что настройки по умолчанию пользователя остались нетронутыми
    default_prefs = get_user_prefs(user_id)
    assert default_prefs["size_mm"] == 100.0
    assert default_prefs["offset_mm"] == 2.5
    assert default_prefs["nesting_qty"] == 1


def test_gdpr_data_deletion_sqlite(tmp_path: Path):
    """Полное удаление данных пользователя через delete_user_data очищает users, prefs, jobs."""
    u1 = 501
    u2 = 502

    record_user_activity(u1, first_name="User1")
    record_user_activity(u2, first_name="User2")

    update_user_prefs(u1, size_mm=80.0)
    update_user_prefs(u2, size_mm=90.0)

    save_job({"job_id": "j_u1_1", "user_id": u1, "orig_stem": "test1"})
    save_job({"job_id": "j_u1_2", "user_id": u1, "orig_stem": "test2"})
    save_job({"job_id": "j_u2_1", "user_id": u2, "orig_stem": "test3"})

    # Создаём физическую директорию задания для u1
    jdir = telegram_bot.JOBS_DIR / "j_u1_1"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "meta.json").write_text(json.dumps({"job_id": "j_u1_1", "user_id": u1}))

    res = telegram_bot.delete_user_data(u1)
    assert res["user_id"] == u1
    assert res["deleted_jobs"] >= 2
    assert res["deleted_user_record"] is True
    assert res["deleted_user_prefs"] is True

    # Проверяем, что u1 удалён из SQLite
    assert get_user(u1) is None
    assert get_job("j_u1_1") is None
    assert get_job("j_u1_2") is None
    assert not jdir.exists()

    # Проверяем, что u2 остался невредим
    assert get_user(u2) is not None
    assert get_job("j_u2_1") is not None
    assert get_user_prefs(u2)["size_mm"] == 90.0


def test_admin_state_and_ban_toggle():
    """Проверка управления состоянием администратора и блокировками в SQLite."""
    target_uid = 444333

    # Изначально пользователь не забанен
    assert not is_user_banned(target_uid)

    # Баним пользователя
    now_banned = toggle_ban_user(target_uid)
    assert now_banned is True
    assert is_user_banned(target_uid) is True

    # Разбаниваем
    now_banned = toggle_ban_user(target_uid)
    assert now_banned is False
    assert is_user_banned(target_uid) is False

    # Обновление произвольного admin ключа
    st = update_admin_state(custom_metric="ok", test_val=123)
    assert st.get("custom_metric") == "ok"
    assert st.get("test_val") == 123
