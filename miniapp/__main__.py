"""Запуск сервера мини-приложения: python -m miniapp."""

from __future__ import annotations

import logging
import os

import uvicorn

from miniapp.server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.getenv("MINIAPP_HOST", "127.0.0.1")
    port = int(os.getenv("MINIAPP_PORT", "8096"))
    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, log_level="info")


if __name__ == "__main__":
    main()
