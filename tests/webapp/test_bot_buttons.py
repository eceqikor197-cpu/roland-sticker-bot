"""Кнопки бота в режиме мини-апки и без него."""

from __future__ import annotations

import pytest

import bot.keyboards as kb_mod
from bot.keyboards import build_job_keyboard, build_upload_keyboard


def _rows(kb):
    return [[(b.text, b.callback_data, b.web_app.url if b.web_app else None) for b in row] for row in kb.keyboard]


@pytest.fixture
def miniapp_on(monkeypatch):
    monkeypatch.setattr(kb_mod, "_get_miniapp_url", lambda: "https://editor.example.ts.net")


def test_without_miniapp_keyboard_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(kb_mod, "_get_miniapp_url", lambda: "")
    rows = _rows(build_job_keyboard("abc", {}))
    assert rows[0][0][1] == "job:abc:nav:size"
    assert all(web is None for row in rows for _, _, web in row)


def test_main_menu_is_short_with_miniapp(miniapp_on) -> None:
    rows = _rows(build_job_keyboard("abc", {}))
    assert rows[0][0][2] == "https://editor.example.ts.net/?job=abc"
    assert rows[1][0][1] == "job:abc:hot:1"
    assert rows[2][0][1] == "job:abc:nav:classic"
    assert len(rows) == 3


def test_classic_menu_and_back_buttons(miniapp_on) -> None:
    classic = _rows(build_job_keyboard("abc", {}, menu_tab="classic"))
    assert classic[0][0][1] == "job:abc:nav:size"
    assert classic[-1][0][1] == "job:abc:nav:main"
    size = _rows(build_job_keyboard("abc", {}, menu_tab="size"))
    assert size[-1][0][1] == "job:abc:nav:classic"


def test_upload_keyboard(miniapp_on) -> None:
    rows = _rows(build_upload_keyboard("abc"))
    assert rows[0][0][2] == "https://editor.example.ts.net/?job=abc"
    assert rows[1][0][1] == "job:abc:quick:1"
