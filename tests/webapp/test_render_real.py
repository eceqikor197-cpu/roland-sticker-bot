"""«Получить EPS» из редактора проходит через настоящий process_and_send_job (Telegram замокан)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import telegram_bot
from miniapp.auth import sign_init_data
from miniapp.server import create_app
from storage import get_job

TOKEN = "123456:test-token"


def test_render_with_real_converter(job_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    job_dir = job_factory("jobr", 3003)
    for name in ("send_photo", "send_document", "send_message", "delete_message"):
        monkeypatch.setattr(telegram_bot.bot, name, MagicMock())
    monkeypatch.setattr(telegram_bot, "notify_admins_about_job", MagicMock())
    init = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 3003})}, TOKEN)
    app = create_app(bot_token=TOKEN, jobs_dir=job_factory.jobs_dir, admin_ids=set(), deliver=telegram_bot.process_and_send_job)
    client = TestClient(app)
    hdr = {"X-Telegram-Init-Data": init}

    quote = client.post("/api/v1/jobs/jobr/quote", json={"size_mm": 110, "fit_a4_sheet": True}, headers=hdr).json()
    r = client.post("/api/v1/jobs/jobr/render", json={"size_mm": 110, "fit_a4_sheet": True}, headers=hdr)
    assert r.status_code == 200, r.text

    job = get_job("jobr")
    assert job["pricing"]["a4_fit_qty"] == quote["pricing"]["a4_fit_qty"]
    assert job["num_contours"] == quote["pricing"]["a4_fit_qty"]
    assert job["width_mm"] <= 297.5 and job["height_mm"] <= 297.5
    assert list(job_dir.glob("*.eps"))
    telegram_bot.bot.send_document.assert_called_once()
    assert telegram_bot.bot.send_document.call_args.args[0] == 3003
