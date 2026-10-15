"""Модуль аутентификации и сессий веб-админки Roland VP-540.

Управляет сессиями (WebSessionStore), защитой от перебора (LoginRateLimiter)
и валидацией CSRF-токенов.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from harvester.database import get_state

logger = logging.getLogger(__name__)


class WebSessionStore:
    """Хранилище сессий администратора в памяти с поддержкой CSRF и TTL."""

    def __init__(self, ttl_seconds: int = 86400 * 7):
        self.ttl_seconds = ttl_seconds
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_session(self) -> Tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge_expired_locked(now)
            self._sessions[session_id] = {
                "csrf_token": csrf_token,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
        return session_id, csrf_token

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return None
            if sess["expires_at"] < now:
                self._sessions.pop(session_id, None)
                return None
            return sess

    def destroy_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def validate_csrf(self, session_id: str, candidate_csrf: str) -> bool:
        sess = self.get_session(session_id)
        if not sess:
            return False
        stored_csrf = str(sess.get("csrf_token", ""))
        if not candidate_csrf or not stored_csrf:
            return False
        return hmac.compare_digest(candidate_csrf, stored_csrf)

    def _purge_expired_locked(self, now: float) -> None:
        expired = [sid for sid, data in self._sessions.items() if data["expires_at"] < now]
        for sid in expired:
            self._sessions.pop(sid, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


class LoginRateLimiter:
    """Ограничитель частоты попыток входа для защиты от brute-force атак."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 600,
        block_seconds: int = 900,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._attempts: Dict[str, List[float]] = {}
        self._blocks: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_blocked(self, ip: str) -> Tuple[bool, int]:
        now = time.time()
        with self._lock:
            block_until = self._blocks.get(ip, 0.0)
            if block_until > now:
                return True, int(block_until - now)
            if ip in self._blocks:
                del self._blocks[ip]
            return False, 0

    def record_failure(self, ip: str) -> Tuple[bool, int]:
        now = time.time()
        with self._lock:
            if self._blocks.get(ip, 0.0) > now:
                return True, int(self._blocks[ip] - now)

            history = [t for t in self._attempts.get(ip, []) if now - t < self.window_seconds]
            history.append(now)
            self._attempts[ip] = history

            if len(history) >= self.max_attempts:
                block_until = now + self.block_seconds
                self._blocks[ip] = block_until
                self._attempts.pop(ip, None)
                logger.warning(
                    "IP %s заблокирован на %d минут из-за %d неудачных попыток входа.",
                    ip,
                    self.block_seconds // 60,
                    self.max_attempts,
                )
                return True, self.block_seconds

            return False, 0

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._attempts.pop(ip, None)
            self._blocks.pop(ip, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()
            self._blocks.clear()


web_session_store = WebSessionStore()
login_rate_limiter = LoginRateLimiter()


def get_or_create_admin_web_key() -> str:
    """Устаревшая функция, сохранена для обратной совместимости."""
    return ""


def get_public_web_url() -> str:
    """Возвращает публичный URL веб-панели (например, из туннеля)."""
    try:
        return str(get_state("public_web_url", "") or "").strip().rstrip("/")
    except Exception:
        return ""
