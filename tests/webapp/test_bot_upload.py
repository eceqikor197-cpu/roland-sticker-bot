"""Загрузка фото в режиме мини-апки: без конвертации, с кнопкой редактора; «⚡ EPS» запускает конвертацию."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import bot.keyboards as kb_mod
import telegram_bot
from telegram_bot import ConversionLimiter, handle_job_callback, handle_media


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(telegram_bot, "JOBS_DIR", jobs)
    monkeypatch.setattr(telegram_bot, "conversion_limiter", ConversionLimiter(max_per_minute=6, max_concurrent_global=2))
    monkeypatch.setattr(kb_mod, "_get_miniapp_url", lambda: "https://editor.example.ts.net")
    return jobs


def _photo_message(user_id: int = 11111) -> MagicMock:
    msg = MagicMock()
    msg.content_type = "photo"
    msg.from_user.id = user_id
    msg.from_user.first_name = "Test"
    msg.from_user.last_name = "User"
    msg.from_user.username = "testuser"
    msg.chat.id = user_id
    photo = MagicMock()
    photo.file_size = 1000
    photo.file_id = "ph1"
    msg.photo = [photo]
    return msg


def test_upload_saves_job_and_offers_editor(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = MagicMock()
    process = MagicMock()
    monkeypatch.setattr(telegram_bot.bot, "reply_to", reply)
    monkeypatch.setattr(telegram_bot.bot, "get_file", MagicMock(return_value=MagicMock(file_path="p")))
    monkeypatch.setattr(telegram_bot.bot, "download_file", MagicMock(return_value=b"\xff\xd8fakejpeg"))
    monkeypatch.setattr(telegram_bot, "process_and_send_job", process)

    handle_media(_photo_message())

    process.assert_not_called()
    jobs = list(isolated.iterdir())
    assert len(jobs) == 1
    meta = json.loads((jobs[0] / "meta.json").read_text(encoding="utf-8"))
    assert meta["user_id"] == 11111 and (jobs[0] / meta["input_filename"]).exists()
    kb = reply.call_args.kwargs["reply_markup"]
    assert kb.keyboard[0][0].web_app.url == f"https://editor.example.ts.net/?job={jobs[0].name}"
    assert kb.keyboard[1][0].callback_data == f"job:{jobs[0].name}:quick:1"


def test_quick_button_runs_conversion(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_dir = isolated / "jobq"
    job_dir.mkdir()
    meta = {"job_id": "jobq", "user_id": 11111, "orig_stem": "s", "input_filename": "input.png",
            "created_at": time.time(), "updated_at": time.time()}
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (job_dir / "input.png").write_bytes(b"x")
    process = MagicMock()
    monkeypatch.setattr(telegram_bot, "process_and_send_job", process)
    monkeypatch.setattr(telegram_bot.bot, "answer_callback_query", MagicMock())
    monkeypatch.setattr(telegram_bot.bot, "send_message", MagicMock(return_value=MagicMock(message_id=5)))

    call = MagicMock()
    call.id = "c1"
    call.data = "job:jobq:quick:1"
    call.from_user.id = 11111
    call.from_user.first_name = "Test"
    call.from_user.last_name = "User"
    call.from_user.username = "testuser"
    call.message.chat.id = 11111
    handle_job_callback(call)

    process.assert_called_once()
    assert process.call_args.kwargs["action_title"] == "EPS по последним настройкам"
