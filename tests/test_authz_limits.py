"""Тесты авторизации, проверки владельца, лимитов и режима согласования — Фаза 3.

Проверяют:
1. Проверку владельца во всех callback'ах job:* (пользователь B получает «⛔ Это не ваш макет», админ может всё).
2. Режим «заявка» для отправки в Hot Folder обычными пользователями и подтверждение/отклонение админом (ALLOW_USER_HOT_FOLDER).
3. Защиту PIL от декомпрессионных бомб (MAX_IMAGE_PIXELS, DecompressionBombError).
4. Ограничения размера входного файла (MAX_UPLOAD_MB), габаритов макета (MAX_SIZE_MM) и растяжения (MIN_STRETCH–MAX_STRETCH).
5. Лимиты частоты (6 в минуту), одновременных конвертаций (1 на пользователя, глобальный семафор) и отображение «в очереди».
6. Скрытие текста исключений от обычных пользователей («Не удалось обработать файл») и показ деталей админу.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL.Image import DecompressionBombError

import telegram_bot
from telegram_bot import (
    ADMIN_IDS,
    MAX_CONCURRENT_CONVERSIONS,
    MAX_CONVERSIONS_PER_MINUTE,
    MAX_SIZE_MM,
    MAX_UPLOAD_MB,
    MIN_STRETCH,
    MAX_STRETCH,
    ConversionLimiter,
    handle_custom_size_text,
    handle_hot_folder_request_callback,
    handle_job_callback,
    handle_media,
    process_and_send_job,
)


@pytest.fixture(autouse=True)
def isolate_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует каталоги заданий и Hot Folder во временную директорию tmp_path."""
    test_jobs_dir = tmp_path / "jobs"
    test_jobs_dir.mkdir(parents=True, exist_ok=True)
    test_hot_eps = tmp_path / "Roland_HotFolder" / "Queue_EPS"
    test_hot_eps.mkdir(parents=True, exist_ok=True)
    test_hot_prev = tmp_path / "Roland_HotFolder" / "Previews"
    test_hot_prev.mkdir(parents=True, exist_ok=True)
    test_prefs = tmp_path / "user_prefs.json"
    test_users = tmp_path / "users_db.json"

    monkeypatch.setattr(telegram_bot, "JOBS_DIR", test_jobs_dir)
    monkeypatch.setattr(telegram_bot, "HOT_QUEUE_EPS_DIR", test_hot_eps)
    monkeypatch.setattr(telegram_bot, "HOT_PREVIEWS_DIR", test_hot_prev)
    monkeypatch.setattr(telegram_bot, "PREFS_FILE", test_prefs)
    monkeypatch.setattr(telegram_bot, "USERS_FILE", test_users)

    # Сбрасываем лимитер конвертаций перед каждым тестом
    telegram_bot.conversion_limiter = ConversionLimiter(
        max_per_minute=MAX_CONVERSIONS_PER_MINUTE,
        max_concurrent_global=MAX_CONCURRENT_CONVERSIONS,
    )


def create_dummy_job(job_id: str, user_id: int, orig_stem: str = "test_sticker") -> Path:
    """Создаёт тестовое задание с метаданными и фиктивным .eps файлом."""
    jdir = telegram_bot.JOBS_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "job_id": job_id,
        "user_id": user_id,
        "orig_stem": orig_stem,
        "input_filename": "input.png",
        "created_at": time.time(),
        "updated_at": time.time(),
        "width_mm": 100.0,
        "height_mm": 100.0,
        "size_mm": 100.0,
        "nesting_qty": 1,
    }
    (jdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (jdir / f"{orig_stem}.eps").write_text("%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n", encoding="utf-8")
    (jdir / "preview_cutcontour.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return jdir


def make_callback_query(call_data: str, user_id: int, username: str = "testuser") -> MagicMock:
    """Создаёт фиктивный CallbackQuery."""
    call = MagicMock()
    call.id = "call_123"
    call.data = call_data
    call.from_user.id = user_id
    call.from_user.first_name = "Test"
    call.from_user.last_name = "User"
    call.from_user.username = username
    call.message.chat.id = user_id
    call.message.message_id = 999
    return call


def make_message(text: str = "", user_id: int = 11111, content_type: str = "text") -> MagicMock:
    """Создаёт фиктивное Message."""
    msg = MagicMock()
    msg.text = text
    msg.content_type = content_type
    msg.from_user.id = user_id
    msg.from_user.first_name = "Test"
    msg.from_user.last_name = "User"
    msg.from_user.username = "testuser"
    msg.chat.id = user_id
    msg.message_id = 888
    return msg


# ==============================================================================
# 1. ПРОВЕРКА ВЛАДЕЛЬЦА В CALLBACK'АХ
# ==============================================================================


def test_user_b_cannot_access_user_a_job(monkeypatch: pytest.MonkeyPatch):
    """Пользователь B не может изменить макет пользователя A: ответ «⛔ Это не ваш макет»."""
    create_dummy_job(job_id="job_a", user_id=11111)

    mock_answer = MagicMock()
    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)

    # Пользователь B (22222) пытается нажать различные кнопки на макете A
    actions = ["hot:1", "sz:150", "dsz:10", "stx:1.1", "nav:main", "noop:0"]
    for act in actions:
        call = make_callback_query(f"job:job_a:{act}", user_id=22222)
        handle_job_callback(call)

        mock_answer.assert_called_with("call_123", "⛔ Это не ваш макет", show_alert=True)
        mock_answer.reset_mock()

    # Убеждаемся, что в Hot Folder ничего не скопировано
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 0


def test_admin_can_access_any_user_job(monkeypatch: pytest.MonkeyPatch):
    """Администратор может управлять макетом любого пользователя."""
    create_dummy_job(job_id="job_a", user_id=11111)

    admin_id = next(iter(ADMIN_IDS))
    mock_answer = MagicMock()
    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)

    # Админ отправляет в Hot Folder
    call = make_callback_query("job:job_a:hot:1", user_id=admin_id)
    handle_job_callback(call)

    # Проверка владельца прошла, задача отправлена
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 1
    mock_answer.assert_called_with("call_123", "🚀 Отправлено в Hot Folder плоттера!")


def test_owner_can_access_own_job(monkeypatch: pytest.MonkeyPatch):
    """Владелец макета может переключать вкладки и управлять им."""
    create_dummy_job(job_id="job_a", user_id=11111)

    mock_answer = MagicMock()
    mock_edit_markup = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "edit_message_reply_markup", mock_edit_markup)

    call = make_callback_query("job:job_a:nav:cut", user_id=11111)
    handle_job_callback(call)

    # Не должно быть ошибки "не ваш макет"
    calls = [c[0][1] for c in mock_answer.call_args_list if len(c[0]) > 1]
    assert "⛔ Это не ваш макет" not in calls


# ==============================================================================
# 2. РЕЖИМ «ЗАЯВКА» В HOT FOLDER И СОГЛАСОВАНИЕ АДМИНОМ
# ==============================================================================


def test_user_hot_folder_approval_workflow(monkeypatch: pytest.MonkeyPatch):
    """Обычный пользователь создаёт заявку на печать, админ подтверждает."""
    create_dummy_job(job_id="job_user", user_id=11111)
    admin_id = next(iter(ADMIN_IDS))

    # ALLOW_USER_HOT_FOLDER = False по умолчанию
    monkeypatch.setattr(telegram_bot, "ALLOW_USER_HOT_FOLDER", False)

    mock_answer = MagicMock()
    mock_send = MagicMock()
    mock_edit_text = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)
    monkeypatch.setattr(telegram_bot.bot, "edit_message_text", mock_edit_text)

    # 1. Обычный пользователь нажимает кнопку отправки в Hot Folder
    call = make_callback_query("job:job_user:hot:1", user_id=11111)
    handle_job_callback(call)

    # Файл пока НЕ должен быть в Hot Folder
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 0

    # Метаданные должны обновиться на статус "pending"
    meta = json.loads((telegram_bot.JOBS_DIR / "job_user" / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("hot_folder_request_status") == "pending"

    # Админ должен получить уведомление с кнопками
    admin_calls = [c for c in mock_send.call_args_list if c[0][0] == admin_id]
    assert len(admin_calls) >= 1
    assert "Заявка на отправку в Hot Folder" in admin_calls[0][0][1]

    # 2. Повторное нажатие пользователем сообщает, что заявка уже ожидает
    call2 = make_callback_query("job:job_user:hot:1", user_id=11111)
    handle_job_callback(call2)
    mock_answer.assert_called_with("call_123", "Заявка уже ожидает подтверждения администратором", show_alert=True)

    # 3. Не-администратор пытается подтвердить заявку -> отказ
    non_admin_call = make_callback_query("hreq:job_user:approve", user_id=22222)
    handle_hot_folder_request_callback(non_admin_call)
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 0

    # 4. Администратор подтверждает заявку
    admin_call = make_callback_query("hreq:job_user:approve", user_id=admin_id)
    handle_hot_folder_request_callback(admin_call)

    # Файл скопирован в Hot Folder!
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 1
    meta_approved = json.loads((telegram_bot.JOBS_DIR / "job_user" / "meta.json").read_text(encoding="utf-8"))
    assert meta_approved.get("hot_folder_request_status") == "approved"

    # Пользователю отправлено уведомление об одобрении
    user_calls = [c for c in mock_send.call_args_list if c[0][0] == 11111 and "Администратор одобрил" in c[0][1]]
    assert len(user_calls) >= 1


def test_user_hot_folder_rejection_workflow(monkeypatch: pytest.MonkeyPatch):
    """Администратор отклоняет заявку на печать в Hot Folder."""
    create_dummy_job(job_id="job_rej", user_id=11111)
    admin_id = next(iter(ADMIN_IDS))

    monkeypatch.setattr(telegram_bot, "ALLOW_USER_HOT_FOLDER", False)
    mock_answer = MagicMock()
    mock_send = MagicMock()
    mock_edit_text = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)
    monkeypatch.setattr(telegram_bot.bot, "edit_message_text", mock_edit_text)

    # Пользователь подает заявку
    handle_job_callback(make_callback_query("job:job_rej:hot:1", user_id=11111))

    # Админ отклоняет
    handle_hot_folder_request_callback(make_callback_query("hreq:job_rej:reject", user_id=admin_id))

    # В Hot Folder ничего нет
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 0
    meta = json.loads((telegram_bot.JOBS_DIR / "job_rej" / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("hot_folder_request_status") == "rejected"

    # Пользователь оповещен об отклонении
    user_calls = [c for c in mock_send.call_args_list if c[0][0] == 11111 and "отклонил" in c[0][1]]
    assert len(user_calls) >= 1


def test_allow_user_hot_folder_flag(monkeypatch: pytest.MonkeyPatch):
    """При ALLOW_USER_HOT_FOLDER = True обычный пользователь отправляет без заявки."""
    create_dummy_job(job_id="job_direct", user_id=11111)
    monkeypatch.setattr(telegram_bot, "ALLOW_USER_HOT_FOLDER", True)

    mock_answer = MagicMock()
    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)

    call = make_callback_query("job:job_direct:hot:1", user_id=11111)
    handle_job_callback(call)

    # Файл скопирован напрямую
    assert len(list(telegram_bot.HOT_QUEUE_EPS_DIR.iterdir())) == 1
    mock_answer.assert_called_with("call_123", "🚀 Отправлено в Hot Folder плоттера!")


# ==============================================================================
# 3. ЗАЩИТА PIL (MAX_IMAGE_PIXELS И DecompressionBombError)
# ==============================================================================


def test_pil_max_image_pixels_set():
    """Проверка, что MAX_IMAGE_PIXELS установлен в 60_000_000 по умолчанию."""
    from PIL import Image
    import jpeg2eps

    assert Image.MAX_IMAGE_PIXELS == 60_000_000
    assert jpeg2eps.MAX_IMAGE_PIXELS == 60_000_000


def test_decompression_bomb_error_handling(monkeypatch: pytest.MonkeyPatch):
    """При возникновении DecompressionBombError пользователю отдаётся понятная ошибка."""
    jdir = create_dummy_job("bomb_job", user_id=11111)

    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)

    def fake_convert(*args, **kwargs):
        raise DecompressionBombError("Image size exceeds limit")

    monkeypatch.setattr(telegram_bot, "convert_image_to_plotter_eps", fake_convert)

    process_and_send_job(
        chat_id=11111,
        user_id=11111,
        job_dir=jdir,
        status_msg_id=None,
    )

    # Пользователю отправлено понятное сообщение
    assert len(mock_send.call_args_list) >= 1
    msg_text = mock_send.call_args_list[0][0][1]
    assert "превышен лимит пикселей" in msg_text or "слишком большой" in msg_text


# ==============================================================================
# 4. ОГРАНИЧЕНИЯ (MAX_UPLOAD_MB, MAX_SIZE_MM, РАСТЯЖЕНИЕ)
# ==============================================================================


def test_max_upload_size_photo(monkeypatch: pytest.MonkeyPatch):
    """Превышение лимита размера для сжатого фото отклоняется до загрузки."""
    mock_reply = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "reply_to", mock_reply)

    msg = make_message(content_type="photo", user_id=11111)
    fake_photo = MagicMock()
    fake_photo.file_size = int((MAX_UPLOAD_MB + 1) * 1024 * 1024)
    fake_photo.file_id = "photo_large_id"
    msg.photo = [fake_photo]

    handle_media(msg)

    mock_reply.assert_called_once()
    assert "Файл слишком большой" in mock_reply.call_args[0][1]


def test_max_upload_size_document(monkeypatch: pytest.MonkeyPatch):
    """Превышение лимита размера для документа отклоняется до загрузки."""
    mock_reply = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "reply_to", mock_reply)

    msg = make_message(content_type="document", user_id=11111)
    fake_doc = MagicMock()
    fake_doc.file_name = "test.png"
    fake_doc.file_size = int((MAX_UPLOAD_MB + 5) * 1024 * 1024)
    fake_doc.file_id = "doc_large_id"
    msg.document = fake_doc

    handle_media(msg)

    mock_reply.assert_called_once()
    assert "Файл слишком большой" in mock_reply.call_args[0][1]


def test_max_size_mm_clamped_on_text_input(monkeypatch: pytest.MonkeyPatch):
    """Метровые размеры больше ширины рулона принимаются, а совсем гигантские ограничиваются MAX_SIZE_MM (100 м)."""
    mock_reply = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "reply_to", mock_reply)

    # 1. Одиночный размер '2.5 м' больше 1370 мм и принимается как есть
    msg = make_message(text="2.5 м", user_id=11111)
    handle_custom_size_text(msg)
    assert telegram_bot.get_user_prefs(11111)["size_mm"] == 2500.0

    # 2. Прямоугольный габарит '2x3 м' — длинная сторона 3 м
    msg2 = make_message(text="2x3 м", user_id=11111)
    handle_custom_size_text(msg2)
    assert telegram_bot.get_user_prefs(11111)["size_mm"] == 3000.0

    # 3. Явно нереальный размер упирается в MAX_SIZE_MM
    msg3 = make_message(text="2000 м", user_id=11111)
    handle_custom_size_text(msg3)
    assert telegram_bot.get_user_prefs(11111)["size_mm"] == MAX_SIZE_MM


def test_zero_size_text_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """Ноль не считается размером: настройка не меняется."""
    from bot.handlers_admin import parse_size_text

    assert parse_size_text("0 см") == ()
    assert parse_size_text("0x5 см") == ()
    assert parse_size_text("-5 см") == ()
    assert parse_size_text("abc") == ()
    assert parse_size_text("1 мм") == (1.0,)

    monkeypatch.setattr(telegram_bot.bot, "reply_to", MagicMock())
    telegram_bot.update_user_prefs(11111, size_mm=80.0)
    handle_custom_size_text(make_message(text="0 см", user_id=11111))
    assert telegram_bot.get_user_prefs(11111)["size_mm"] == 80.0


def test_clamp_finite_rejects_garbage():
    """Не число, nan и бесконечность заменяются значением по умолчанию."""
    from config import clamp_finite

    assert clamp_finite("abc", 1.0, 10.0, 5.0) == 5.0
    assert clamp_finite(None, 1.0, 10.0, 5.0) == 5.0
    assert clamp_finite("nan", 1.0, 10.0, 5.0) == 5.0
    assert clamp_finite("inf", 1.0, 10.0, 5.0) == 5.0
    assert clamp_finite(-3, 1.0, 10.0, 5.0) == 1.0
    assert clamp_finite("2.5", 1.0, 10.0, 5.0) == 2.5


def test_stretch_limits(monkeypatch: pytest.MonkeyPatch):
    """Коэффициенты растяжения ограничены диапазоном MIN_STRETCH–MAX_STRETCH."""
    create_dummy_job("stretch_job", user_id=11111)
    mock_answer = MagicMock()
    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", mock_answer)
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)
    monkeypatch.setattr(telegram_bot, "process_and_send_job", MagicMock())

    # Начальное значение 1.0, пытаемся многократно увеличивать
    telegram_bot.update_user_prefs(11111, stretch_x=MAX_STRETCH * 0.95, stretch_y=MIN_STRETCH * 1.5)

    # Увеличение ширины выше MAX_STRETCH
    call_inc = make_callback_query("job:stretch_job:stx:1.25", user_id=11111)
    handle_job_callback(call_inc)
    prefs = telegram_bot.get_user_prefs(11111)
    assert prefs["stretch_x"] <= MAX_STRETCH

    # Уменьшение высоты ниже MIN_STRETCH
    call_dec = make_callback_query("job:stretch_job:sty:0.5", user_id=11111)
    handle_job_callback(call_dec)
    prefs = telegram_bot.get_user_prefs(11111)
    assert prefs["stretch_y"] >= MIN_STRETCH


# ==============================================================================
# 5. ЛИМИТЫ ЧАСТОТЫ И ПАРАЛЛЕЛЬНОСТИ
# ==============================================================================


def test_rate_limit_per_user():
    """Лимит частоты: не более 6 конвертаций на пользователя в минуту."""
    limiter = ConversionLimiter(max_per_minute=6, max_concurrent_global=2)
    user_id = 99999

    # Выполняем 6 успешных конвертаций
    for _ in range(6):
        can, reason = limiter.check_and_acquire_user_slot(user_id)
        assert can is True
        assert reason == "ok"
        limiter.release_user_slot(user_id, record_completed=True)

    # 7-я конвертация в ту же минуту должна быть отклонена
    can, reason = limiter.check_and_acquire_user_slot(user_id)
    assert can is False
    assert reason == "rate_limit"


def test_concurrent_limit_per_user():
    """Лимит одновременности: не более 1 конвертации на пользователя."""
    limiter = ConversionLimiter(max_per_minute=6, max_concurrent_global=2)
    user_id = 99999

    can1, reason1 = limiter.check_and_acquire_user_slot(user_id)
    assert can1 is True

    # Вторая конвертация, пока первая ещё не завершена
    can2, reason2 = limiter.check_and_acquire_user_slot(user_id)
    assert can2 is False
    assert reason2 == "concurrent"

    # Завершаем первую
    limiter.release_user_slot(user_id, record_completed=True)

    # Теперь можно начать новую
    can3, reason3 = limiter.check_and_acquire_user_slot(user_id)
    assert can3 is True
    limiter.release_user_slot(user_id, record_completed=True)


def test_global_concurrency_semaphore_queue():
    """Глобальный семафор (по умолчанию 2): третий запрос встаёт в очередь."""
    limiter = ConversionLimiter(max_per_minute=10, max_concurrent_global=2)
    queued_events = []

    def run_worker(uid: int, hold_sec: float):
        can, _ = limiter.check_and_acquire_user_slot(uid)
        if not can:
            return
        def on_q():
            queued_events.append(uid)

        with limiter.run_conversion_slot(uid, on_queued=on_q):
            time.sleep(hold_sec)

    t1 = threading.Thread(target=run_worker, args=(1, 0.2))
    t2 = threading.Thread(target=run_worker, args=(2, 0.2))
    t3 = threading.Thread(target=run_worker, args=(3, 0.1))

    t1.start()
    t2.start()
    time.sleep(0.05)  # даём занять 2 глобальных слота
    t3.start()

    t1.join()
    t2.join()
    t3.join()

    # Пользователь 3 должен был получить уведомление об очереди
    assert 3 in queued_events


# ==============================================================================
# 6. СКРЫТИЕ ТЕКСТА ИСКЛЮЧЕНИЙ
# ==============================================================================


def test_exception_masking_for_regular_user_vs_admin(monkeypatch: pytest.MonkeyPatch):
    """Обычный пользователь получает 'Не удалось обработать файл', админ — детали исключения."""
    jdir = create_dummy_job("err_job", user_id=11111)
    admin_id = next(iter(ADMIN_IDS))

    mock_send = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "send_message", mock_send)

    def failing_convert(*args, **kwargs):
        raise ValueError("CriticalInternalDbOrLibraryFailure: detail XYZ")

    monkeypatch.setattr(telegram_bot, "convert_image_to_plotter_eps", failing_convert)

    # 1. Запуск для обычного пользователя (11111)
    process_and_send_job(
        chat_id=11111,
        user_id=11111,
        job_dir=jdir,
        status_msg_id=None,
    )
    user_msg = mock_send.call_args_list[-1][0][1]
    assert "Не удалось обработать файл" in user_msg
    assert "CriticalInternalDbOrLibraryFailure" not in user_msg

    # 2. Запуск для администратора
    process_and_send_job(
        chat_id=admin_id,
        user_id=admin_id,
        job_dir=jdir,
        status_msg_id=None,
    )
    admin_msg = mock_send.call_args_list[-1][0][1]
    assert "CriticalInternalDbOrLibraryFailure" in admin_msg
