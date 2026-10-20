"""Проверка подписи initData Telegram Mini App.

Алгоритм из документации Telegram (Validating data received via the Mini App):
secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token),
hash = hex(HMAC_SHA256(key=secret_key, msg=data_check_string)),
где data_check_string — все поля кроме hash, отсортированные по ключу, в виде key=value через \\n.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Dict, Optional

DEFAULT_MAX_AGE_SEC = 24 * 3600


class InitDataError(ValueError):
    """initData не прошла проверку."""


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def sign_init_data(fields: Dict[str, str], bot_token: str) -> str:
    """Подписывает поля как это делает Telegram. Нужна для тестов и локальной отладки."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret_key(bot_token), check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": digest})


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_sec: int = DEFAULT_MAX_AGE_SEC,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Проверяет подпись и срок initData, возвращает объект user из неё."""
    if not init_data or not bot_token:
        raise InitDataError("нет initData")
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InitDataError("нет подписи")

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("неверная подпись")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("неверная дата") from exc
    current = time.time() if now is None else now
    if max_age_sec > 0 and current - auth_date > max_age_sec:
        raise InitDataError("initData устарела")

    try:
        user = json.loads(fields.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise InitDataError("неверный user") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("нет user.id")
    return user
