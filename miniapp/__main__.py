"""Запуск сервера мини-приложения: python -m miniapp."""

from __future__ import annotations

import logging

import uvicorn

from config import MINIAPP_HOST, MINIAPP_MENU_BUTTON, MINIAPP_PORT, MINIAPP_URL, PLOTTER_BOT_TOKEN
from miniapp.server import create_app

logger = logging.getLogger("miniapp")


def set_menu_button() -> None:
    """Кнопка меню бота «Редактор» (слева от поля ввода) открывает список макетов в мини-апке."""
    if not (MINIAPP_URL and MINIAPP_MENU_BUTTON and PLOTTER_BOT_TOKEN):
        return
    try:
        import telebot
        from telebot.types import MenuButtonWebApp, WebAppInfo

        telebot.TeleBot(PLOTTER_BOT_TOKEN).set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Редактор", web_app=WebAppInfo(url=f"{MINIAPP_URL}/"))
        )
        logger.info("Кнопка меню бота указывает на %s", MINIAPP_URL)
    except Exception as exc:
        logger.warning("Не удалось поставить кнопку меню бота: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not MINIAPP_URL:
        logger.warning("MINIAPP_URL не задан или не https://: бот не покажет кнопки редактора")
    set_menu_button()
    uvicorn.run(create_app(), host=MINIAPP_HOST, port=MINIAPP_PORT, proxy_headers=True, log_level="info")


if __name__ == "__main__":
    main()
