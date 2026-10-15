"""Пакет веб-интерфейса и сервера административной панели."""

from web.auth import (
    LoginRateLimiter,
    WebSessionStore,
    get_or_create_admin_web_key,
    get_public_web_url,
    login_rate_limiter,
    web_session_store,
)
from web.pages import (
    PERF_MODE_LABELS,
    format_ts,
    render_dashboard,
    render_login_page,
    render_template,
)
from web.server import (
    AdminWebHandler,
    start_cloudflared_tunnel_supervisor,
    start_web_admin_server,
)

__all__ = [
    "AdminWebHandler",
    "LoginRateLimiter",
    "PERF_MODE_LABELS",
    "WebSessionStore",
    "format_ts",
    "get_or_create_admin_web_key",
    "get_public_web_url",
    "login_rate_limiter",
    "render_dashboard",
    "render_login_page",
    "render_template",
    "start_cloudflared_tunnel_supervisor",
    "start_web_admin_server",
    "web_session_store",
]
