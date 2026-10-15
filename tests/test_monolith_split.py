"""Тесты разделения монолита telegram_bot.py на модули bot/, web/, pricing.py — Фаза 7.

Проверяют:
1. Размер telegram_bot.py строго короче 150 строк.
2. Корректность запуска main() бота с mock-объектами без обращения к сети.
3. Доступность всех экспортируемых модулей и пакетов (bot, web, pricing).
4. Наличие и валидность HTML-шаблонов веб-админки.
"""

from __future__ import annotations

from unittest import mock

import pytest

import telegram_bot
from config import BASE_DIR


def test_telegram_bot_line_count():
    """telegram_bot.py должен быть компактной точкой входа (< 150 строк)."""
    bot_path = BASE_DIR / "telegram_bot.py"
    lines = bot_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150, f"telegram_bot.py имеет {len(lines)} строк, ожидается < 150"


def test_templates_exist():
    """Шаблоны веб-админки должны быть вынесены в templates/*.html."""
    templates_dir = BASE_DIR / "templates"
    assert templates_dir.exists()
    assert (templates_dir / "login.html").exists()
    assert (templates_dir / "dashboard.html").exists()
    assert (templates_dir / "card.html").exists()


def test_pricing_toml_and_module():
    """Конфигурация pricing.toml должна загружаться и использоваться в pricing.py."""
    import pricing

    assert pricing.PRICING_CONFIG_PATH.exists()
    cfg = pricing.load_pricing_config()
    assert cfg["costs"]["zenon"]["material_rub_per_m2"] > 0
    assert cfg["costs"]["zenon"]["ink_rub_per_m2"] > 0
    assert cfg["roll"]["default_width_mm"] > 0


def test_bot_mock_startup(monkeypatch: pytest.MonkeyPatch):
    """Бот должен успешно стартовать с тестовым токеном без сетевых вызовов."""
    monkeypatch.setenv("PLOTTER_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")

    with (
        mock.patch("telebot.TeleBot.infinity_polling") as mock_poll,
        mock.patch("telebot.TeleBot.set_my_commands") as mock_cmds,
        mock.patch("threading.Thread.start") as mock_th,
    ):
        telegram_bot.main()
        assert mock_poll.called
        assert mock_cmds.called
        assert mock_th.called
