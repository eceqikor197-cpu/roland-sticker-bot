"""HTTP-сервер веб-админки Roland VP-540.

Реализует AdminWebHandler для просмотра очереди заданий, отправки в Hot Folder,
управления парсером Harvester и туннеля SSH/Cloudflare.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import shutil
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, List, Optional, Tuple

from config import (
    ADMIN_IDS,
    ADMIN_WEB_COOKIE_SECURE,
    ADMIN_WEB_PASSWORD,
    HOT_QUEUE_EPS_DIR,
    JOBS_DIR,
    PUBLIC_CATALOG,
    WEB_HOST,
    WEB_PORT,
)
from harvester.bot_integration import (
    enqueue_and_run_urgent_topic,
    render_harvester_web_catalog,
)
from harvester.database import (
    get_asset,
    get_state,
    init_db,
    set_state,
)
from harvester.storage.rclone_sync import (
    copy_harvester_asset_to_hot_folder,
    ensure_local_eps_for_asset,
    sync_staging_to_gdrive,
)
from web.auth import login_rate_limiter, web_session_store
from web.pages import render_dashboard, render_login_page

logger = logging.getLogger(__name__)


def _get_public_catalog() -> bool:
    try:
        import telegram_bot

        return bool(getattr(telegram_bot, "PUBLIC_CATALOG", PUBLIC_CATALOG))
    except Exception:
        return PUBLIC_CATALOG


def _get_admin_password() -> str:
    try:
        import telegram_bot

        return str(getattr(telegram_bot, "ADMIN_WEB_PASSWORD", ADMIN_WEB_PASSWORD))
    except Exception:
        return ADMIN_WEB_PASSWORD


def _get_jobs_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "JOBS_DIR", JOBS_DIR))
    except Exception:
        return JOBS_DIR


def _get_hot_queue_dir() -> Path:
    try:
        import telegram_bot

        return Path(getattr(telegram_bot, "HOT_QUEUE_EPS_DIR", HOT_QUEUE_EPS_DIR))
    except Exception:
        return HOT_QUEUE_EPS_DIR


def _load_json_file(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


class AdminWebHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов административной веб-панели."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self' 'unsafe-inline';",
        )
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def get_client_ip(self) -> str:
        return self.client_address[0]

    def get_session_id_from_cookie(self) -> Optional[str]:
        cookie_hdr = self.headers.get("Cookie", "")
        for part in cookie_hdr.split(";"):
            part = part.strip()
            if part.startswith("roland_session="):
                return part.split("=", 1)[1].strip()
        return None

    def check_admin_auth(self) -> Tuple[bool, Optional[str], Optional[str]]:
        session_id = self.get_session_id_from_cookie()
        if not session_id:
            return False, None, None
        sess = web_session_store.get_session(session_id)
        if not sess:
            return False, None, None
        return True, session_id, sess.get("csrf_token")

    def send_session_cookie(self, session_id: str) -> None:
        flags = "Path=/; Max-Age=604800; HttpOnly; SameSite=Strict"
        if ADMIN_WEB_COOKIE_SECURE:
            flags += "; Secure"
        self.send_header("Set-Cookie", f"roland_session={session_id}; {flags}")

    def clear_session_cookie(self) -> None:
        flags = "Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        if ADMIN_WEB_COOKIE_SECURE:
            flags += "; Secure"
        self.send_header("Set-Cookie", f"roland_session=; {flags}")

    def serve_login_page(
        self,
        next_url: str = "/",
        error_msg: str = "",
        status_code: int = 200,
    ) -> None:
        page = render_login_page(next_url=next_url, error_msg=error_msg)
        body = page.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        client_ip = self.get_client_ip()
        is_admin_user, session_id, csrf_token = self.check_admin_auth()

        # Защита изменяющих эндпоинтов от вызова через GET (CSRF)
        if (
            path in ("/harvester_toggle", "/harvester_sync", "/harvester_order")
            or path.startswith("/send_hot/")
            or path.startswith("/harvester_hot/")
        ):
            self.send_error(405, "Method Not Allowed")
            return

        if path == "/login":
            is_blocked, remaining = login_rate_limiter.is_blocked(client_ip)
            next_url = qs.get("next", ["/"])[0]
            if not next_url.startswith("/"):
                next_url = "/"
            if is_blocked:
                self.serve_login_page(
                    next_url=next_url,
                    error_msg=f"Слишком много неудачных попыток входа. Доступ заблокирован ({max(1, remaining // 60)} мин.).",
                    status_code=429,
                )
                return
            if is_admin_user:
                self.send_response(302)
                self.send_header("Location", next_url)
                self.end_headers()
                return
            self.serve_login_page(next_url=next_url)
            return

        if path == "/logout":
            if session_id:
                web_session_store.destroy_session(session_id)
            self.send_response(302)
            self.clear_session_cookie()
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if path in ("/", "/index.html"):
            if not is_admin_user:
                self.send_response(302)
                self.send_header("Location", "/login?next=/")
                self.end_headers()
                return
            filter_uid_str = qs.get("user", ["all"])[0]
            hot_msg = qs.get("hot_sent", [""])[0]
            self.serve_dashboard(filter_uid_str, hot_msg=hot_msg, csrf_token=csrf_token or "")
            return

        if path == "/harvester":
            if not _get_public_catalog() and not is_admin_user:
                self.send_response(302)
                self.send_header("Location", f"/login?next={urllib.parse.quote(self.path)}")
                self.end_headers()
                return
            cat_f = qs.get("cat", ["all"])[0]
            subcat_f = qs.get("subcat", ["all"])[0]
            folder_f = qs.get("folder", ["all"])[0]
            search_q = qs.get("q", [""])[0]
            msg_b = qs.get("msg", [""])[0]
            body = render_harvester_web_catalog(
                cat_filter=cat_f,
                subcat_filter=subcat_f,
                folder_filter=folder_f,
                search_q=search_q,
                msg_banner=msg_b,
                is_admin=is_admin_user,
                csrf_token=csrf_token or "",
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/harvester_file/"):
            if not _get_public_catalog() and not is_admin_user:
                self.send_response(302)
                self.send_header("Location", f"/login?next={urllib.parse.quote(self.path)}")
                self.end_headers()
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                _, aid, kind = parts
                self.serve_harvester_file(aid, kind)
                return

        if path.startswith("/files/"):
            if not is_admin_user:
                self.send_response(302)
                self.send_header("Location", "/login?next=/")
                self.end_headers()
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                _, job_id, kind = parts
                self.serve_job_file(job_id, kind)
                return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        client_ip = self.get_client_ip()

        content_len = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_len) if content_len > 0 else b""
        post_params = urllib.parse.parse_qs(body_bytes.decode("utf-8", errors="replace"))

        if path == "/login":
            is_blocked, remaining = login_rate_limiter.is_blocked(client_ip)
            next_url = post_params.get("next", ["/"])[0]
            if not next_url.startswith("/"):
                next_url = "/"

            if is_blocked:
                self.serve_login_page(
                    next_url=next_url,
                    error_msg=f"Слишком много неудачных попыток входа. Доступ заблокирован ({max(1, remaining // 60)} мин.).",
                    status_code=429,
                )
                return

            pw = post_params.get("pw", [""])[0]
            if pw and hmac.compare_digest(pw.encode("utf-8"), _get_admin_password().encode("utf-8")):
                login_rate_limiter.record_success(client_ip)
                session_id, _ = web_session_store.create_session()
                self.send_response(302)
                self.send_session_cookie(session_id)
                self.send_header("Location", next_url)
                self.end_headers()
                return

            is_now_blocked, _remaining = login_rate_limiter.record_failure(client_ip)
            if is_now_blocked:
                self.serve_login_page(
                    next_url=next_url,
                    error_msg="Неверный пароль. Превышен лимит попыток! IP заблокирован на 15 минут.",
                    status_code=429,
                )
            else:
                self.serve_login_page(
                    next_url=next_url,
                    error_msg="Неверный пароль администратора.",
                    status_code=401,
                )
            return

        is_admin_user, session_id, _ = self.check_admin_auth()
        if not is_admin_user or not session_id:
            self.send_response(302)
            self.send_header("Location", f"/login?next={urllib.parse.quote(path)}")
            self.end_headers()
            return

        # Проверка CSRF токена
        candidate_csrf = post_params.get("csrf_token", [""])[0] or self.headers.get("X-CSRF-Token", "")
        if not web_session_store.validate_csrf(session_id, candidate_csrf):
            self.send_error(403, "Forbidden: Invalid or missing CSRF token")
            return

        if path == "/harvester_toggle":
            init_db()
            curr = bool(get_state("enabled", True))
            set_state("enabled", not curr)
            m = "Парсер 24/7 запущен!" if not curr else "Парсер поставлен на паузу"
            self.send_response(302)
            self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
            self.end_headers()
            return

        if path == "/harvester_sync":
            init_db()
            ok, cnt, mb, msg = sync_staging_to_gdrive()
            m = f"Синхронизация с Google Drive: выгружено {cnt} шт. ({mb:.1f} МБ) — {msg}"
            self.send_response(302)
            self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
            self.end_headers()
            return

        if path == "/harvester_order":
            init_db()
            t_raw = post_params.get("topic", [""])[0].strip()
            if t_raw:
                bot_instance = getattr(self.server, "bot", None)
                if bot_instance is None:
                    try:
                        import telegram_bot

                        bot_instance = getattr(telegram_bot, "bot", None)
                    except Exception:
                        bot_instance = None
                enqueue_and_run_urgent_topic(bot_instance, list(ADMIN_IDS)[0], t_raw)
                m = f"🔥 Тема «{t_raw}» запущена вне очереди! Результаты появятся здесь и в Telegram-боте."
            else:
                m = "Укажите название темы"
            self.send_response(302)
            self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
            self.end_headers()
            return

        if path.startswith("/harvester_hot/"):
            init_db()
            aid = post_params.get("asset_id", [""])[0] or path.strip("/").split("/")[-1]
            confirm_unlicensed = post_params.get("confirm_unlicensed", ["0"])[0] in ("1", "true", "yes")
            asset = get_asset(aid)
            if not asset:
                self.send_error(404, "Asset not found")
                return
            lic = str(asset.get("license", "unknown")).lower().strip()
            if lic == "unknown" and not confirm_unlicensed:
                m = "⚠️ Ошибка: Лицензия не подтверждена, не печатать на продажу! Требуется подтверждение админа."
                self.send_response(302)
                self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
                self.end_headers()
                return

            dest = copy_harvester_asset_to_hot_folder(aid, admin_confirmed=confirm_unlicensed)
            fname = dest.name if dest else "ошибка"
            m = f"🚀 Макет {fname} отправлен в сетевую папку плоттера Roland_HotFolder!"
            self.send_response(302)
            self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
            self.end_headers()
            return

        if path.startswith("/send_hot/"):
            job_id = post_params.get("job_id", [""])[0] or path.strip("/").split("/")[-1]
            try:
                import telegram_bot

                dest = telegram_bot.copy_job_to_hot_folder(job_id)
            except Exception:
                dest = None
            fname = dest.name if dest else ""
            self.send_response(302)
            self.send_header("Location", f"/?hot_sent={urllib.parse.quote(fname)}")
            self.end_headers()
            return

        self.send_error(404, "Not Found")

    def serve_job_file(self, job_id: str, kind: str) -> None:
        if not re.match(r"^[a-f0-9]{8,16}$", job_id):
            self.send_error(400, "Invalid job ID")
            return
        jdir = _get_jobs_dir() / job_id
        meta = _load_json_file(jdir / "meta.json", {})
        if not meta:
            self.send_error(404, "Job not found")
            return

        if kind == "preview":
            fpath = jdir / "preview_cutcontour.png"
            ctype = "image/png"
            dl_name = None
        elif kind == "orig":
            inp = meta.get("input_filename", "input.jpg")
            fpath = jdir / inp
            ext = Path(inp).suffix.lower()
            ctype = "image/png" if ext == ".png" else "image/jpeg"
            dl_name = f"{meta.get('orig_stem', 'orig')}{ext}"
        elif kind == "eps":
            eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
            fpath = jdir / eps_name
            ctype = "application/postscript"
            dl_name = eps_name
        else:
            self.send_error(400, "Unknown file kind")
            return

        if not fpath.exists():
            self.send_error(404, "File missing")
            return

        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if dl_name and kind == "eps":
            encoded_name = urllib.parse.quote(dl_name)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{encoded_name}",
            )
        self.end_headers()
        self.wfile.write(data)

    def serve_harvester_file(self, asset_id: str, kind: str) -> None:
        init_db()
        asset = get_asset(asset_id)
        if not asset:
            self.send_error(404, "Asset not found")
            return

        if kind == "preview":
            fpath = Path(asset.get("local_preview_path", ""))
            ctype = "image/jpeg"
            dl_name = None
        elif kind == "eps":
            fpath_opt = ensure_local_eps_for_asset(asset_id)
            if fpath_opt is None:
                self.send_error(404, "EPS file missing")
                return
            fpath = fpath_opt
            ctype = "application/postscript"
            dl_name = asset.get("eps_filename", f"{asset_id}.eps")
        else:
            self.send_error(400, "Unknown kind")
            return

        if not fpath.exists():
            self.send_error(404, "File missing on disk")
            return

        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if dl_name and kind == "eps":
            encoded_name = urllib.parse.quote(dl_name)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{encoded_name}",
            )
        self.end_headers()
        self.wfile.write(data)

    def serve_dashboard(
        self,
        filter_uid_str: str,
        hot_msg: str = "",
        csrf_token: str = "",
    ) -> None:
        page = render_dashboard(
            filter_uid_str,
            hot_msg=hot_msg,
            csrf_token=csrf_token,
            hot_queue_dir=_get_hot_queue_dir(),
        )
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_web_admin_server(bot: Any = None) -> None:
    """Запускает HTTP-сервер панели администратора."""
    try:
        server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), AdminWebHandler)
        if bot is not None:
            server.bot = bot  # type: ignore[attr-defined]
        logger.info("Веб-админка запущена на http://%s:%d", WEB_HOST, WEB_PORT)
        server.serve_forever()
    except Exception as exc:
        logger.error("Ошибка запуска веб-админки на %s:%d: %s", WEB_HOST, WEB_PORT, exc)


def start_cloudflared_tunnel_supervisor(bot: Any = None) -> None:
    """Запускает SSH-туннель через localhost.run для внешнего доступа к веб-админке."""
    import atexit
    import html
    import subprocess
    import threading
    import time

    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    ssh_bin = shutil.which("ssh") or "/usr/bin/ssh"
    url_pattern = re.compile(r"https://[a-z0-9-]+\.lhr\.life")
    active_proc: List[Optional[subprocess.Popen]] = [None]

    def _cleanup() -> None:
        p = active_proc[0]
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    atexit.register(_cleanup)

    last_url = ""

    while True:
        try:
            proc = subprocess.Popen(
                [
                    ssh_bin,
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-o",
                    "ServerAliveInterval=30",
                    "-o",
                    "ServerAliveCountMax=3",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-T",
                    "-R",
                    f"80:127.0.0.1:{WEB_PORT}",
                    "nokey@localhost.run",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            active_proc[0] = proc
            if proc.stdout:
                for line in proc.stdout:
                    m = url_pattern.search(line)
                    if m:
                        new_url = m.group(0).rstrip("/")
                        if new_url == last_url:
                            continue
                        last_url = new_url
                        try:
                            init_db()
                            set_state("public_web_url", new_url)
                            logger.info("Публичный веб-адрес обновлен: %s", new_url)
                        except Exception as exc:
                            logger.warning("Не удалось сохранить URL туннеля в БД: %s", exc)

                        def _notify_or_edit(u: str = new_url) -> None:
                            current_bot = bot
                            if current_bot is None:
                                try:
                                    import telegram_bot

                                    current_bot = getattr(telegram_bot, "bot", None)
                                except Exception:
                                    current_bot = None
                            if current_bot is None:
                                return
                            kb = InlineKeyboardMarkup()
                            kb.row(
                                InlineKeyboardButton(
                                    "🌐 Веб-База Стикеров (Админ)",
                                    url=f"{u}/harvester",
                                )
                            )
                            kb.row(
                                InlineKeyboardButton(
                                    "👑 Веб-Панель Работ (Админ)",
                                    url=f"{u}/",
                                )
                            )
                            text = (
                                f"🌐 <b>Онлайн Веб-Дашборд и База Стикеров активны!</b>\n\n"
                                f"• 📦 <b>Публичный каталог стикеров (с любого устройства без VPN):</b>\n"
                                f'<a href="{html.escape(u)}/harvester">{html.escape(u)}/harvester</a>\n\n'
                                f"• 🦊 <b>Постоянный адрес Tailscale (при включённом Tailscale на Mac):</b>\n"
                                f"<code>http://[fd7a:115c:a1e0::b62e:d84a]:{WEB_PORT}/harvester</code>\n\n"
                                f"• 🔐 <b>Вход в веб-админку:</b> используйте пароль администратора\n"
                                f"<i>🔄 Ссылка в этом сообщении и в меню /harvester обновляется автоматически</i>"
                            )
                            saved_ids = get_state("tunnel_msg_ids", {}) or {}
                            if not isinstance(saved_ids, dict):
                                saved_ids = {}
                            for aid in ADMIN_IDS:
                                aid_str = str(aid)
                                prev_mid = saved_ids.get(aid_str)
                                edited = False
                                if prev_mid:
                                    try:
                                        current_bot.edit_message_text(
                                            text,
                                            chat_id=aid,
                                            message_id=int(prev_mid),
                                            reply_markup=kb,
                                        )
                                        edited = True
                                    except Exception as e_err:
                                        if "message is not modified" in str(e_err):
                                            edited = True
                                if not edited:
                                    try:
                                        sent = current_bot.send_message(
                                            aid,
                                            text,
                                            reply_markup=kb,
                                            disable_notification=True,
                                        )
                                        saved_ids[aid_str] = sent.message_id
                                    except Exception:
                                        pass
                            set_state("tunnel_msg_ids", saved_ids)

                        threading.Thread(target=_notify_or_edit, daemon=True).start()
            proc.wait()
        except Exception as exc:
            logger.warning("Ошибка в цикле туннеля: %s, перезапуск через 10с...", exc)
        time.sleep(10)

