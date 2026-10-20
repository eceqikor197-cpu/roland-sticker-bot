"""Проверка подписи initData Telegram Mini App."""

from __future__ import annotations

import json
import time

import pytest

from miniapp.auth import InitDataError, sign_init_data, validate_init_data

TOKEN = "123456:test-token"


def _init(user_id: int = 42, auth_date: float | None = None, token: str = TOKEN) -> str:
    return sign_init_data(
        {
            "auth_date": str(int(auth_date if auth_date is not None else time.time())),
            "query_id": "AAH",
            "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
        },
        token,
    )


def test_valid_init_data_returns_user() -> None:
    user = validate_init_data(_init(77), TOKEN)
    assert user["id"] == 77
    assert user["first_name"] == "Тест"


def test_signed_by_other_bot_is_rejected() -> None:
    with pytest.raises(InitDataError):
        validate_init_data(_init(token="999:other"), TOKEN)


def test_tampered_user_is_rejected() -> None:
    data = _init(42).replace("%22id%22%3A+42", "%22id%22%3A+43")
    assert data != _init(42)
    with pytest.raises(InitDataError):
        validate_init_data(data, TOKEN)


def test_expired_init_data_is_rejected() -> None:
    with pytest.raises(InitDataError):
        validate_init_data(_init(auth_date=time.time() - 2 * 86400), TOKEN)


@pytest.mark.parametrize("data", ["", "auth_date=1", "hash=abc"])
def test_garbage_is_rejected(data: str) -> None:
    with pytest.raises(InitDataError):
        validate_init_data(data, TOKEN)
