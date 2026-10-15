"""Тесты приватности, срока хранения данных, /delete_my_data и Live-шпиона — Фаза 4.

Проверяют:
1. Значения по умолчанию для LIVE_SPY_DEFAULT (False) и JOB_RETENTION_HOURS (168 ч.) в config.
2. Краткое уведомление о конфиденциальности и кнопки /privacy, /delete_my_data в команде /start.
3. Полный текст политики конфиденциальности в команде /privacy и callback user:privacy.
4. Удаление заданий, профиля и настроек пользователя через /delete_my_data и callback user:delete_my_data.
5. Управление Live-шпионом через команду /spy и callback adm:toggle_spy, запрет для не-админов.
6. Фоновая очистка старых заданий (cleanup_old_jobs) по таймеру и отсутствие синхронного вызова в handle_media.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import telegram_bot
from config import JOB_RETENTION_HOURS, LIVE_SPY_DEFAULT
from harvester.config import JOB_RETENTION_HOURS as HARV_RETENTION, LIVE_SPY_DEFAULT as HARV_SPY
from telegram_bot import (
    ADMIN_IDS,
    ConversionLimiter,
    cleanup_old_jobs,
    delete_user_data,
    format_privacy_text,
    get_admin_state,
    handle_admin_callback,
    handle_delete_my_data,
    handle_media,
    handle_privacy,
    handle_spy_command,
    handle_start,
    handle_user_callback,
    notify_admins_about_job,
    run_cleanup_loop,
    start_cleanup_scheduler,
)


@pytest.fixture(autouse=True)
def isolate_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует каталоги заданий, базы данных и настройки во временную директорию tmp_path."""
    test_jobs_dir = tmp_path / "jobs"
    test_jobs_dir.mkdir(parents=True, exist_ok=True)
    test_prefs = tmp_path / "user_prefs.json"
    test_users = tmp_path / "users_db.json"
    test_admin_state = tmp_path / "admin_state.json"

    monkeypatch.setattr(telegram_bot, "WORK_DIR", tmp_path)
    monkeypatch.setattr(telegram_bot, "JOBS_DIR", test_jobs_dir)
    monkeypatch.setattr(telegram_bot, "PREFS_FILE", test_prefs)
    monkeypatch.setattr(telegram_bot, "USERS_FILE", test_users)
    monkeypatch.setattr(telegram_bot, "ADMIN_STATE_FILE", test_admin_state)

    # Сбрасываем лимитер конвертаций перед каждым тестом
    telegram_bot.conversion_limiter = ConversionLimiter(
        max_per_minute=6,
        max_concurrent_global=2,
    )


def test_config_privacy_defaults():
    """Параметры LIVE_SPY_DEFAULT (False) и JOB_RETENTION_HOURS (168) корректно заданы в config."""
    assert LIVE_SPY_DEFAULT is False
    assert JOB_RETENTION_HOURS == 168.0
    assert HARV_SPY is False
    assert HARV_RETENTION == 168.0
    assert telegram_bot.LIVE_SPY_DEFAULT is False
    assert telegram_bot.JOB_RETENTION_HOURS == 168.0


def test_start_message_contains_privacy_notice_and_buttons():
    """Команда /start содержит уведомление о конфиденциальности и кнопки privacy / delete."""
    msg = MagicMock()
    msg.from_user.id = 55555
    msg.from_user.first_name = "Иван"
    msg.from_user.last_name = "Тестовый"
    msg.from_user.username = "ivan_test"
    msg.chat.id = 55555

    replied_text = None
    replied_markup = None

    def fake_reply(m, text, reply_markup=None):
        nonlocal replied_text, replied_markup
        replied_text = text
        replied_markup = reply_markup

    with patch.object(telegram_bot.bot, "reply_to", side_effect=fake_reply):
        handle_start(msg)

    assert replied_text is not None
    assert "Конфиденциальность" in replied_text
    assert "хранятся" in replied_text
    assert "/privacy" in replied_text
    assert "/delete_my_data" in replied_text

    # Проверяем наличие inline-кнопок
    assert replied_markup is not None
    button_callbacks = [
        btn.callback_data
        for row in replied_markup.keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]
    assert "user:privacy" in button_callbacks
    assert "user:delete_my_data" in button_callbacks


def test_privacy_command_and_callback():
    """Команда /privacy и callback user:privacy возвращают полный текст политики конфиденциальности."""
    privacy_text = format_privacy_text()
    assert "Политика конфиденциальности" in privacy_text
    assert "CutContour" in privacy_text
    assert "Срок хранения файлов" in privacy_text
    assert "Просмотр администратором" in privacy_text
    assert "Live-мониторинг" in privacy_text
    assert "/delete_my_data" in privacy_text

    msg = MagicMock()
    msg.from_user.id = 66666
    msg.from_user.first_name = "Петр"
    msg.from_user.last_name = None
    msg.from_user.username = "petr"
    msg.chat.id = 66666

    replied_text = None
    replied_markup = None

    def fake_reply(m, text, reply_markup=None):
        nonlocal replied_text, replied_markup
        replied_text = text
        replied_markup = reply_markup

    with patch.object(telegram_bot.bot, "reply_to", side_effect=fake_reply):
        handle_privacy(msg)

    assert replied_text == privacy_text
    btn_cbs = [
        btn.callback_data
        for row in replied_markup.keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]
    assert "user:delete_my_data" in btn_cbs

    # Проверяем callback query user:privacy
    call = MagicMock()
    call.id = "call_priv_1"
    call.data = "user:privacy"
    call.from_user.id = 66666
    call.message.chat.id = 66666
    call.message.message_id = 42
    call.message.content_type = "text"

    edited_text = None

    def fake_edit(text, chat_id, message_id, reply_markup=None):
        nonlocal edited_text
        edited_text = text

    with patch.object(telegram_bot.bot, "edit_message_text", side_effect=fake_edit), \
         patch.object(telegram_bot.bot, "answer_callback_query") as mock_ans:
        handle_user_callback(call)
        mock_ans.assert_called_once_with("call_priv_1")

    assert edited_text == privacy_text


def test_delete_my_data_deletes_jobs_records_and_prefs(tmp_path: Path):
    """delete_user_data удаляет только задания и профиль указанного пользователя, не трогая других."""
    user_a = 111111
    user_b = 222222

    # Создаём задания для пользователя A (2 шт.)
    job_a1 = telegram_bot.JOBS_DIR / "job_a1"
    job_a1.mkdir()
    (job_a1 / "meta.json").write_text(json.dumps({"job_id": "job_a1", "user_id": user_a}), encoding="utf-8")
    (job_a1 / "preview_cutcontour.png").write_bytes(b"preview_a1")

    job_a2 = telegram_bot.JOBS_DIR / "job_a2"
    job_a2.mkdir()
    (job_a2 / "meta.json").write_text(json.dumps({"job_id": "job_a2", "user_id": user_a}), encoding="utf-8")
    (job_a2 / "preview_cutcontour.png").write_bytes(b"preview_a2")

    # Создаём задание для пользователя B (1 шт.)
    job_b = telegram_bot.JOBS_DIR / "job_b"
    job_b.mkdir()
    (job_b / "meta.json").write_text(json.dumps({"job_id": "job_b", "user_id": user_b}), encoding="utf-8")
    (job_b / "preview_cutcontour.png").write_bytes(b"preview_b")

    # Создаём записи в users_db.json
    telegram_bot.save_json_file(
        telegram_bot.USERS_FILE,
        {
            str(user_a): {"user_id": user_a, "first_name": "Пользователь А"},
            str(user_b): {"user_id": user_b, "first_name": "Пользователь Б"},
        },
    )

    # Создаём настройки в user_prefs.json
    telegram_bot.save_json_file(
        telegram_bot.PREFS_FILE,
        {
            str(user_a): {"size_mm": 70.0, "offset_mm": 3.0},
            str(user_b): {"size_mm": 120.0, "offset_mm": 1.5},
        },
    )

    # Добавляем историю в лимитер для пользователя A
    telegram_bot.conversion_limiter._history[user_a] = [time.time()]
    telegram_bot.conversion_limiter._active_users.add(user_a)

    # Выполняем удаление данных пользователя A
    res = delete_user_data(user_a)

    assert res["deleted_jobs"] == 2
    assert res["deleted_user_record"] is True
    assert res["deleted_user_prefs"] is True

    # Проверяем, что файлы пользователя A удалены
    assert not job_a1.exists()
    assert not job_a2.exists()

    # Проверяем, что файлы пользователя B сохранены в целости
    assert job_b.exists()
    assert (job_b / "preview_cutcontour.png").read_bytes() == b"preview_b"

    # Проверяем состояние users_db.json
    users = telegram_bot.load_json_file(telegram_bot.USERS_FILE, {})
    assert str(user_a) not in users
    assert str(user_b) in users

    # Проверяем состояние user_prefs.json
    prefs = telegram_bot.load_json_file(telegram_bot.PREFS_FILE, {})
    assert str(user_a) not in prefs
    assert str(user_b) in prefs

    # Проверяем сброс лимитера
    assert user_a not in telegram_bot.conversion_limiter._history
    assert user_a not in telegram_bot.conversion_limiter._active_users


def test_delete_my_data_command_and_callback():
    """Команда /delete_my_data и callback user:delete_my_data вызывают удаление и отвечают пользователю."""
    user_id = 777777

    # Создаём тестовое задание
    jdir = telegram_bot.JOBS_DIR / "job_to_del"
    jdir.mkdir()
    (jdir / "meta.json").write_text(json.dumps({"job_id": "job_to_del", "user_id": user_id}), encoding="utf-8")

    # Тестируем команду /delete_my_data
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = user_id

    replied_text = None

    def fake_reply(m, text, **kwargs):
        nonlocal replied_text
        replied_text = text

    with patch.object(telegram_bot.bot, "reply_to", side_effect=fake_reply):
        handle_delete_my_data(msg)

    assert replied_text is not None
    assert "успешно удалены" in replied_text
    assert "1 шт." in replied_text
    assert not jdir.exists()

    # Тестируем callback user:delete_my_data для пользователя без активных заданий
    call = MagicMock()
    call.id = "call_del_1"
    call.data = "user:delete_my_data"
    call.from_user.id = user_id
    call.message.chat.id = user_id
    call.message.message_id = 99
    call.message.content_type = "text"

    edited_text = None

    def fake_edit(text, chat_id, message_id, **kwargs):
        nonlocal edited_text
        edited_text = text

    with patch.object(telegram_bot.bot, "edit_message_text", side_effect=fake_edit), \
         patch.object(telegram_bot.bot, "answer_callback_query") as mock_ans:
        handle_user_callback(call)
        mock_ans.assert_called_once_with("call_del_1", "Ваши данные удалены")

    assert edited_text is not None
    assert "успешно удалены" in edited_text
    assert "нет активных файлов" in edited_text


def test_live_spy_default_and_admin_toggle():
    """Live-шпион по умолчанию выключен; админ может переключать через /spy и adm:toggle_spy."""
    # 1. По умолчанию состояние Live-шпиона False
    st = get_admin_state()
    assert st.get("live_spy_enabled") is False

    admin_id = next(iter(ADMIN_IDS))
    user_id = 888888

    # 2. Попытка переключения обычным пользователем блокируется
    user_msg = MagicMock()
    user_msg.from_user.id = user_id
    user_msg.from_user.first_name = "User"
    user_msg.from_user.last_name = None
    user_msg.from_user.username = None
    user_msg.text = "/spy"

    with patch.object(telegram_bot.bot, "reply_to") as mock_reply:
        handle_spy_command(user_msg)
        mock_reply.assert_called_once()
        assert "только у администратора" in mock_reply.call_args[0][1]

    assert get_admin_state().get("live_spy_enabled") is False

    # 3. При выключенном шпионе notify_admins_about_job не отправляет уведомлений
    job_meta = {
        "job_id": "j_spy_test",
        "user_id": user_id,
        "orig_stem": "test",
        "pricing": {},
    }
    prev_png = telegram_bot.JOBS_DIR / "prev.png"
    prev_png.write_bytes(b"fake_png")

    with patch.object(telegram_bot.bot, "send_photo") as mock_photo, \
         patch.object(telegram_bot.bot, "send_message") as mock_msg:
        notify_admins_about_job(user_id, job_meta, prev_png, "Новый макет")
        mock_photo.assert_not_called()
        mock_msg.assert_not_called()

    # 4. Админ включает шпиона через /spy on
    admin_msg = MagicMock()
    admin_msg.from_user.id = admin_id
    admin_msg.from_user.first_name = "Admin"
    admin_msg.from_user.last_name = None
    admin_msg.from_user.username = "admin"
    admin_msg.text = "/spy on"

    with patch.object(telegram_bot.bot, "reply_to") as mock_reply:
        handle_spy_command(admin_msg)
        assert "ВКЛЮЧЕН" in mock_reply.call_args[0][1]

    assert get_admin_state().get("live_spy_enabled") is True

    # 5. При включённом шпионе notify_admins_about_job отправляет уведомление админу
    with patch.object(telegram_bot.bot, "send_photo") as mock_photo, \
         patch.object(telegram_bot.bot, "send_message") as mock_msg:
        notify_admins_about_job(user_id, job_meta, prev_png, "Новый макет")
        assert mock_photo.called or mock_msg.called

    # 6. Админ выключает шпиона через /spy off
    admin_msg.text = "/spy off"
    with patch.object(telegram_bot.bot, "reply_to") as mock_reply:
        handle_spy_command(admin_msg)
        assert "ВЫКЛЮЧЕН" in mock_reply.call_args[0][1]

    assert get_admin_state().get("live_spy_enabled") is False

    # 7. Админ переключает шпиона через callback adm:toggle_spy
    call = MagicMock()
    call.id = "call_spy_toggle"
    call.data = "adm:toggle_spy"
    call.from_user.id = admin_id
    call.message.chat.id = admin_id
    call.message.message_id = 101

    with patch.object(telegram_bot.bot, "edit_message_text") as mock_edit, \
         patch.object(telegram_bot.bot, "answer_callback_query") as mock_ans:
        handle_admin_callback(call)
        mock_ans.assert_called_once()
        assert "ВКЛЮЧЕНЫ" in mock_ans.call_args[0][1]
        assert mock_edit.called

    assert get_admin_state().get("live_spy_enabled") is True


def test_cleanup_old_jobs_retention_and_scheduler():
    """cleanup_old_jobs удаляет задания старше JOB_RETENTION_HOURS и сохраняет свежие."""
    now = time.time()

    # 1. Создаём старое задание (старше 168 часов)
    old_job = telegram_bot.JOBS_DIR / "old_job"
    old_job.mkdir()
    (old_job / "meta.json").write_text("{}", encoding="utf-8")
    old_mtime = now - (JOB_RETENTION_HOURS + 10) * 3600.0
    os.utime(old_job, (old_mtime, old_mtime))

    # 2. Создаём свежее задание (1 час назад)
    fresh_job = telegram_bot.JOBS_DIR / "fresh_job"
    fresh_job.mkdir()
    (fresh_job / "meta.json").write_text("{}", encoding="utf-8")
    fresh_mtime = now - 3600.0
    os.utime(fresh_job, (fresh_mtime, fresh_mtime))

    # 3. Запускаем очистку
    deleted = cleanup_old_jobs(max_age_hours=JOB_RETENTION_HOURS)
    assert deleted == 1
    assert not old_job.exists()
    assert fresh_job.exists()

    # 4. Проверяем работу run_cleanup_loop со stop_event
    stop_event = threading.Event()
    stop_event.set()
    # Цикл должен завершиться сразу, так как stop_event установлен
    run_cleanup_loop(interval_seconds=0.01, stop_event=stop_event)

    # 5. Проверяем старт планировщика start_cleanup_scheduler
    th = start_cleanup_scheduler(interval_seconds=3600.0)
    assert th.is_alive()
    assert th.daemon is True


def test_handle_media_does_not_synchronously_call_cleanup():
    """handle_media не должен вызывать cleanup_old_jobs синхронно при каждом входящем файле."""
    msg = MagicMock()
    msg.content_type = "photo"
    msg.from_user.id = 999999
    msg.from_user.first_name = "User"
    msg.from_user.last_name = None
    msg.from_user.username = None
    msg.chat.id = 999999
    photo_mock = MagicMock()
    photo_mock.file_size = 1000
    photo_mock.file_id = "fake_file_id"
    msg.photo = [photo_mock]

    with patch("telegram_bot.cleanup_old_jobs") as mock_cleanup, \
         patch.object(telegram_bot.bot, "get_file", side_effect=Exception("stop_here")), \
         patch.object(telegram_bot.bot, "reply_to"):
        try:
            handle_media(msg)
        except Exception:
            pass
        mock_cleanup.assert_not_called()
