#!/usr/bin/env python3
r"""
Telegram-бот на сервере crybaby для конвертации JPEG/PNG стикеров и стикерпаков
в печатный формат .eps для плоттера-каттера Roland VP-540 (Roland VersaWorks).

Возможности:
1. 100% сохранение качества оригинала (Lossless Print & Cut) + векторный контур CutContour (100% Magenta).
2. Двойной контур Roland VersaWorks: CutContour (надсечка) + PerfCutContour (сквозная рамка стикерпака или вырубка Die-Cut).
3. Выбор точного размера (5см, 7см, 10см, 15см, А5, А4 или свой размер в мм/см), авто-раскладка тиража на рулон (Nesting 1/10/25/50/100 шт) и калькулятор расхода плёнки/себестоимости/цены.
4. Отправка готового .EPS в сетевую папку Hot Folder (\\192.168.0.108\Roland_HotFolder\Queue_EPS) в 1 клик.
5. Встроенная Админ-панель (/admin) + Live-шпион + Веб-админка (http://192.168.0.108:8090).
"""

import contextlib
import datetime
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telegram_bot")

import telebot
from telebot.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from PIL.Image import DecompressionBombError

from jpeg2eps import (
    MM_TO_PT,
    SUPPORTED_EXTENSIONS,
    calculate_nesting_and_price,
    convert_image_to_plotter_eps,
)
from harvester.bot_integration import (
    check_and_consume_waiting_topic,
    enqueue_and_run_urgent_topic,
     register_harvester_bot_handlers,
    render_harvester_web_catalog,
)
from harvester.database import get_asset, get_state, init_db, set_state
from harvester.storage.rclone_sync import (
    copy_harvester_asset_to_hot_folder,
    ensure_local_eps_for_asset,
    sync_staging_to_gdrive,
)

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "bot_workspace"
JOBS_DIR = WORK_DIR / "jobs"
PREFS_FILE = WORK_DIR / "user_prefs.json"
USERS_FILE = WORK_DIR / "users_db.json"
ADMIN_STATE_FILE = WORK_DIR / "admin_state.json"

HOT_FOLDER_ROOT = Path("/home/crybaby/Roland_HotFolder")
if not HOT_FOLDER_ROOT.parent.exists():
    HOT_FOLDER_ROOT = WORK_DIR / "Roland_HotFolder"
HOT_QUEUE_EPS_DIR = HOT_FOLDER_ROOT / "Queue_EPS"
HOT_PREVIEWS_DIR = HOT_FOLDER_ROOT / "Previews"

WORK_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HOT_QUEUE_EPS_DIR.mkdir(parents=True, exist_ok=True)
HOT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

from config import (
    JOB_RETENTION_HOURS,
    LIVE_SPY_DEFAULT,
    PUBLIC_CATALOG,
)
from harvester.config import BOT_TOKEN

ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "870592714").split(",") if x.strip().isdigit()}


def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


ALLOW_USER_HOT_FOLDER = os.getenv("ALLOW_USER_HOT_FOLDER", "false").lower() in ("true", "1", "yes")
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "15"))
MAX_SIZE_MM = float(os.getenv("MAX_SIZE_MM", "1370.0"))
MIN_SIZE_MM = 5.0
MIN_STRETCH = 0.1
MAX_STRETCH = 10.0
MAX_CONVERSIONS_PER_MINUTE = int(os.getenv("MAX_CONVERSIONS_PER_MINUTE", "6"))
MAX_CONCURRENT_CONVERSIONS = int(os.getenv("MAX_CONCURRENT_CONVERSIONS", "2"))


class ConversionLimiter:
    """Ограничивает частоту (<= 6/мин) и параллельность (1 на пользователя, N глобально)."""

    def __init__(
        self,
        max_per_minute: int = 6,
        max_concurrent_global: int = 2,
    ):
        self.max_per_minute = max_per_minute
        self.max_concurrent_global = max_concurrent_global
        self._history: Dict[int, List[float]] = {}
        self._active_users: set[int] = set()
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrent_global)

    def check_and_acquire_user_slot(self, user_id: int) -> Tuple[bool, str]:
        if is_admin(user_id):
            return True, "ok"
        now = time.time()
        with self._lock:
            if user_id in self._active_users:
                return False, "concurrent"
            user_times = [t for t in self._history.get(user_id, []) if now - t < 60.0]
            self._history[user_id] = user_times
            if len(user_times) >= self.max_per_minute:
                return False, "rate_limit"
            self._active_users.add(user_id)
            return True, "ok"

    def release_user_slot(self, user_id: int, record_completed: bool = True) -> None:
        if is_admin(user_id):
            return
        now = time.time()
        with self._lock:
            self._active_users.discard(user_id)
            if record_completed:
                if user_id not in self._history:
                    self._history[user_id] = []
                self._history[user_id].append(now)

    @contextlib.contextmanager
    def run_conversion_slot(self, user_id: int, on_queued: Optional[Any] = None):
        acquired_global = False
        try:
            acquired_now = self._semaphore.acquire(blocking=False)
            if not acquired_now:
                if on_queued:
                    try:
                        on_queued()
                    except Exception:
                        pass
                self._semaphore.acquire(blocking=True)
            acquired_global = True
            yield
        finally:
            if acquired_global:
                self._semaphore.release()
            self.release_user_slot(user_id)


conversion_limiter = ConversionLimiter(
    max_per_minute=MAX_CONVERSIONS_PER_MINUTE,
    max_concurrent_global=MAX_CONCURRENT_CONVERSIONS,
)

WEB_HOST = os.getenv("ADMIN_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("ADMIN_WEB_PORT", "8090"))
ADMIN_WEB_PASSWORD = os.getenv("ADMIN_WEB_PASSWORD", "roland")
ADMIN_WEB_COOKIE_SECURE = os.getenv("ADMIN_WEB_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
ENABLE_PUBLIC_TUNNEL = os.getenv("ENABLE_PUBLIC_TUNNEL", "false").lower() in ("true", "1", "yes")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
db_lock = threading.RLock()



class WebSessionStore:
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
    return ""


def get_public_web_url() -> str:
    try:
        return str(get_state("public_web_url", "") or "").strip().rstrip("/")
    except Exception:
        return ""

PERF_MODE_LABELS = {
    "none": "Только CutContour (надсечка)",
    "sheet_frame": "CutContour + Рамка листа (PerfCut)",
    "die_cut": "CutContour + Вырубка наклеек (PerfCut)",
}


def load_json_file(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json_file(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_admin_state() -> Dict[str, Any]:
    with db_lock:
        state = load_json_file(ADMIN_STATE_FILE, {})
        defaults = {
            "live_spy_enabled": LIVE_SPY_DEFAULT,
            "banned_users": [],
        }
        defaults.update(state)
        return defaults


def update_admin_state(**kwargs: Any) -> Dict[str, Any]:
    state = get_admin_state()
    with db_lock:
        state.update(kwargs)
        save_json_file(ADMIN_STATE_FILE, state)
    return state


def is_user_banned(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    state = get_admin_state()
    return int(user_id) in [int(x) for x in state.get("banned_users", [])]


def toggle_ban_user(target_uid: int) -> bool:
    state = get_admin_state()
    banned = {int(x) for x in state.get("banned_users", [])}
    if int(target_uid) in banned:
        banned.remove(int(target_uid))
        now_banned = False
    else:
        banned.add(int(target_uid))
        now_banned = True
    update_admin_state(banned_users=sorted(list(banned)))
    return now_banned


def record_user_activity(
    user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    action: str = "",
    last_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    with db_lock:
        users = load_json_file(USERS_FILE, {})
        key = str(user_id)
        now = time.time()
        entry = users.get(
            key,
            {
                "user_id": int(user_id),
                "first_name": first_name or f"User {user_id}",
                "last_name": last_name or "",
                "username": username or "",
                "first_seen": now,
                "last_seen": now,
                "jobs_count": 0,
                "last_action": action,
                "last_job_id": last_job_id or "",
            },
        )
        if first_name:
            entry["first_name"] = first_name
        if last_name is not None:
            entry["last_name"] = last_name
        if username is not None:
            entry["username"] = username
        entry["last_seen"] = now
        if action:
            entry["last_action"] = action
        if last_job_id:
            entry["last_job_id"] = last_job_id
        users[key] = entry
        save_json_file(USERS_FILE, users)
        return entry


def format_user_trigger(user_info: Dict[str, Any]) -> str:
    uid = user_info.get("user_id", "")
    fn = (user_info.get("first_name") or "").strip()
    ln = (user_info.get("last_name") or "").strip()
    un = (user_info.get("username") or "").strip()
    full = f"{fn} {ln}".strip() or f"ID {uid}"
    if un:
        return f"{html.escape(full)} (@{html.escape(un)})"
    return f"{html.escape(full)} (<code>{uid}</code>)"


def get_user_display(user_id: int) -> str:
    users = load_json_file(USERS_FILE, {})
    info = users.get(str(user_id))
    if info:
        return format_user_trigger(info)
    return f"ID <code>{user_id}</code>"


def get_user_short_slug(user_id: int) -> str:
    users = load_json_file(USERS_FILE, {})
    info = users.get(str(user_id), {})
    raw = info.get("username") or info.get("first_name") or str(user_id)
    slug = re.sub(r"[^\w\-А-Яа-яЁё]+", "", str(raw)).strip()
    return slug[:16] or str(user_id)


def load_all_prefs() -> Dict[str, Any]:
    with db_lock:
        return load_json_file(PREFS_FILE, {})


def save_all_prefs(data: Dict[str, Any]) -> None:
    with db_lock:
        save_json_file(PREFS_FILE, data)


def get_user_prefs(user_id: int) -> Dict[str, Any]:
    all_prefs = load_all_prefs()
    key = str(user_id)
    defaults = {
        "offset_mm": 2.5,
        "size_mm": 100.0,
        "keep_background": False,
        "vectorize": False,
        "perf_cut_mode": "none",
        "perf_cut_passes": 2,
        "nesting_qty": 1,
        "roll_width_mm": 1370.0,
        "stretch_x": 1.0,
        "stretch_y": 1.0,
        "fit_a4_sheet": False,
    }
    if key in all_prefs and isinstance(all_prefs[key], dict):
        defaults.update(all_prefs[key])
    # Всегда используем 100% оригинальное растровое качество (vtracer отключён, т.к. вызывает SEGV в потоках Python 3.14)
    defaults["vectorize"] = False
    if float(defaults.get("roll_width_mm", 1370.0)) == 1000.0:
        defaults["roll_width_mm"] = 1370.0
    return defaults


def update_user_prefs(user_id: int, **kwargs: Any) -> Dict[str, Any]:
    all_prefs = load_all_prefs()
    key = str(user_id)
    prefs = get_user_prefs(user_id)
    prefs.update(kwargs)
    all_prefs[key] = prefs
    save_all_prefs(all_prefs)
    return prefs


def cleanup_old_jobs(max_age_hours: Optional[float] = None) -> int:
    """Удаляет директории заданий старше max_age_hours часов (по умолчанию JOB_RETENTION_HOURS)."""
    if max_age_hours is None:
        max_age_hours = JOB_RETENTION_HOURS
    now = time.time()
    cutoff = now - max_age_hours * 3600.0
    deleted_count = 0
    if not JOBS_DIR.exists():
        return 0
    for item in list(JOBS_DIR.iterdir()):
        try:
            if item.is_dir() and item.stat().st_mtime < cutoff:
                shutil.rmtree(item, ignore_errors=True)
                deleted_count += 1
        except Exception as exc:
            logger.warning("Не удалось удалить устаревшее задание %s: %s", item, exc)
    return deleted_count


_cleanup_thread: Optional[threading.Thread] = None
_cleanup_stop_event: Optional[threading.Event] = None


def run_cleanup_loop(interval_seconds: float = 3600.0, stop_event: Optional[threading.Event] = None) -> None:
    """Периодическая фоновая очистка старых заданий."""
    logger.info(
        "Запущен фоновый поток очистки заданий (интервал %s с, срок хранения %s ч)",
        interval_seconds,
        JOB_RETENTION_HOURS,
    )
    while not (stop_event and stop_event.is_set()):
        try:
            cnt = cleanup_old_jobs(max_age_hours=JOB_RETENTION_HOURS)
            if cnt > 0:
                logger.info("Фоновая автоочистка: удалено %d устаревших заданий", cnt)
        except Exception as exc:
            logger.warning("Ошибка при периодической очистке старых заданий: %s", exc)

        if stop_event:
            if stop_event.wait(interval_seconds):
                break
        else:
            time.sleep(interval_seconds)


def start_cleanup_scheduler(interval_seconds: float = 3600.0) -> threading.Thread:
    """Запускает фоновый поток периодической очистки заданий."""
    global _cleanup_thread, _cleanup_stop_event
    if _cleanup_thread and _cleanup_thread.is_alive():
        return _cleanup_thread
    _cleanup_stop_event = threading.Event()
    _cleanup_thread = threading.Thread(
        target=run_cleanup_loop,
        args=(interval_seconds, _cleanup_stop_event),
        daemon=True,
        name="JobCleanupScheduler",
    )
    _cleanup_thread.start()
    return _cleanup_thread


def delete_user_data(user_id: int) -> Dict[str, Any]:
    """Удаляет все задания, файлы макетов и записи пользователя (GDPR / Privacy)."""
    uid_int = int(user_id)
    uid_str = str(user_id)
    deleted_jobs = 0

    # 1. Удаляем все каталоги заданий пользователя из JOBS_DIR
    if JOBS_DIR.exists():
        for jdir in list(JOBS_DIR.iterdir()):
            if not jdir.is_dir():
                continue
            meta_file = jdir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = load_json_file(meta_file, {})
                if int(meta.get("user_id", 0)) == uid_int:
                    shutil.rmtree(jdir, ignore_errors=True)
                    deleted_jobs += 1
            except Exception as exc:
                logger.warning("Не удалось удалить задание пользователя %s из %s: %s", uid_int, jdir, exc)

    # 2. Удаляем запись пользователя из USERS_FILE
    user_record_deleted = False
    with db_lock:
        users = load_json_file(USERS_FILE, {})
        if uid_str in users:
            del users[uid_str]
            save_json_file(USERS_FILE, users)
            user_record_deleted = True

    # 3. Удаляем настройки пользователя из PREFS_FILE
    user_prefs_deleted = False
    with db_lock:
        prefs = load_json_file(PREFS_FILE, {})
        if uid_str in prefs:
            del prefs[uid_str]
            save_json_file(PREFS_FILE, prefs)
            user_prefs_deleted = True

    # 4. Сбрасываем историю лимитера для данного пользователя
    if hasattr(conversion_limiter, "_history"):
        with conversion_limiter._lock:
            conversion_limiter._history.pop(uid_int, None)
            conversion_limiter._active_users.discard(uid_int)

    logger.info(
        "Данные пользователя %s удалены: заданий=%d, запись=%s, настройки=%s",
        uid_int,
        deleted_jobs,
        user_record_deleted,
        user_prefs_deleted,
    )
    return {
        "user_id": uid_int,
        "deleted_jobs": deleted_jobs,
        "deleted_user_record": user_record_deleted,
        "deleted_user_prefs": user_prefs_deleted,
    }


def format_privacy_text() -> str:
    """Формирует подробный текст политики конфиденциальности и обработки данных."""
    retention_days = JOB_RETENTION_HOURS / 24.0
    if retention_days.is_integer():
        retention_str = f"{int(retention_days)} дн. ({int(JOB_RETENTION_HOURS)} ч.)"
    else:
        retention_str = f"{retention_days:.1f} дн. ({int(JOB_RETENTION_HOURS)} ч.)"

    return (
        "🔒 <b>Политика конфиденциальности и обработки данных</b>\n\n"
        "1. <b>Обработка файлов:</b>\n"
        "Ваши изображения и документы обрабатываются на сервере исключительно для создания контура реза CutContour "
        "и подготовки файлов .EPS для плоттера Roland.\n\n"
        f"2. <b>Срок хранения файлов:</b>\n"
        f"Все файлы макетов хранятся на сервере <b>{retention_str}</b>, после чего автоматически и безвозвратно удаляются фоновой службой.\n\n"
        "3. <b>Просмотр администратором (Live-мониторинг):</b>\n"
        "Администратор сервиса может просматривать загруженные макеты для обработки заказов, проверки качества контуров "
        "и отправки на печать. Режим Live-мониторинга включается администратором осознанно вручную.\n\n"
        "4. <b>Удаление данных (/delete_my_data):</b>\n"
        "Вы можете в любой момент удалить все свои задания, файлы макетов, сохранённые настройки и профиль командой "
        "/delete_my_data или кнопкой ниже."
    )


def inspect_eps_stats(eps_path: Path) -> Tuple[int, int, float, float]:
    """Возвращает (cut_contours_count, perf_contours_count, width_mm, height_mm)."""
    if not eps_path.exists():
        return 1, 0, 100.0, 100.0
    try:
        raw = eps_path.read_bytes()
        header_part = raw[:1200].decode("latin1", errors="ignore")
        m_bbox = re.search(
            r"%%HiResBoundingBox:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            header_part,
        )
        w_mm, h_mm = 100.0, 100.0
        if m_bbox:
            w_pt = float(m_bbox.group(3)) - float(m_bbox.group(1))
            h_pt = float(m_bbox.group(4)) - float(m_bbox.group(2))
            w_mm = round(w_pt / MM_TO_PT, 1)
            h_mm = round(h_pt / MM_TO_PT, 1)

        cut_marker = b"% --- Plotter CutContour Spot Color Vector Layer"
        perf_marker = b"% --- Plotter PerfCutContour Spot Color Vector Layer"

        cut_cnt = 0
        c_idx = raw.find(cut_marker)
        if c_idx != -1:
            c_end = raw.find(b"\r\ns\r\n", c_idx)
            c_sec = raw[c_idx:c_end] if c_end != -1 else raw[c_idx : c_idx + 200000]
            cut_cnt = len(re.findall(rb"\bcp\b", c_sec))

        perf_cnt = 0
        p_idx = raw.find(perf_marker)
        if p_idx != -1:
            p_end = raw.find(b"\r\ns\r\n", p_idx)
            p_sec = raw[p_idx:p_end] if p_end != -1 else raw[p_idx : p_idx + 200000]
            perf_cnt = len(re.findall(rb"\bcp\b", p_sec))

        return cut_cnt, perf_cnt, w_mm, h_mm
    except Exception:
        return 1, 0, 100.0, 100.0


def copy_job_to_hot_folder(job_id: str) -> Optional[Path]:
    """Копирует готовый .eps и превью в сетевую папку Samba /home/crybaby/Roland_HotFolder/."""
    jdir = JOBS_DIR / job_id
    meta_file = jdir / "meta.json"
    if not meta_file.exists():
        return None
    meta = load_json_file(meta_file, {})
    eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
    eps_path = jdir / eps_name
    if not eps_path.exists():
        return None

    uid = int(meta.get("user_id", 0))
    user_slug = get_user_short_slug(uid)
    qty = int(meta.get("nesting_qty", 1))
    size_mm = int(round(float(meta.get("size_mm", 100))))
    dt_str = datetime.datetime.now().strftime("%m-%d_%H-%M")
    safe_stem = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", str(meta.get("orig_stem", "sticker"))).strip("_")

    hot_base = f"{dt_str}_{user_slug}_{qty}sht_{size_mm}mm_{safe_stem}"
    dest_eps = HOT_QUEUE_EPS_DIR / f"{hot_base}.eps"
    shutil.copy2(eps_path, dest_eps)

    preview_png = jdir / "preview_cutcontour.png"
    if preview_png.exists():
        shutil.copy2(preview_png, HOT_PREVIEWS_DIR / f"{hot_base}.png")

    meta["in_hot_folder"] = True
    meta["hot_filename"] = dest_eps.name
    save_json_file(meta_file, meta)
    return dest_eps


def list_all_jobs(filter_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for jdir in JOBS_DIR.iterdir():
        if not jdir.is_dir():
            continue
        meta_file = jdir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if filter_user_id is not None and int(meta.get("user_id", 0)) != int(filter_user_id):
                continue
            eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
            eps_path = jdir / eps_name
            preview_path = jdir / "preview_cutcontour.png"
            meta["has_eps"] = eps_path.exists()
            meta["eps_filename"] = eps_name
            meta["eps_size_mb"] = (
                round(eps_path.stat().st_size / (1024 * 1024), 2) if eps_path.exists() else 0.0
            )
            meta["has_preview"] = preview_path.exists()
            jobs.append(meta)
        except Exception:
            continue
    jobs.sort(key=lambda x: float(x.get("updated_at", x.get("created_at", 0))), reverse=True)
    return jobs


def backfill_existing_jobs_and_users() -> None:
    users = load_json_file(USERS_FILE, {})
    prefs = load_all_prefs()
    seen_uids = {int(k) for k in prefs.keys() if str(k).isdigit()}
    all_jobs = list_all_jobs()
    for j in all_jobs:
        uid = int(j.get("user_id", 0))
        if uid:
            seen_uids.add(uid)

    for uid in seen_uids:
        if str(uid) not in users:
            try:
                chat = bot.get_chat(uid)
                record_user_activity(
                    uid,
                    first_name=getattr(chat, "first_name", None) or f"User {uid}",
                    last_name=getattr(chat, "last_name", None) or "",
                    username=getattr(chat, "username", None) or "",
                    action="Активен в боте",
                )
            except Exception:
                record_user_activity(uid, first_name=f"User {uid}", action="Активен в боте")

    users = load_json_file(USERS_FILE, {})
    counts: Dict[str, int] = {}
    for j in all_jobs:
        uid_str = str(j.get("user_id", ""))
        counts[uid_str] = counts.get(uid_str, 0) + 1
        jdir = JOBS_DIR / j["job_id"]
        meta_file = jdir / "meta.json"
        eps_path = jdir / f"{j.get('orig_stem', 'sticker')}.eps"
        changed = False
        if "num_contours" not in j or "width_mm" not in j:
            cc, pc, w_mm, h_mm = inspect_eps_stats(eps_path)
            j["num_contours"] = cc
            j["perf_contours"] = pc
            j["width_mm"] = w_mm
            j["height_mm"] = h_mm
            changed = True
        if "offset_mm" not in j:
            up = get_user_prefs(int(j.get("user_id", 0)))
            j["offset_mm"] = up["offset_mm"]
            j["size_mm"] = up["size_mm"]
            j["keep_background"] = up["keep_background"]
            j["vectorize"] = up["vectorize"]
            j["perf_cut_mode"] = up.get("perf_cut_mode", "none")
            j["nesting_qty"] = up.get("nesting_qty", 1)
            changed = True
        if changed:
            save_json_file(meta_file, j)

    with db_lock:
        for k, v in counts.items():
            if k in users:
                users[k]["jobs_count"] = v
        save_json_file(USERS_FILE, users)


def format_ts(ts: float) -> str:
    if not ts:
        return "—"
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=3)))
    return dt.strftime("%d.%m %H:%M:%S")


def build_job_keyboard(
    job_id: str,
    prefs: Dict[str, Any],
    in_hot_folder: bool = False,
    a4_fit_qty: int = 1,
    menu_tab: Optional[str] = None,
    hot_request_status: Optional[str] = None,
) -> InlineKeyboardMarkup:
    offset_mm = float(prefs.get("offset_mm", 2.5))
    size_mm = float(prefs.get("size_mm", 100.0))
    keep_bg = bool(prefs.get("keep_background", False))
    perf_mode = str(prefs.get("perf_cut_mode", "none"))
    qty = int(prefs.get("nesting_qty", 1))
    fit_a4 = bool(prefs.get("fit_a4_sheet", False))
    sx = float(prefs.get("stretch_x", 1.0))
    sy = float(prefs.get("stretch_y", 1.0))
    active_tab = menu_tab or str(prefs.get("menu_tab", "main"))

    kb = InlineKeyboardMarkup(row_width=2)

    # =========================================================================
    # ПОДМЕНЮ 1: РАЗМЕР МАКЕТА (Плавный шаг + Готовые пресеты)
    # =========================================================================
    if active_tab == "size":
        kb.row(
            InlineKeyboardButton(
                f"📏 Сейчас: {size_mm/10.0:g} см ({size_mm:g} мм)",
                callback_data=f"job:{job_id}:noop:0",
            )
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 0.5 см", callback_data=f"job:{job_id}:dsz:-5"),
            InlineKeyboardButton("➕ Увеличить 0.5 см", callback_data=f"job:{job_id}:dsz:5"),
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 1 см", callback_data=f"job:{job_id}:dsz:-10"),
            InlineKeyboardButton("➕ Увеличить 1 см", callback_data=f"job:{job_id}:dsz:10"),
        )
        kb.row(
            InlineKeyboardButton("➖ Уменьшить 5 см", callback_data=f"job:{job_id}:dsz:-50"),
            InlineKeyboardButton("➕ Увеличить 5 см", callback_data=f"job:{job_id}:dsz:50"),
        )

        # Компактные пресеты по 3 в ряд (короткий текст, не обрезается)
        row_s1 = []
        for s_val, s_lbl in [(50.0, "5 см"), (70.0, "7 см"), (100.0, "10 см")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s1.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s1)

        row_s2 = []
        for s_val, s_lbl in [(150.0, "15 см"), (210.0, "А5 (21см)"), (297.0, "А4 (30см)")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s2.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s2)

        row_s3 = []
        for s_val, s_lbl in [(500.0, "50 см"), (1000.0, "100 см (1м)"), (1370.0, "137 см")]:
            act = (not fit_a4) and abs(size_mm - s_val) < 1.5
            row_s3.append(
                InlineKeyboardButton(
                    f"{'✅ ' if act else ''}{s_lbl}",
                    callback_data=f"job:{job_id}:sz:{s_val:g}",
                )
            )
        kb.row(*row_s3)

        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # =========================================================================
    # ПОДМЕНЮ 2: РАСТЯЖЕНИЕ И СЖАТИЕ ПО ОСЯМ (Пропорции Ш × В без ограничений)
    # =========================================================================
    if active_tab == "stretch":
        kb.row(
            InlineKeyboardButton(
                f"📐 Пропорции сейчас: Ш ×{sx:.2g} | В ×{sy:.2g}",
                callback_data=f"job:{job_id}:noop:0",
            )
        )
        kb.row(
            InlineKeyboardButton("↔️ Сжать ширину (-10%)", callback_data=f"job:{job_id}:stx:0.9"),
            InlineKeyboardButton("↔️ Растянуть вширь (+10%)", callback_data=f"job:{job_id}:stx:1.1"),
        )
        kb.row(
            InlineKeyboardButton("↕️ Сжать высоту (-10%)", callback_data=f"job:{job_id}:sty:0.9"),
            InlineKeyboardButton("↕️ Растянуть вверх (+10%)", callback_data=f"job:{job_id}:sty:1.1"),
        )
        kb.row(
            InlineKeyboardButton("↔️ Ширина -25%", callback_data=f"job:{job_id}:stx:0.75"),
            InlineKeyboardButton("↔️ Ширина +25%", callback_data=f"job:{job_id}:stx:1.25"),
        )
        kb.row(
            InlineKeyboardButton("↕️ Высота -25%", callback_data=f"job:{job_id}:sty:0.75"),
            InlineKeyboardButton("↕️ Высота +25%", callback_data=f"job:{job_id}:sty:1.25"),
        )
        kb.row(
            InlineKeyboardButton("🔄 Сбросить пропорции 1:1", callback_data=f"job:{job_id}:rst:1")
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # =========================================================================
    # ПОДМЕНЮ 3: КОНТУР РЕЗА И ОТСТУП КАНТА (CutContour + PerfCutContour)
    # =========================================================================
    if active_tab == "cut":
        perf_passes = int(prefs.get("perf_cut_passes", 2))
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 0.0) < 0.05 else ''}Без канта (0 мм)",
                callback_data=f"job:{job_id}:off:0.0",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 1.5) < 0.05 else ''}Тонкий кант (1.5 мм)",
                callback_data=f"job:{job_id}:off:1.5",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 2.5) < 0.05 else ''}Стандарт (2.5 мм)",
                callback_data=f"job:{job_id}:off:2.5",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if abs(offset_mm - 4.0) < 0.05 else ''}Широкий кант (4 мм)",
                callback_data=f"job:{job_id}:off:4.0",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'none' and not fit_a4 else ''}✂️ Только до подложки (CutContour)",
                callback_data=f"job:{job_id}:perf:none",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'sheet_frame' or fit_a4 else ''}📦 До подложки + Сквозная рамка листа",
                callback_data=f"job:{job_id}:perf:sheet_frame",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_mode == 'die_cut' else ''}🔪 До подложки + Вырубка по контуру насквозь",
                callback_data=f"job:{job_id}:perf:die_cut",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes == 1 else ''}🔁 Сквозной: 1 проход",
                callback_data=f"job:{job_id}:ppass:1",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes == 2 else ''}🔥 Сквозной: 2 прохода (Дорез)",
                callback_data=f"job:{job_id}:ppass:2",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if perf_passes >= 3 else ''}💥 Сквозной: 3 прохода (Толстая плёнка / Ламинат)",
                callback_data=f"job:{job_id}:ppass:3",
            )
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # =========================================================================
    # ПОДМЕНЮ 4: РАСКЛАДКА НА ЛИСТ А4 И ТИРАЖ НА РУЛОН ПЛОТТЕРА
    # =========================================================================
    if active_tab == "layout":
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if fit_a4 else ''}📄 Заполнить лист А4 ({a4_fit_qty} шт на листе)",
                callback_data=f"job:{job_id}:a4:{'0' if fit_a4 else '1'}",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 1) else ''}1️⃣ Одиночный макет (1 штука)",
                callback_data=f"job:{job_id}:qty:1",
            )
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 10) else ''}📦 На рулон: 10 штук",
                callback_data=f"job:{job_id}:qty:10",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 25) else ''}📦 На рулон: 25 штук",
                callback_data=f"job:{job_id}:qty:25",
            ),
        )
        kb.row(
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 50) else ''}📦 На рулон: 50 штук",
                callback_data=f"job:{job_id}:qty:50",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if (not fit_a4 and qty == 100) else ''}📦 На рулон: 100 штук",
                callback_data=f"job:{job_id}:qty:100",
            ),
        )
        kb.row(
            InlineKeyboardButton("⬅️ Назад к основному меню", callback_data=f"job:{job_id}:nav:main")
        )
        return kb

    # =========================================================================
    # ГЛАВНОЕ МЕНЮ МАКЕТА (Все кнопки широкие, по 1-2 в ряд — ничего не обрезается!)
    # =========================================================================
    stretch_badge = (
        f" ({sx:.2g}×{sy:.2g})"
        if (abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02)
        else ""
    )
    qty_badge = f"А4 ({a4_fit_qty} шт)" if fit_a4 else f"{qty} шт"

    # Ряд 1: Переход в меню Размера и меню Растяжения/Сжатия
    kb.row(
        InlineKeyboardButton(
            f"📐 Размер ({size_mm/10.0:g} см) ▸",
            callback_data=f"job:{job_id}:nav:size",
        ),
        InlineKeyboardButton(
            f"↔️ Сжать / Растянуть{stretch_badge} ▸",
            callback_data=f"job:{job_id}:nav:stretch",
        ),
    )

    # Ряд 2: Переход в меню Контура/Канта и меню Тиража/Раскладки
    kb.row(
        InlineKeyboardButton(
            f"✂️ Контур и Кант ({offset_mm:g} мм) ▸",
            callback_data=f"job:{job_id}:nav:cut",
        ),
        InlineKeyboardButton(
            f"📦 Тираж: {qty_badge} ▸",
            callback_data=f"job:{job_id}:nav:layout",
        ),
    )

    # Ряд 3: Быстрая кнопка раскладки на полный лист А4 в 1 клик
    a4_btn_lbl = (
        f"✅ Собрано на лист А4 ({a4_fit_qty} шт) — Вернуть 1 шт"
        if fit_a4
        else f"📄 Разложить на весь лист А4 ({a4_fit_qty} шт)"
    )
    kb.row(
        InlineKeyboardButton(a4_btn_lbl, callback_data=f"job:{job_id}:a4:{'0' if fit_a4 else '1'}")
    )

    # Ряд 4: Переключение фона
    bg_toggle_val = "0" if keep_bg else "1"
    bg_lbl = (
        "🔲 Фон: Оставлен (нажмите, чтобы вырезать)"
        if keep_bg
        else "✂️ Фон: Вырезан (нажмите, чтобы оставить)"
    )
    kb.row(InlineKeyboardButton(bg_lbl, callback_data=f"job:{job_id}:bg:{bg_toggle_val}"))

    # Ряд 5: Отправка на плоттер
    if in_hot_folder:
        hot_lbl = "✅ Уже в Hot Folder плоттера (отправить ещё раз)"
    elif hot_request_status == "pending":
        hot_lbl = "⏳ Заявка ожидает подтверждения админа"
    elif not ALLOW_USER_HOT_FOLDER:
        hot_lbl = "🚀 Запросить печать на плоттере (Hot Folder)"
    else:
        hot_lbl = "🚀 Отправить на ПК с плоттером (Hot Folder)"
    kb.row(InlineKeyboardButton(hot_lbl, callback_data=f"job:{job_id}:hot:1"))

    return kb


def notify_admins_about_job(
    actor_id: int,
    job_meta: Dict[str, Any],
    preview_png: Path,
    action_title: str,
) -> None:
    if is_admin(actor_id):
        return
    state = get_admin_state()
    if not state.get("live_spy_enabled", LIVE_SPY_DEFAULT):
        return

    user_disp = get_user_display(actor_id)
    job_id = job_meta["job_id"]
    eps_filename = f"{job_meta['orig_stem']}.eps"
    num_contours = job_meta.get("num_contours", 1)
    perf_mode = job_meta.get("perf_cut_mode", "none")
    qty = int(job_meta.get("nesting_qty", 1))
    w_mm = job_meta.get("width_mm", 100.0)
    h_mm = job_meta.get("height_mm", 100.0)
    calc = job_meta.get("pricing", {})

    text = (
        f"👀 <b>Активность в боте: {action_title}</b>\n"
        f"👤 Пользователь: {user_disp}\n"
        f"📄 Файл: <code>{html.escape(eps_filename)}</code>\n"
        f"📐 Габарит: <b>{w_mm:g} × {h_mm:g} мм</b> | Тираж: <b>{qty} шт.</b>\n"
        f"✂️ Резка: <b>{PERF_MODE_LABELS.get(perf_mode, perf_mode)}</b> ({num_contours} конт.)\n"
        f"💰 Себестоимость (ЗЕНОН): <b>~{calc.get('cost_rub', 10)} ₽</b> | Цена клиенту: <b>~{calc.get('retail_rub', 150)} ₽</b>"
    )

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📥 Скачать его .EPS", callback_data=f"adm:dl_eps:{job_id}"),
        InlineKeyboardButton("🚀 В Hot Folder плоттера", callback_data=f"adm:hot:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("🖼 Исходник", callback_data=f"adm:dl_orig:{job_id}"),
        InlineKeyboardButton("👑 Админ-панель", callback_data="adm:home"),
    )

    for admin_id in ADMIN_IDS:
        try:
            if preview_png.exists() and preview_png.stat().st_size > 0:
                try:
                    with open(preview_png, "rb") as pf:
                        bot.send_photo(admin_id, photo=pf, caption=text, reply_markup=kb)
                except Exception:
                    bot.send_message(admin_id, text, reply_markup=kb)
            else:
                bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            traceback.print_exc()


def process_and_send_job(
    chat_id: int,
    user_id: int,
    job_dir: Path,
    status_msg_id: Optional[int] = None,
    was_compressed_photo: bool = False,
    action_title: str = "Новая конвертация",
) -> None:
    meta_file = job_dir / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    input_path = job_dir / meta["input_filename"]
    orig_stem = meta["orig_stem"]
    prefs = get_user_prefs(user_id)

    offset_mm = float(prefs["offset_mm"])
    size_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, float(prefs["size_mm"])))
    keep_bg = bool(prefs["keep_background"])
    perf_mode = str(prefs.get("perf_cut_mode", "none"))
    perf_passes = int(prefs.get("perf_cut_passes", 2))
    nesting_qty = int(prefs.get("nesting_qty", 1))
    roll_width_mm = float(prefs.get("roll_width_mm", 1370.0))
    stretch_x = max(MIN_STRETCH, min(MAX_STRETCH, float(prefs.get("stretch_x", 1.0))))
    stretch_y = max(MIN_STRETCH, min(MAX_STRETCH, float(prefs.get("stretch_y", 1.0))))
    fit_a4_sheet = bool(prefs.get("fit_a4_sheet", False))

    eps_filename = f"{orig_stem}.eps"
    output_eps = job_dir / eps_filename
    preview_png = job_dir / "preview_cutcontour.png"

    try:
        t0 = time.time()
        convert_image_to_plotter_eps(
            input_path=input_path,
            output_eps=output_eps,
            size_mm=size_mm,
            offset_mm=offset_mm,
            bg_tolerance=12.0,
            keep_background=keep_bg,
            no_cut_contour=False,
            vectorize_artwork=False,
            also_svg=False,
            preview_png_path=preview_png,
            perf_cut_mode=perf_mode,
            nesting_qty=nesting_qty,
            roll_width_mm=roll_width_mm,
            stretch_x=stretch_x,
            stretch_y=stretch_y,
            fit_a4_sheet=fit_a4_sheet,
            render_a4_guide_on_preview=True,
            perf_cut_passes=perf_passes,
        )
    except DecompressionBombError:
        logger.warning("DecompressionBombError in process_and_send_job for user %s", user_id)
        if status_msg_id is not None:
            try:
                bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
        bot.send_message(
            chat_id,
            "⚠️ <b>Файл слишком большой или повреждён (превышен лимит пикселей).</b>\n"
            "Пожалуйста, уменьшите разрешение изображения.",
        )
        return
    except Exception as exc:
        logger.exception("Error in process_and_send_job for user %s", user_id)
        if status_msg_id is not None:
            try:
                bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
        if is_admin(user_id):
            bot.send_message(
                chat_id,
                f"❌ <b>Ошибка при обработке файла:</b>\n<code>{html.escape(str(exc))}</code>",
            )
        else:
            bot.send_message(
                chat_id,
                "❌ <b>Не удалось обработать файл.</b>",
            )
        return

    elapsed = time.time() - t0
    num_contours, perf_contours, total_w_mm, total_h_mm = inspect_eps_stats(output_eps)
    eps_size_mb = output_eps.stat().st_size / (1024 * 1024)

    # Для расчёта цены и раскладки определяем размер 1 штуки
    if nesting_qty == 1 and not fit_a4_sheet:
        single_w_mm, single_h_mm = total_w_mm, total_h_mm
        meta["single_w_mm"] = single_w_mm
        meta["single_h_mm"] = single_h_mm
    else:
        single_w_mm = float(meta.get("single_w_mm", size_mm))
        single_h_mm = float(meta.get("single_h_mm", size_mm))

    calc_for_25 = calculate_nesting_and_price(
        single_w_mm, single_h_mm, qty=max(nesting_qty, 25), roll_width_mm=roll_width_mm
    )
    calc_curr = calculate_nesting_and_price(
        single_w_mm, single_h_mm, qty=(int(calc_for_25["a4_fit_qty"]) if fit_a4_sheet else nesting_qty), roll_width_mm=roll_width_mm
    )
    a4_fit_qty = int(calc_curr.get("a4_fit_qty", 1))

    meta.update(
        {
            "updated_at": time.time(),
            "offset_mm": offset_mm,
            "size_mm": size_mm,
            "keep_background": keep_bg,
            "vectorize": False,
            "perf_cut_mode": perf_mode,
            "perf_cut_passes": perf_passes,
            "nesting_qty": nesting_qty,
            "stretch_x": stretch_x,
            "stretch_y": stretch_y,
            "fit_a4_sheet": fit_a4_sheet,
            "num_contours": num_contours,
            "perf_contours": perf_contours,
            "width_mm": total_w_mm,
            "height_mm": total_h_mm,
            "eps_size_mb": round(eps_size_mb, 2),
            "elapsed_sec": round(elapsed, 2),
            "last_action": action_title,
            "pricing": calc_curr,
        }
    )
    save_json_file(meta_file, meta)

    with db_lock:
        users = load_json_file(USERS_FILE, {})
        ukey = str(user_id)
        if ukey in users:
            users[ukey]["jobs_count"] = len(list_all_jobs(filter_user_id=user_id))
            users[ukey]["last_seen"] = time.time()
            users[ukey]["last_action"] = f"{action_title} ({eps_filename})"
            users[ukey]["last_job_id"] = job_dir.name
            save_json_file(USERS_FILE, users)

    perf_info = (
        f" + голубой <code>PerfCutContour</code> ({perf_contours} шт. × <b>{perf_passes} прох.</b>)"
        if perf_contours > 0
        else ""
    )
    stretch_info = (
        f" (пропорции {stretch_x:.2g}×{stretch_y:.2g})"
        if (abs(stretch_x - 1.0) > 0.02 or abs(stretch_y - 1.0) > 0.02)
        else ""
    )

    caption_lines = [
        f"✅ <b>Готово для Roland VP-540!</b> ({elapsed:.1f} сек)",
        f"📄 Файл: <code>{html.escape(eps_filename)}</code> ({eps_size_mb:.1f} МБ)",
        f"📐 Габарит макета: <b>{total_w_mm:g} × {total_h_mm:g} мм</b> (1 шт: <b>{single_w_mm/10.0:.1f} × {single_h_mm/10.0:.1f} см</b>){stretch_info}",
        f"✂️ Контуры: розовый <code>CutContour</code> (<b>{num_contours} шт.</b>){perf_info}",
        f"📏 Кант: <b>{offset_mm:g} мм</b> | Режим: <b>{'Лист А4 с вырубкой рамки' if fit_a4_sheet else PERF_MODE_LABELS.get(perf_mode, perf_mode)}</b>",
        "",
        "💰 <b>Калькулятор по накладной ЗЕНОН-СПБ (рулон 1.37м 103₽/м + чернила STARINK 4006₽/л):</b>",
        f"• 📄 <b>На 1 лист А4 (21×29.7 см):</b> входит <b>{a4_fit_qty} шт.</b> (себест. листа А4 ~<b>{calc_curr['a4_cost_rub']} ₽</b> | клиенту ~<b>{calc_curr['a4_retail_rub']} ₽</b>)",
    ]
    if fit_a4_sheet:
        caption_lines.extend(
            [
                f"• ✅ <b>Сейчас собран полный лист А4 ({a4_fit_qty} шт.):</b> себестоимость <b>~{calc_curr['a4_cost_rub']} ₽</b>, розница <b>~{calc_curr['a4_retail_rub']} ₽</b>",
            ]
        )
    elif nesting_qty > 1:
        caption_lines.extend(
            [
                f"• Собрано на рулон 1.37 м: <b>{nesting_qty} шт.</b> ({calc_curr['cols']} в ряд × {calc_curr['rows']} ряд., расход {calc_curr['used_length_m']} пог. м)",
                f"• Себестоимость: <b>~{calc_curr['cost_rub']} ₽</b> (материал {calc_curr['material_cost_rub']} ₽ + чернила {calc_curr['ink_cost_rub']} ₽)",
                f"• Рекомендуемая цена клиенту: <b>{calc_curr['retail_rub']} ₽</b> ({calc_curr['unit_rub']} ₽/шт)",
            ]
        )
    else:
        caption_lines.extend(
            [
                f"• На рулон 1.37 м войдёт: <b>по {calc_for_25['cols']} шт. в ряд</b>",
                f"• 1 шт.: себест. ~<b>{calc_curr['cost_rub']} ₽</b> | тираж 25 шт. ({calc_for_25['used_length_m']} пог.м): себест. <b>~{calc_for_25['cost_rub']} ₽</b> (материал {calc_for_25['material_cost_rub']}₽ + чернила {calc_for_25['ink_cost_rub']}₽), клиенту <b>~{calc_for_25['retail_rub']} ₽</b>",
            ]
        )

    caption_lines.append(
        "\n💡 <i>Справа на превью показан точный масштаб на листе А4. Вы можете сжимать/растягивать кнопками ниже или написать в чат <code>12x8 см</code>!</i>"
    )
    caption = "\n".join(caption_lines)
    kb = build_job_keyboard(
        job_dir.name,
        prefs,
        in_hot_folder=bool(meta.get("in_hot_folder")),
        a4_fit_qty=a4_fit_qty,
        hot_request_status=meta.get("hot_folder_request_status"),
    )

    if preview_png.exists():
        try:
            with open(preview_png, "rb") as pf:
                bot.send_photo(
                    chat_id,
                    photo=pf,
                    caption=caption,
                    reply_markup=kb,
                )
        except Exception:
            try:
                from PIL import Image as _PILImage

                with _PILImage.open(preview_png) as _im:
                    _w, _h = _im.size
                    _safe_w = max(_w, int(_h / 15) + 1)
                    _safe_h = max(_h, int(_w / 15) + 1)
                    _pad = _PILImage.new("RGB", (_safe_w, _safe_h), (24, 27, 36))
                    _pad.paste(_im.convert("RGB"), ((_safe_w - _w) // 2, (_safe_h - _h) // 2))
                    _safe_path = job_dir / "preview_safe.jpg"
                    _pad.save(_safe_path, quality=90)
                with open(_safe_path, "rb") as pf:
                    bot.send_photo(
                        chat_id,
                        photo=pf,
                        caption=caption,
                        reply_markup=kb,
                    )
            except Exception:
                bot.send_message(chat_id, caption, reply_markup=kb)

    if fit_a4_sheet:
        dl_eps_name = f"{orig_stem}_A4_{a4_fit_qty}sht_{int(round(size_mm))}mm.eps"
    elif nesting_qty > 1:
        dl_eps_name = f"{orig_stem}_{nesting_qty}sht_{int(round(size_mm))}mm.eps"
    else:
        dl_eps_name = eps_filename

    with open(output_eps, "rb") as ef:
        bot.send_document(
            chat_id,
            document=ef,
            visible_file_name=dl_eps_name,
            caption=f"🖨 Готовый <code>.EPS</code> для Roland VersaWorks — <code>{html.escape(dl_eps_name)}</code>",
        )

    if status_msg_id is not None:
        try:
            bot.delete_message(chat_id, status_msg_id)
        except Exception:
            pass

    notify_admins_about_job(
        actor_id=user_id,
        job_meta=meta,
        preview_png=preview_png,
        action_title=action_title,
    )


# ==============================================================================
# АДМИН-ПАНЕЛЬ В TELEGRAM (/admin)
# ==============================================================================


def build_admin_home_view() -> tuple[str, InlineKeyboardMarkup]:
    users = load_json_file(USERS_FILE, {})
    jobs = list_all_jobs()
    state = get_admin_state()
    spy_on = state.get("live_spy_enabled", LIVE_SPY_DEFAULT)
    hot_files = sorted(HOT_QUEUE_EPS_DIR.glob("*.eps"), key=lambda p: p.stat().st_mtime, reverse=True)

    total_contours = sum(int(j.get("num_contours", 1)) for j in jobs)
    total_mb = sum(float(j.get("eps_size_mb", 0.0)) for j in jobs)
    public_url = get_public_web_url()

    lines = [
        "👑 <b>Админ-панель Plotter EPS Bot (сервер crybaby)</b>\n",
        f"👥 Пользователей в боте: <b>{len(users)}</b>",
        f"🗂 Всего сконвертировано макетов: <b>{len(jobs)}</b> ({total_mb:.1f} МБ)",
        f"✂️ Построено контуров <code>CutContour</code>: <b>{total_contours} шт.</b>",
        f"🖨 В сетевой папке Hot Folder плоттера: <b>{len(hot_files)} файл(ов)</b>",
        f"🔔 Live-уведомления о чужих работах: <b>{'🟢 ВКЛ' if spy_on else '🔴 ВЫКЛ'}</b>\n",
        "🌐 <b>Веб-Дашборд и База Стикеров (доступ из любой точки):</b>",
    ]
    if public_url:
        lines.append(
            f"• 🌍 <b>Онлайн HTTPS (без VPN, для всех):</b> <a href=\"{html.escape(public_url)}/harvester\">{html.escape(public_url)}/harvester</a>"
        )
    lines.extend(
        [
            f"• 🦊 <b>Tailscale:</b> <code>http://[fd7a:115c:a1e0::b62e:d84a]:{WEB_PORT}/harvester</code>",
            f"• 🦊 <b>Tailscale IPv4:</b> <code>http://100.76.216.73:{WEB_PORT}/harvester</code>",
            f"• 🏠 <b>По локалке:</b> <code>http://192.168.0.115:{WEB_PORT}/harvester</code>",
            "• 🔐 <b>Вход в веб-админку:</b> используйте пароль администратора\n",
            "📂 <b>Сетевая папка для ПК с плоттером (Samba):</b>",
            "• Windows: <code>\\\\192.168.0.115\\Roland_HotFolder</code> (или <code>\\\\100.76.216.73\\Roland_HotFolder</code>)",
            "• Mac: <code>smb://192.168.0.115/Roland_HotFolder</code>\n",
            "<b>Последние действия в боте:</b>",
        ]
    )

    for j in jobs[:6]:
        uid = int(j.get("user_id", 0))
        udisp = get_user_display(uid)
        ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
        nc = j.get("num_contours", 1)
        qty = j.get("nesting_qty", 1)
        w_mm = j.get("width_mm", 100)
        h_mm = j.get("height_mm", 100)
        lines.append(
            f"• <code>{ts_str}</code> — {udisp}: <code>{html.escape(j.get('orig_stem', 'file'))}.eps</code> ({w_mm:g}×{h_mm:g}мм, {qty}шт, {nc} конт.)"
        )

    kb = InlineKeyboardMarkup()
    if public_url:
        kb.row(
            InlineKeyboardButton(
                "🌐 Веб-База Стикеров (Админ)",
                url=f"{public_url}/harvester",
            ),
            InlineKeyboardButton(
                "👑 Веб-Панель Работ (Админ)",
                url=f"{public_url}/",
            ),
        )
    kb.row(
        InlineKeyboardButton("🚜 Парсер Базы 24/7 (Google Drive 5 ТБ)", callback_data="harv:home"),
    )
    kb.row(
        InlineKeyboardButton(f"🖼 Смотреть работы ({len(jobs)})", callback_data="adm:feed:0:all"),
        InlineKeyboardButton(f"👥 Пользователи ({len(users)})", callback_data="adm:users"),
    )
    kb.row(
        InlineKeyboardButton(
            f"🔔 Live-шпион: {'ВКЛ 🟢' if spy_on else 'ВЫКЛ 🔴'}",
            callback_data="adm:toggle_spy",
        ),
        InlineKeyboardButton("🔄 Обновить", callback_data="adm:home"),
    )
    return "\n".join(lines), kb


def send_or_edit_admin_feed_item(
    chat_id: int,
    index: int,
    filter_arg: str = "all",
    prev_msg_id: Optional[int] = None,
) -> None:
    filter_uid = int(filter_arg) if filter_arg != "all" and filter_arg.isdigit() else None
    jobs = list_all_jobs(filter_user_id=filter_uid)

    if not jobs:
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="adm:home"))
        bot.send_message(chat_id, "📭 Работ пока не найдено.", reply_markup=kb)
        return

    index = max(0, min(index, len(jobs) - 1))
    j = jobs[index]
    job_id = j["job_id"]
    uid = int(j.get("user_id", 0))
    udisp = get_user_display(uid)
    ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
    eps_name = f"{j.get('orig_stem', 'sticker')}.eps"
    nc = j.get("num_contours", 1)
    off = j.get("offset_mm", 2.5)
    w_mm = j.get("width_mm", 100)
    h_mm = j.get("height_mm", 100)
    qty = j.get("nesting_qty", 1)
    perf_mode = j.get("perf_cut_mode", "none")
    size_mb = j.get("eps_size_mb", 0.0)
    calc = j.get("pricing", {})

    caption = (
        f"🗂 <b>Работа #{index + 1} из {len(jobs)}</b>\n"
        f"👤 Автор: {udisp}\n"
        f"🕒 Время: <code>{ts_str}</code>\n"
        f"📄 Файл: <code>{html.escape(eps_name)}</code> ({size_mb:.1f} МБ)\n"
        f"📐 Размер: <b>{w_mm:g} × {h_mm:g} мм</b> | Тираж: <b>{qty} шт.</b>\n"
        f"✂️ Контуров: <b>{nc} шт.</b> ({PERF_MODE_LABELS.get(perf_mode, perf_mode)}, кант {off:g}мм)\n"
        f"💰 Себестоимость: <b>~{calc.get('cost_rub', 30)} ₽</b> | Клиенту: <b>~{calc.get('retail_rub', 200)} ₽</b>"
    )

    kb = InlineKeyboardMarkup()
    nav_row = []
    if index > 0:
        nav_row.append(
            InlineKeyboardButton("⬅️ Пред.", callback_data=f"adm:feed:{index - 1}:{filter_arg}")
        )
    nav_row.append(
        InlineKeyboardButton(f"{index + 1}/{len(jobs)}", callback_data="adm:noop")
    )
    if index < len(jobs) - 1:
        nav_row.append(
            InlineKeyboardButton("След. ➡️", callback_data=f"adm:feed:{index + 1}:{filter_arg}")
        )
    kb.row(*nav_row)

    kb.row(
        InlineKeyboardButton("📥 Скачать .EPS", callback_data=f"adm:dl_eps:{job_id}"),
        InlineKeyboardButton("🖼 Скачать оригинал", callback_data=f"adm:dl_orig:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("🚀 В Hot Folder плоттера", callback_data=f"adm:hot:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton("👑 В главное меню админки", callback_data="adm:home"),
    )

    if prev_msg_id is not None:
        try:
            bot.delete_message(chat_id, prev_msg_id)
        except Exception:
            pass

    preview_png = JOBS_DIR / job_id / "preview_cutcontour.png"
    if preview_png.exists():
        with open(preview_png, "rb") as pf:
            bot.send_photo(chat_id, photo=pf, caption=caption, reply_markup=kb)
    else:
        bot.send_message(chat_id, caption, reply_markup=kb)


def build_admin_users_view() -> tuple[str, InlineKeyboardMarkup]:
    users = load_json_file(USERS_FILE, {})
    state = get_admin_state()
    banned_set = {int(x) for x in state.get("banned_users", [])}

    lines = ["👥 <b>Пользователи бота:</b>\n"]
    kb = InlineKeyboardMarkup()

    sorted_users = sorted(
        users.values(),
        key=lambda u: float(u.get("last_seen", 0)),
        reverse=True,
    )

    for u in sorted_users:
        uid = int(u["user_id"])
        udisp = format_user_trigger(u)
        jcount = len(list_all_jobs(filter_user_id=uid))
        last_str = format_ts(float(u.get("last_seen", 0)))
        last_act = html.escape(str(u.get("last_action", "—")))
        role_badge = "👑 Админ" if is_admin(uid) else ("🚫 Заблокирован" if uid in banned_set else "🟢 Активен")
        prefs = get_user_prefs(uid)

        lines.append(
            f"• {udisp} [{role_badge}]\n"
            f"  └ Работ: <b>{jcount}</b> | Посл. раз: <code>{last_str}</code>\n"
            f"  └ Настройки: размер <b>{prefs['size_mm']:g}мм</b>, кант <b>{prefs['offset_mm']:g}мм</b>, тираж <b>{prefs.get('nesting_qty', 1)}шт</b>\n"
            f"  └ Действие: <i>{last_act}</i>\n"
        )

        fn_short = (u.get("first_name") or str(uid))[:14]
        row_btns = [
            InlineKeyboardButton(
                f"🗂 Работы {fn_short} ({jcount})",
                callback_data=f"adm:feed:0:{uid}",
            )
        ]
        if not is_admin(uid):
            ban_lbl = "✅ Разбанить" if uid in banned_set else "🚫 Забанить"
            row_btns.append(
                InlineKeyboardButton(ban_lbl, callback_data=f"adm:ban:{uid}")
            )
        kb.row(*row_btns)

    kb.row(InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="adm:home"))
    return "\n".join(lines), kb


@bot.message_handler(commands=["admin", "stats"])
def handle_admin_command(message: Message) -> None:
    user = message.from_user
    record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/admin",
    )
    if not is_admin(user.id):
        bot.reply_to(message, "⛔ Доступ к админ-панели есть только у администратора.")
        return

    text, kb = build_admin_home_view()
    bot.send_message(message.chat.id, text, reply_markup=kb)


@bot.message_handler(commands=["spy", "live_spy"])
def handle_spy_command(message: Message) -> None:
    user = message.from_user
    record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/spy",
    )
    if not is_admin(user.id):
        bot.reply_to(message, "⛔ Доступ к управлению Live-шпионом есть только у администратора.")
        return

    raw_text = (message.text or "").strip()
    parts = raw_text.split()
    st = get_admin_state()
    current = st.get("live_spy_enabled", LIVE_SPY_DEFAULT)

    if len(parts) > 1:
        arg = parts[1].lower()
        if arg in ("on", "1", "true", "вкл", "включить"):
            new_val = True
        elif arg in ("off", "0", "false", "выкл", "выключить"):
            new_val = False
        else:
            bot.reply_to(
                message,
                "ℹ️ Использование: <code>/spy</code> (переключить) или <code>/spy on</code> / <code>/spy off</code>",
            )
            return
    else:
        new_val = not current

    update_admin_state(live_spy_enabled=new_val)
    status_str = "🟢 ВКЛЮЧЕН" if new_val else "🔴 ВЫКЛЮЧЕН"
    bot.reply_to(
        message,
        f"🔔 <b>Live-шпион: {status_str}</b>\n\n"
        f"Уведомления о макетах пользователей {'будут' if new_val else 'НЕ будут'} пересылаться администратору.\n"
        f"ℹ️ <i>Пользователи уведомлены в /privacy о том, что администратор может просматривать макеты для обработки заказов.</i>",
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("adm:"))
def handle_admin_callback(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Только для администратора", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    chat_id = call.message.chat.id

    try:
        if action == "noop":
            bot.answer_callback_query(call.id)
            return

        if action == "home":
            bot.answer_callback_query(call.id)
            text, kb = build_admin_home_view()
            if call.message.content_type == "text":
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            else:
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except Exception:
                    pass
                bot.send_message(chat_id, text, reply_markup=kb)
            return

        if action == "toggle_spy":
            st = get_admin_state()
            new_val = not st.get("live_spy_enabled", LIVE_SPY_DEFAULT)
            update_admin_state(live_spy_enabled=new_val)
            bot.answer_callback_query(
                call.id,
                f"Live-уведомления: {'ВКЛЮЧЕНЫ 🟢' if new_val else 'ВЫКЛЮЧЕНЫ 🔴'}",
            )
            text, kb = build_admin_home_view()
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            return

        if action == "users":
            bot.answer_callback_query(call.id)
            text, kb = build_admin_users_view()
            if call.message.content_type == "text":
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            else:
                bot.send_message(chat_id, text, reply_markup=kb)
            return

        if action == "ban" and len(parts) >= 3:
            target_uid = int(parts[2])
            now_banned = toggle_ban_user(target_uid)
            bot.answer_callback_query(
                call.id,
                f"Пользователь {target_uid} {'заблокирован 🚫' if now_banned else 'разблокирован ✅'}",
            )
            text, kb = build_admin_users_view()
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
            return

        if action == "feed" and len(parts) >= 4:
            idx = int(parts[2])
            filter_arg = parts[3]
            bot.answer_callback_query(call.id)
            send_or_edit_admin_feed_item(
                chat_id=chat_id,
                index=idx,
                filter_arg=filter_arg,
                prev_msg_id=call.message.message_id,
            )
            return

        if action == "hot" and len(parts) >= 3:
            job_id = parts[2]
            dest = copy_job_to_hot_folder(job_id)
            if dest is None:
                bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "✅ Отправлено в Hot Folder плоттера!")
            bot.send_message(
                chat_id,
                f"🚀 <b>Файл отправлен в сетевую папку плоттера (Hot Folder)!</b>\n"
                f"📄 Имя файла: <code>{html.escape(dest.name)}</code>\n"
                f"📂 На ПК с Roland VersaWorks: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>",
            )
            return

        if action == "dl_eps" and len(parts) >= 3:
            job_id = parts[2]
            jdir = JOBS_DIR / job_id
            meta = load_json_file(jdir / "meta.json", {})
            eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
            eps_path = jdir / eps_name
            if not eps_path.exists():
                bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "📥 Отправляю .EPS файл...")
            with open(eps_path, "rb") as ef:
                bot.send_document(
                    chat_id,
                    document=ef,
                    visible_file_name=eps_name,
                    caption=f"🖨 Файл пользователя {get_user_display(int(meta.get('user_id', 0)))} — <code>{html.escape(eps_name)}</code>",
                )
            return

        if action == "dl_orig" and len(parts) >= 3:
            job_id = parts[2]
            jdir = JOBS_DIR / job_id
            meta = load_json_file(jdir / "meta.json", {})
            inp_name = meta.get("input_filename", "input.jpg")
            inp_path = jdir / inp_name
            if not inp_path.exists():
                bot.answer_callback_query(call.id, "⚠️ Исходник не найден", show_alert=True)
                return
            bot.answer_callback_query(call.id, "🖼 Отправляю исходный файл...")
            ext = Path(inp_name).suffix or ".jpg"
            orig_dl_name = f"{meta.get('orig_stem', 'original')}{ext}"
            with open(inp_path, "rb") as inf:
                bot.send_document(
                    chat_id,
                    document=inf,
                    visible_file_name=orig_dl_name,
                    caption=f"🖼 Исходник от {get_user_display(int(meta.get('user_id', 0)))}",
                )
            return

    except Exception as exc:
        traceback.print_exc()
        bot.answer_callback_query(call.id, f"Ошибка: {exc}")


# ==============================================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ ХЕНДЛЕРЫ
# ==============================================================================


@bot.message_handler(commands=["start", "help"])
def handle_start(message: Message) -> None:
    user = message.from_user
    record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/start",
    )
    if is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    prefs = get_user_prefs(user.id)
    retention_days = JOB_RETENTION_HOURS / 24.0
    if retention_days.is_integer():
        retention_days_str = f"{int(retention_days)} дн."
    else:
        retention_days_str = f"{retention_days:.1f} дн."

    text = (
        "🖨 <b>Бот подготовки файлов для плоттера Roland VP-540 (Print & Cut → EPS)</b>\n"
        "Работает 24/7 на сервере <code>crybaby</code>.\n\n"
        "<b>Что умеет бот:</b>\n"
        "• 💎 <b>100% сохранение качества оригинала</b> внутри наклеек + розовый контур надсечки <code>CutContour</code>;\n"
        "• 📦 <b>Двойной рез для Roland VersaWorks:</b> умеет добавлять сквозной контур <code>PerfCutContour</code> (голубая линия на превью) — рамку вокруг всего стикерпака или вырубку каждого стикера навылет;\n"
        "• 🧩 <b>Авто-раскладка на рулон (Nesting) + калькулятор цены:</b> собирает тираж 10 / 25 / 50 / 100 шт. в один печатный <code>.eps</code> на ширину рулона и считает себестоимость плёнки/чернил;\n"
        "• 🚀 <b>Отправка в Hot Folder плоттера:</b> в 1 клик кидает готовый <code>.eps</code> в сетевую папку <code>\\\\192.168.0.108\\Roland_HotFolder</code>.\n\n"
        f"⚙️ <b>Ваши текущие настройки:</b>\n"
        f"• Размер большей стороны: <b>{prefs['size_mm']:g} мм</b> ({prefs['size_mm']/10.0:g} см)\n"
        f"• Отступ контура: <b>{prefs['offset_mm']:g} мм</b>\n"
        f"• Резка: <b>{PERF_MODE_LABELS.get(prefs.get('perf_cut_mode', 'none'))}</b>\n"
        f"• Тираж в раскладке: <b>{prefs.get('nesting_qty', 1)} шт.</b>\n\n"
        "💡 <i>Вы также можете просто написать в чат число (например, <code>8.5 см</code> или <code>120 мм</code>), чтобы задать точный нестандартный размер!</i>\n\n"
        f"🔒 <b>Конфиденциальность:</b> файлы обрабатываются на сервере и хранятся {retention_days_str} ({int(JOB_RETENTION_HOURS)} ч.). "
        "Администратор может просматривать их для обработки заказа. "
        "Подробнее: /privacy | Удалить данные: /delete_my_data"
    )
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📦 База стикерпаков и наклеек", callback_data="harv:home"),
        InlineKeyboardButton("📂 По категориям", callback_data="harv:cats"),
    )
    if is_admin(user.id):
        kb.row(
            InlineKeyboardButton("🔥 Заказать тему вне очереди", callback_data="harv:order_prompt"),
            InlineKeyboardButton("👑 Админ-панель (/admin)", callback_data="adm:home"),
        )
    kb.row(
        InlineKeyboardButton("🔒 Конфиденциальность", callback_data="user:privacy"),
        InlineKeyboardButton("🗑 Удалить данные", callback_data="user:delete_my_data"),
    )
    bot.reply_to(message, text, reply_markup=kb)


@bot.message_handler(commands=["privacy"])
def handle_privacy(message: Message) -> None:
    user = message.from_user
    record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="/privacy",
    )
    if is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    text = format_privacy_text()
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🗑 Удалить мои данные", callback_data="user:delete_my_data"),
    )
    bot.reply_to(message, text, reply_markup=kb)


@bot.message_handler(commands=["delete_my_data"])
def handle_delete_my_data(message: Message) -> None:
    user = message.from_user
    if is_user_banned(user.id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    res = delete_user_data(user.id)
    cnt = res.get("deleted_jobs", 0)
    jobs_info = f"<b>{cnt} шт.</b>" if cnt > 0 else "нет активных файлов"
    text = (
        "🗑 <b>Все ваши персональные данные успешно удалены:</b>\n\n"
        f"• Макеты и задания на сервере: {jobs_info}\n"
        "• Профиль пользователя и история действий удалены\n"
        "• Пользовательские настройки сброшены\n\n"
        "Ваши файлы больше не хранятся на сервере. При следующей отправке макета профиль будет создан заново."
    )
    bot.reply_to(message, text)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("user:"))
def handle_user_callback(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "⛔ Ваш доступ ограничен.", show_alert=True)
        return

    if action == "privacy":
        bot.answer_callback_query(call.id)
        text = format_privacy_text()
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🗑 Удалить мои данные", callback_data="user:delete_my_data"),
        )
        if call.message.content_type == "text":
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
        return

    if action == "delete_my_data":
        res = delete_user_data(user_id)
        cnt = res.get("deleted_jobs", 0)
        jobs_info = f"<b>{cnt} шт.</b>" if cnt > 0 else "нет активных файлов"
        bot.answer_callback_query(call.id, "Ваши данные удалены")
        text = (
            "🗑 <b>Все ваши персональные данные успешно удалены:</b>\n\n"
            f"• Макеты и задания на сервере: {jobs_info}\n"
            "• Профиль пользователя и история действий удалены\n"
            "• Пользовательские настройки сброшены\n\n"
            "Ваши файлы больше не хранятся на сервере."
        )
        if call.message.content_type == "text":
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id)
        else:
            bot.send_message(chat_id, text)
        return


register_harvester_bot_handlers(bot, is_admin, is_user_banned)


def parse_size_text(text: str) -> Tuple[float, ...]:
    """Разбирает текстовое описание размера стикера или габарита ШxВ в миллиметры.

    Возвращает:
      - (w_mm, h_mm) для прямоугольных габаритов вида '20x10 см', '150x90 мм', '2x1.5 м'
      - (size_mm,) для одиночного размера вида '15 см', '150 мм', '2 м'
      - () если текст не является корректным описанием размера
    """
    raw_txt = (text or "").strip()
    txt = raw_txt.lower()
    if txt.startswith("/"):
        return ()

    # 1. Проверяем ввод вида ШxВ (например: '12x8 см', '12х8', '150x90 мм', '2x1.5 м')
    m_rect = re.match(
        r"^(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*(см|cm|мм|mm|м|m)?$",
        txt,
    )
    if m_rect:
        w_val = float(m_rect.group(1).replace(",", "."))
        h_val = float(m_rect.group(2).replace(",", "."))
        unit = m_rect.group(3) or "см"
        if unit in ("м", "m"):
            w_mm, h_mm = w_val * 1000.0, h_val * 1000.0
        elif unit in ("см", "cm"):
            w_mm, h_mm = w_val * 10.0, h_val * 10.0
        else:
            w_mm, h_mm = w_val, h_val
        w_mm = max(5.0, round(w_mm, 1))
        h_mm = max(5.0, round(h_mm, 1))
        return (w_mm, h_mm)

    # 2. Обычный ввод одной стороны ('8.5 см', '120 мм', '2.5 м')
    m = re.match(r"^(\d+(?:[.,]\d+)?)\s*(см|cm|мм|mm|м|m)?$", txt)
    if m:
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2) or "см"
        if unit in ("м", "m"):
            size_mm = val * 1000.0
        elif unit in ("см", "cm"):
            size_mm = val * 10.0
        else:
            size_mm = val
        size_mm = max(5.0, round(size_mm, 1))
        return (size_mm,)

    return ()


@bot.message_handler(content_types=["text"])
def handle_custom_size_text(message: Message) -> None:
    """Позволяет написать в чат размер ('8.5 см', '120 мм' или '12x8 см') или срочную тему (для админа)."""
    raw_txt = (message.text or "").strip()
    txt = raw_txt.lower()
    if txt.startswith("/"):
        return
    user = message.from_user
    if is_user_banned(user.id):
        return

    if is_admin(user.id) and check_and_consume_waiting_topic(user.id):
        enqueue_and_run_urgent_topic(bot, message.chat.id, raw_txt)
        return

    parsed = parse_size_text(raw_txt)

    if len(parsed) == 2:
        w_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[0]))
        h_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[1]))
        size_mm = max(w_mm, h_mm)
        uinfo = record_user_activity(
            user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            action=f"Задан размер {w_mm:g}×{h_mm:g} мм",
        )
        last_jid = uinfo.get("last_job_id", "")
        job_dir = JOBS_DIR / last_jid if last_jid else None

        sx, sy = w_mm / size_mm, h_mm / size_mm
        if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
            meta = load_json_file(job_dir / "meta.json", {})
            orig_w = float(meta.get("single_w_mm") or meta.get("width_mm") or 100.0)
            orig_h = float(meta.get("single_h_mm") or meta.get("height_mm") or 100.0)
            curr_sx = float(meta.get("stretch_x", 1.0))
            curr_sy = float(meta.get("stretch_y", 1.0))
            base_w = orig_w / max(0.05, curr_sx)
            base_h = orig_h / max(0.05, curr_sy)
            if base_w > 1 and base_h > 1:
                base_longest = max(base_w, base_h)
                base_w_at_sz = (base_w / base_longest) * size_mm
                base_h_at_sz = (base_h / base_longest) * size_mm
                sx = w_mm / max(1.0, base_w_at_sz)
                sy = h_mm / max(1.0, base_h_at_sz)

        sx = max(MIN_STRETCH, min(MAX_STRETCH, round(sx, 4)))
        sy = max(MIN_STRETCH, min(MAX_STRETCH, round(sy, 4)))

        update_user_prefs(
            user.id,
            size_mm=size_mm,
            stretch_x=sx,
            stretch_y=sy,
            fit_a4_sheet=False,
        )
        if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
            meta = load_json_file(job_dir / "meta.json", {})
            if not is_admin(user.id) and int(meta.get("user_id", 0)) != user.id:
                bot.reply_to(message, "⛔ Это не ваш макет.")
                return

            can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user.id)
            if not can_convert:
                if limit_reason == "concurrent":
                    bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
                else:
                    bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
                return

            status_msg = bot.reply_to(
                message,
                f"📐 Растягиваю макет под габарит <b>~{w_mm/10.0:g} × {h_mm/10.0:g} см</b>...",
            )

            def on_queue_rect() -> None:
                try:
                    bot.edit_message_text(
                        "⏳ <b>Сервер занят.</b> Макет в очереди на обработку...",
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id,
                    )
                except Exception:
                    pass

            with conversion_limiter.run_conversion_slot(user.id, on_queued=on_queue_rect):
                process_and_send_job(
                    chat_id=message.chat.id,
                    user_id=user.id,
                    job_dir=job_dir,
                    status_msg_id=status_msg.message_id,
                    was_compressed_photo=False,
                    action_title=f"Габарит {w_mm/10.0:g}×{h_mm/10.0:g} см",
                )
        else:
            bot.reply_to(
                message,
                f"✅ Задан габарит: <b>{w_mm/10.0:g} × {h_mm/10.0:g} см</b>. Теперь отправьте картинку!",
            )
        return

    if not parsed:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("📦 Открыть базу стикерпаков", callback_data="harv:home"),
            InlineKeyboardButton("📂 По категориям", callback_data="harv:cats"),
        )
        if is_admin(user.id):
            kb.row(InlineKeyboardButton("🔥 Заказать тему вне очереди", callback_data="harv:order_prompt"))
        bot.reply_to(
            message,
            "📎 <b>Онлайн-конвертер:</b> отправьте любую картинку (фото или файл без сжатия), чтобы получить готовый <code>.eps</code> с контуром реза.\n"
            "📐 <b>Размер макета:</b> напишите желаемый размер, например: <code>8.5 см</code>, <code>120 мм</code>, <code>1.2 м</code> или <code>12x8 см</code> (до 1370 мм).\n"
            "📦 <b>Готовые стикерпаки:</b> нажмите кнопку ниже, чтобы выбрать готовые макеты из базы!",
            reply_markup=kb,
        )
        return

    size_mm = max(MIN_SIZE_MM, min(MAX_SIZE_MM, parsed[0]))

    update_user_prefs(user.id, size_mm=size_mm, fit_a4_sheet=False)
    uinfo = record_user_activity(
        user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action=f"Задан размер {size_mm:g} мм",
    )

    last_jid = uinfo.get("last_job_id", "")
    job_dir = JOBS_DIR / last_jid if last_jid else None
    if job_dir and job_dir.exists() and (job_dir / "meta.json").exists():
        meta = load_json_file(job_dir / "meta.json", {})
        if not is_admin(user.id) and int(meta.get("user_id", 0)) != user.id:
            bot.reply_to(message, "⛔ Это не ваш макет.")
            return

        can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user.id)
        if not can_convert:
            if limit_reason == "concurrent":
                bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
            else:
                bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
            return

        status_msg = bot.reply_to(
            message,
            f"📐 Установлен размер <b>{size_mm:g} мм ({size_mm/10.0:g} см)</b>. Пересчитываю ваш последний макет...",
        )

        def on_queue_single() -> None:
            try:
                bot.edit_message_text(
                    "⏳ <b>Сервер занят.</b> Макет в очереди на обработку...",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        with conversion_limiter.run_conversion_slot(user.id, on_queued=on_queue_single):
            process_and_send_job(
                chat_id=message.chat.id,
                user_id=user.id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=False,
                action_title=f"Размер {size_mm:g} мм",
            )
    else:
        bot.reply_to(
            message,
            f"✅ Установлен размер по умолчанию: <b>{size_mm:g} мм ({size_mm/10.0:g} см)</b>.\nТеперь отправьте картинку!",
        )


@bot.message_handler(content_types=["photo", "document"])
def handle_media(message: Message) -> None:
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id

    if is_user_banned(user_id):
        bot.reply_to(message, "⛔ Ваш доступ к боту ограничен администратором.")
        return

    was_compressed_photo = False
    orig_filename = "sticker.jpg"

    if message.content_type == "photo":
        was_compressed_photo = True
        photo_obj = message.photo[-1]
        if photo_obj.file_size and photo_obj.file_size > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            return
        file_id = photo_obj.file_id
        orig_filename = f"photo_{int(time.time())}.jpg"
    elif message.content_type == "document":
        doc = message.document
        orig_filename = doc.file_name or f"file_{int(time.time())}.png"
        ext = Path(orig_filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            bot.reply_to(
                message,
                f"⚠️ Формат <code>{html.escape(ext)}</code> не поддерживается.\nОтправьте изображение: JPG, PNG, WebP, BMP или TIFF.",
            )
            return
        if doc.file_size and doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            return
        file_id = doc.file_id
    else:
        return

    can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user_id)
    if not can_convert:
        if limit_reason == "concurrent":
            bot.reply_to(message, "⏳ У вас уже выполняется конвертация. Пожалуйста, дождитесь её завершения.")
        else:
            bot.reply_to(message, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите немного.")
        return

    # При загрузке нового макета сбрасываем тираж на 1 шт, пропорции на 1:1 и открываем главное меню
    curr_p = get_user_prefs(user_id)
    new_off = curr_p.get("offset_mm", 2.5)
    if new_off <= 0.0:
        new_off = 2.5
    update_user_prefs(
        user_id,
        nesting_qty=1,
        keep_background=False,
        vectorize=False,
        offset_mm=new_off,
        stretch_x=1.0,
        stretch_y=1.0,
        fit_a4_sheet=False,
        menu_tab="main",
    )

    status_msg = bot.reply_to(
        message,
        "⏳ <b>Обрабатываю макет на сервере crybaby...</b>\nВыделяю стикеры, строю контуры Roland VP-540 и примеряю на лист А4.",
    )

    job_id = uuid.uuid4().hex[:10]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    record_user_activity(
        user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        action="Загрузка изображения",
        last_job_id=job_id,
    )

    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        if len(downloaded) > MAX_UPLOAD_MB * 1024 * 1024:
            bot.reply_to(message, f"⛔ Файл слишком большой. Максимальный размер загрузки: {MAX_UPLOAD_MB:g} МБ.")
            conversion_limiter.release_user_slot(user_id, record_completed=False)
            return

        ext = Path(orig_filename).suffix.lower() or ".jpg"
        safe_stem = (
            re.sub(r"[^\w\-. ()А-Яа-яЁё]+", "_", Path(orig_filename).stem).strip()
            or "sticker"
        )
        input_filename = f"input{ext}"
        input_path = job_dir / input_filename
        input_path.write_bytes(downloaded)

        meta = {
            "job_id": job_id,
            "user_id": user_id,
            "orig_stem": safe_stem,
            "input_filename": input_filename,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        save_json_file(job_dir / "meta.json", meta)

        def on_queue_media() -> None:
            try:
                bot.edit_message_text(
                    "⏳ <b>Сервер занят.</b> Ваш макет в очереди на обработку...",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        with conversion_limiter.run_conversion_slot(user_id, on_queued=on_queue_media):
            process_and_send_job(
                chat_id=chat_id,
                user_id=user_id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=was_compressed_photo,
                action_title="Новая конвертация",
            )
    except DecompressionBombError:
        conversion_limiter.release_user_slot(user_id, record_completed=False)
        logger.warning("DecompressionBombError in handle_media for user %s", user_id)
        bot.send_message(
            chat_id,
            "⚠️ <b>Файл слишком большой или повреждён (превышен лимит пикселей).</b>\n"
            "Пожалуйста, уменьшите разрешение изображения.",
        )
    except Exception as exc:
        conversion_limiter.release_user_slot(user_id, record_completed=False)
        logger.exception("Error in handle_media for user %s", user_id)
        if is_admin(user_id):
            bot.send_message(
                chat_id,
                f"❌ <b>Ошибка при обработке файла:</b>\n<code>{html.escape(str(exc))}</code>",
            )
        else:
            bot.send_message(
                chat_id,
                "❌ <b>Не удалось обработать файл.</b>",
            )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("job:"))
def handle_job_callback(call: CallbackQuery) -> None:
    job_id = ""
    user_id = 0
    try:
        user = call.from_user
        user_id = user.id
        if is_user_banned(user_id):
            bot.answer_callback_query(call.id, "⛔ Доступ ограничен", show_alert=True)
            return

        parts = call.data.split(":")
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "Неверная команда")
            return

        _, job_id, param, val_str = parts
        chat_id = call.message.chat.id
        job_dir = JOBS_DIR / job_id
        meta_file = job_dir / "meta.json"

        if not job_dir.exists() or not meta_file.exists():
            bot.answer_callback_query(call.id, "⚠️ Исходный файл этой задачи уже очищен из кэша.")
            return

        meta = load_json_file(meta_file, {})
        job_owner_id = int(meta.get("user_id", 0))
        if not is_admin(user_id) and job_owner_id != user_id:
            bot.answer_callback_query(call.id, "⛔ Это не ваш макет", show_alert=True)
            return

        if param == "noop":
            bot.answer_callback_query(
                call.id,
                "💡 Вы также можете написать любой размер в чат (например: 15 см, 1.2 м или 20x10 см)",
            )
            return

        # Мгновенное переключение вложенных подменю прямо под фотографией без пересчёта файла
        if param == "nav":
            update_user_prefs(user_id, menu_tab=val_str)
            prefs = get_user_prefs(user_id)
            a4_q = int((meta.get("pricing") or {}).get("a4_fit_qty", 1))
            new_kb = build_job_keyboard(
                job_id,
                prefs,
                in_hot_folder=bool(meta.get("in_hot_folder")),
                a4_fit_qty=a4_q,
                menu_tab=val_str,
                hot_request_status=meta.get("hot_folder_request_status"),
            )
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=new_kb,
                )
            except Exception:
                pass
            bot.answer_callback_query(call.id)
            return

        if param == "hot":
            if is_admin(user_id) or ALLOW_USER_HOT_FOLDER:
                dest = copy_job_to_hot_folder(job_id)
                if dest is None:
                    bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🚀 Отправлено в Hot Folder плоттера!")
                bot.send_message(
                    chat_id,
                    f"🚀 <b>Макет отправлен на ПК с плоттером (Hot Folder)!</b>\n"
                    f"📄 Файл: <code>{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Windows: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\{html.escape(dest.name)}</code>\n"
                    f"📂 Сетевой путь Mac: <code>smb://192.168.0.108/Roland_HotFolder/Queue_EPS/{html.escape(dest.name)}</code>",
                )
                return
            else:
                if meta.get("hot_folder_request_status") == "pending":
                    bot.answer_callback_query(
                        call.id, "Заявка уже ожидает подтверждения администратором", show_alert=True
                    )
                    return

                meta["hot_folder_request_status"] = "pending"
                meta["hot_folder_requested_at"] = time.time()
                save_json_file(meta_file, meta)

                bot.answer_callback_query(call.id, "Заявка отправлена на согласование администратору!")
                bot.send_message(
                    chat_id,
                    "⏳ <b>Заявка на печать отправлена администратору!</b>\n"
                    "После подтверждения макет будет автоматически отправлен на плоттер.",
                )

                kb = InlineKeyboardMarkup()
                kb.row(
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"hreq:{job_id}:approve"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"hreq:{job_id}:reject"),
                )
                user_name = user.username and f"@{user.username}" or f"{user.first_name} ({user_id})"
                eps_name = f"{meta.get('orig_stem', 'sticker')}.eps"
                w_mm = meta.get("width_mm", "?")
                h_mm = meta.get("height_mm", "?")
                qty = meta.get("nesting_qty", 1)
                admin_text = (
                    f"📥 <b>Заявка на отправку в Hot Folder</b>\n"
                    f"👤 От: <b>{html.escape(str(user_name))}</b> (ID: <code>{user_id}</code>)\n"
                    f"📄 Файл: <code>{html.escape(eps_name)}</code>\n"
                    f"📐 Размер: <b>{w_mm} × {h_mm} мм</b> | Тираж: <b>{qty} шт.</b>\n"
                    f"🆔 Задача: <code>{job_id}</code>"
                )
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(admin_id, admin_text, reply_markup=kb)
                    except Exception as e:
                        logger.warning("Failed to notify admin %s: %s", admin_id, e)
                return

        curr_prefs = get_user_prefs(user_id)
        action_desc = "Пересчёт параметров"
        if param == "off":
            update_user_prefs(user_id, offset_mm=float(val_str), menu_tab="cut")
            action_desc = f"Кант {val_str} мм"
            bot.answer_callback_query(call.id, f"Отступ изменён на {val_str} мм. Пересчитываю...")
        elif param == "sz":
            val_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, float(val_str)))
            update_user_prefs(user_id, size_mm=val_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {val_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер изменён на {val_sz/10.0:g} см. Пересчитываю...")
        elif param == "dsz":
            delta_mm = float(val_str)
            new_sz = max(MIN_SIZE_MM, min(MAX_SIZE_MM, round(float(curr_prefs.get("size_mm", 100.0)) + delta_mm, 1)))
            update_user_prefs(user_id, size_mm=new_sz, fit_a4_sheet=False, menu_tab="size")
            action_desc = f"Размер {new_sz/10.0:g} см"
            bot.answer_callback_query(call.id, f"Размер: {new_sz/10.0:g} см. Пересчитываю...")
        elif param == "stx":
            mult = float(val_str)
            new_sx = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_prefs.get("stretch_x", 1.0)) * mult, 4)))
            update_user_prefs(user_id, stretch_x=new_sx, menu_tab="stretch")
            action_desc = f"Ширина ×{new_sx:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб ширины: ×{new_sx:.3g}. Пересчитываю...")
        elif param == "sty":
            mult = float(val_str)
            new_sy = max(MIN_STRETCH, min(MAX_STRETCH, round(float(curr_prefs.get("stretch_y", 1.0)) * mult, 4)))
            update_user_prefs(user_id, stretch_y=new_sy, menu_tab="stretch")
            action_desc = f"Высота ×{new_sy:.3g}"
            bot.answer_callback_query(call.id, f"Масштаб высоты: ×{new_sy:.3g}. Пересчитываю...")
        elif param == "rst":
            update_user_prefs(user_id, stretch_x=1.0, stretch_y=1.0, menu_tab="stretch")
            action_desc = "Сброс пропорций 1:1"
            bot.answer_callback_query(call.id, "Пропорции сброшены 1:1. Пересчитываю...")
        elif param == "a4":
            use_a4 = (val_str == "1")
            update_user_prefs(user_id, fit_a4_sheet=use_a4, nesting_qty=1)
            action_desc = "Раскладка на лист А4 (210×297 мм)" if use_a4 else "Режим 1 наклейки"
            bot.answer_callback_query(call.id, f"{action_desc}...")
        elif param == "perf":
            update_user_prefs(user_id, perf_cut_mode=val_str, menu_tab="cut")
            action_desc = PERF_MODE_LABELS.get(val_str, val_str)
            bot.answer_callback_query(call.id, f"Режим резки: {action_desc}. Пересчитываю...")
        elif param == "ppass":
            p_cnt = max(1, min(5, int(val_str)))
            new_pmode = curr_prefs.get("perf_cut_mode", "none")
            if new_pmode == "none" and not curr_prefs.get("fit_a4_sheet", False):
                new_pmode = "die_cut"
            update_user_prefs(
                user_id,
                perf_cut_passes=p_cnt,
                perf_cut_mode=new_pmode,
                menu_tab="cut",
            )
            action_desc = f"Сквозной рез PerfCut: {p_cnt} прохода(ов)"
            bot.answer_callback_query(call.id, f"{action_desc}. Пересчитываю...")
        elif param == "qty":
            q = int(val_str)
            update_user_prefs(user_id, nesting_qty=q, fit_a4_sheet=False, menu_tab="layout")
            action_desc = f"Раскладка тиража {q} шт. на рулон"
            bot.answer_callback_query(call.id, f"Собираю раскладку {q} шт. на рулон...")
        elif param == "bg":
            update_user_prefs(user_id, keep_background=(val_str == "1"))
            action_desc = "Изменён режим фона"
            bot.answer_callback_query(call.id, "Режим фона изменён. Пересчитываю...")
        elif param == "vec":
            update_user_prefs(user_id, vectorize=False)
            action_desc = "Оригинальное качество"
            bot.answer_callback_query(call.id, "Пересчитываю...")

        record_user_activity(
            user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            action=action_desc,
            last_job_id=job_id,
        )

        can_convert, limit_reason = conversion_limiter.check_and_acquire_user_slot(user_id)
        if not can_convert:
            if limit_reason == "concurrent":
                bot.answer_callback_query(
                    call.id, "⏳ Предыдущая конвертация ещё выполняется. Пожалуйста, дождитесь завершения.", show_alert=True
                )
            else:
                bot.answer_callback_query(
                    call.id, "⏳ Лимит: не более 6 конвертаций в минуту. Пожалуйста, подождите.", show_alert=True
                )
            return

        status_msg = bot.send_message(
            chat_id,
            f"🔄 <b>{action_desc}: пересчитываю EPS...</b>",
        )

        def on_queue_cb() -> None:
            try:
                bot.edit_message_text(
                    f"⏳ <b>Сервер занят ({action_desc}).</b> Макет в очереди на обработку...",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            except Exception:
                pass

        with conversion_limiter.run_conversion_slot(user_id, on_queued=on_queue_cb):
            process_and_send_job(
                chat_id=chat_id,
                user_id=user_id,
                job_dir=job_dir,
                status_msg_id=status_msg.message_id,
                was_compressed_photo=False,
                action_title=action_desc,
            )
    except Exception as exc:
        logger.exception("Error in handle_job_callback for job %s, user %s", job_id, user_id)
        if is_admin(user_id):
            bot.answer_callback_query(call.id, f"Ошибка: {exc}", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ Не удалось обработать файл", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("hreq:"))
def handle_hot_folder_request_callback(call: CallbackQuery) -> None:
    admin_user = call.from_user
    if not is_admin(admin_user.id):
        bot.answer_callback_query(call.id, "⛔ Только для администраторов", show_alert=True)
        return

    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Неверная команда")
        return

    _, job_id, action = parts
    job_dir = JOBS_DIR / job_id
    meta_file = job_dir / "meta.json"
    if not meta_file.exists():
        bot.answer_callback_query(call.id, "⚠️ Задача не найдена", show_alert=True)
        return

    meta = load_json_file(meta_file, {})
    target_user_id = int(meta.get("user_id", 0))

    if action == "approve":
        dest = copy_job_to_hot_folder(job_id)
        if dest is None:
            bot.answer_callback_query(call.id, "⚠️ Файл .eps не найден", show_alert=True)
            return
        meta["hot_folder_request_status"] = "approved"
        meta["hot_folder_approved_by"] = admin_user.id
        save_json_file(meta_file, meta)

        bot.answer_callback_query(call.id, "✅ Заявка одобрена!")
        try:
            admin_name = admin_user.first_name or str(admin_user.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ <b>Заявка одобрена</b> администратором {html.escape(admin_name)}!\n"
                     f"Файл <code>{html.escape(dest.name)}</code> скопирован в Hot Folder.",
            )
        except Exception:
            pass

        if target_user_id:
            try:
                bot.send_message(
                    target_user_id,
                    f"🚀 <b>Администратор одобрил печать макета!</b>\n"
                    f"📄 Файл отправлен на плоттер: <code>{html.escape(dest.name)}</code>",
                )
            except Exception as e:
                logger.warning("Failed to notify user %s about approval: %s", target_user_id, e)

    elif action == "reject":
        meta["hot_folder_request_status"] = "rejected"
        meta["hot_folder_rejected_by"] = admin_user.id
        save_json_file(meta_file, meta)

        bot.answer_callback_query(call.id, "❌ Заявка отклонена")
        try:
            admin_name = admin_user.first_name or str(admin_user.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ <b>Заявка отклонена</b> администратором {html.escape(admin_name)}.",
            )
        except Exception:
            pass

        if target_user_id:
            try:
                bot.send_message(
                    target_user_id,
                    f"❌ <b>Администратор отклонил отправку макета {job_id} в Hot Folder.</b>",
                )
            except Exception as e:
                logger.warning("Failed to notify user %s about rejection: %s", target_user_id, e)


# ==============================================================================
# ВЕБ-ПАНЕЛЬ АДМИНИСТРАТОРА В БРАУЗЕРЕ (ПОРТ 8090)
# ==============================================================================


class AdminWebHandler(BaseHTTPRequestHandler):
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
        err_html = (
            f'<div style="background:#7f1d1d;color:#fecaca;padding:10px 14px;border-radius:8px;margin-bottom:14px;font-size:13px;">{html.escape(error_msg)}</div>'
            if error_msg
            else ""
        )
        page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>🔐 Вход администратора — Roland VP-540</title>
<style>
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0f1117; color: #f1f5f9;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 20px;
  }}
  .box {{
    background: #181b24; border: 1px solid #2a2f3d; border-radius: 16px;
    padding: 28px; width: 100%; max-width: 400px;
  }}
  h2 {{ margin: 0 0 8px 0; font-size: 20px; }}
  p {{ margin: 0 0 18px 0; color: #94a3b8; font-size: 13.5px; line-height: 1.45; }}
  input[type="password"] {{
    width: 100%; box-sizing: border-box; background: #0f1117; border: 1px solid #2a2f3d;
    color: #fff; padding: 11px 14px; border-radius: 10px; font-size: 15px; margin-bottom: 14px;
  }}
  button {{
    width: 100%; background: #ec008c; color: #fff; border: none; padding: 11px;
    border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer;
  }}
  .back {{
    display: block; text-align: center; margin-top: 14px; color: #94a3b8;
    text-decoration: none; font-size: 13px;
  }}
</style>
</head>
<body>
  <div class="box">
    <h2>🔐 Вход для администратора</h2>
    <p>Каталог стикеров открыт для всех, а управление парсером и просмотр чужих работ доступны по паролю админа.</p>
    {err_html}
    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{html.escape(next_url)}" />
      <input type="password" name="pw" placeholder="Введите пароль админа..." autofocus required />
      <button type="submit">Войти в панель управления</button>
    </form>
    <a class="back" href="/harvester">⬅️ Вернуться в публичный каталог стикеров</a>
  </div>
</body>
</html>"""
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

        # Mutating endpoints must NOT execute via GET
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
            if not PUBLIC_CATALOG and not is_admin_user:
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
            if not PUBLIC_CATALOG and not is_admin_user:
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
            if pw and hmac.compare_digest(pw.encode("utf-8"), ADMIN_WEB_PASSWORD.encode("utf-8")):
                login_rate_limiter.record_success(client_ip)
                session_id, _ = web_session_store.create_session()
                self.send_response(302)
                self.send_session_cookie(session_id)
                self.send_header("Location", next_url)
                self.end_headers()
                return

            is_now_blocked, remaining = login_rate_limiter.record_failure(client_ip)
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

        # Check CSRF token
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
                enqueue_and_run_urgent_topic(bot, list(ADMIN_IDS)[0], t_raw)
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
            dest = copy_harvester_asset_to_hot_folder(aid)
            fname = dest.name if dest else "ошибка"
            m = f"🚀 Макет {fname} отправлен в сетевую папку плоттера Roland_HotFolder!"
            self.send_response(302)
            self.send_header("Location", f"/harvester?msg={urllib.parse.quote(m)}")
            self.end_headers()
            return

        if path.startswith("/send_hot/"):
            job_id = post_params.get("job_id", [""])[0] or path.strip("/").split("/")[-1]
            dest = copy_job_to_hot_folder(job_id)
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
        jdir = JOBS_DIR / job_id
        meta = load_json_file(jdir / "meta.json", {})
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
        users = load_json_file(USERS_FILE, {})
        filter_uid = (
            int(filter_uid_str)
            if filter_uid_str != "all" and filter_uid_str.isdigit()
            else None
        )
        all_jobs = list_all_jobs(filter_user_id=filter_uid)
        total_jobs_all = len(list_all_jobs())
        total_contours = sum(int(j.get("num_contours", 1)) for j in all_jobs)
        hot_files = list(HOT_QUEUE_EPS_DIR.glob("*.eps"))

        user_pills = [
            f'<a class="pill {"active" if filter_uid is None else ""}" href="/?user=all">Все работы ({total_jobs_all})</a>'
        ]
        for u in sorted(users.values(), key=lambda x: float(x.get("last_seen", 0)), reverse=True):
            uid = int(u["user_id"])
            fn = html.escape(f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or str(uid))
            un = f" (@{html.escape(u['username'])})" if u.get("username") else ""
            cnt = len(list_all_jobs(filter_user_id=uid))
            active_cls = "active" if filter_uid == uid else ""
            user_pills.append(
                f'<a class="pill {active_cls}" href="/?user={uid}">👤 {fn}{un} ({cnt})</a>'
            )

        cards_html = []
        for j in all_jobs:
            jid = j["job_id"]
            uid = int(j.get("user_id", 0))
            uinfo = users.get(str(uid), {"user_id": uid, "first_name": f"User {uid}"})
            uname_html = format_user_trigger(uinfo)
            ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
            nc = j.get("num_contours", 1)
            off = j.get("offset_mm", 2.5)
            w_mm = j.get("width_mm", 100)
            h_mm = j.get("height_mm", 100)
            qty = j.get("nesting_qty", 1)
            perf_mode = j.get("perf_cut_mode", "none")
            size_mb = j.get("eps_size_mb", 0.0)
            calc = j.get("pricing", {})
            in_hot = bool(j.get("in_hot_folder"))
            last_act = html.escape(str(j.get("last_action", "Конвертация")))

            cards_html.append(
                f"""
                <div class="card">
                    <div class="card-header">
                        <div>
                            <div class="user-badge">👤 {uname_html}</div>
                            <div class="meta-time">🕒 {ts_str} • {last_act}</div>
                        </div>
                        <span class="contour-badge">✂️ {nc} конт. • {qty} шт</span>
                    </div>
                    <div class="preview-pair">
                        <a href="/files/{jid}/orig" target="_blank" class="img-box" title="Открыть исходник">
                            <span class="img-label">Исходник</span>
                            <img src="/files/{jid}/orig" loading="lazy" alt="orig" />
                        </a>
                        <a href="/files/{jid}/preview" target="_blank" class="img-box" title="Открыть превью контуров">
                            <span class="img-label magenta">CutContour / PerfCut</span>
                            <img src="/files/{jid}/preview" loading="lazy" alt="preview" />
                        </a>
                    </div>
                    <div class="card-specs">
                        <span>📐 <b>{w_mm:g} × {h_mm:g} мм</b> ({size_mb:.1f} МБ)</span>
                        <span>📏 Кант: <b>{off:g} мм</b></span>
                        <span>🔪 <b>{html.escape(PERF_MODE_LABELS.get(perf_mode, perf_mode))}</b></span>
                        <span>💰 Себест: <b>~{calc.get('cost_rub', 30)} ₽</b> | Клиенту: <b>~{calc.get('retail_rub', 200)} ₽</b></span>
                    </div>
                    <div class="card-actions">
                        <a class="btn btn-primary" href="/files/{jid}/eps">📥 Скачать .EPS</a>
                        <form method="POST" action="/send_hot/{jid}" style="margin:0;flex:1;display:flex;">
                            <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}" />
                            <button type="submit" class="btn btn-hot" style="width:100%;border:none;cursor:pointer;font-family:inherit;">{'✅ В Hot Folder' if in_hot else '🚀 В Hot Folder'}</button>
                        </form>
                        <a class="btn btn-secondary" href="/files/{jid}/orig" download>🖼</a>
                    </div>
                </div>
                """
            )

        toast_html = (
            f'<div style="background:#065f46;color:#fff;padding:12px 18px;border-radius:10px;margin-bottom:18px;font-weight:600;">🚀 Файл <code>{html.escape(hot_msg)}</code> скопирован в сетевую папку плоттера <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\</code>!</div>'
            if hot_msg
            else ""
        )

        page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="csrf-token" content="{html.escape(csrf_token)}" />
<meta http-equiv="refresh" content="25" />
<title>👑 Админ-панель Roland VP-540 — crybaby</title>
<style>
  :root {{
    --bg: #0f1117;
    --card: #181b24;
    --border: #2a2f3d;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --magenta: #ec008c;
    --cyan: #00aeef;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .top-bar {{
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
    gap: 16px; margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }}
  h1 {{ margin: 0; font-size: 21px; display: flex; align-items: center; gap: 10px; }}
  .stats-row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .stat-box {{
    background: var(--card); border: 1px solid var(--border);
    padding: 8px 13px; border-radius: 10px; font-size: 13px;
  }}
  .smb-banner {{
    background: rgba(0, 174, 239, 0.1); border: 1px solid rgba(0, 174, 239, 0.35);
    padding: 10px 16px; border-radius: 10px; font-size: 13px; margin-bottom: 18px;
    display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
  }}
  .pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }}
  .pill {{
    text-decoration: none; color: var(--text); background: var(--card);
    border: 1px solid var(--border); padding: 8px 14px; border-radius: 999px;
    font-size: 13px; transition: 0.15s;
  }}
  .pill:hover, .pill.active {{
    background: var(--magenta); border-color: var(--magenta); color: #fff; font-weight: 600;
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(390px, 1fr)); gap: 18px;
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 16px; display: flex; flex-direction: column; gap: 12px;
  }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
  .user-badge {{ font-weight: 600; font-size: 15px; }}
  .meta-time {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
  .contour-badge {{
    background: rgba(236, 0, 140, 0.18); color: #ff5cc0; border: 1px solid rgba(236, 0, 140, 0.4);
    padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap;
  }}
  .preview-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .img-box {{
    position: relative; background: #232734; border-radius: 10px; overflow: hidden;
    aspect-ratio: 1 / 1; display: flex; align-items: center; justify-content: center;
    border: 1px solid var(--border);
  }}
  .img-box img {{ width: 100%; height: 100%; object-fit: contain; }}
  .img-label {{
    position: absolute; top: 6px; left: 6px; background: rgba(0,0,0,0.72);
    color: #fff; font-size: 11px; padding: 2px 7px; border-radius: 6px;
  }}
  .img-label.magenta {{ background: var(--magenta); }}
  .card-specs {{
    display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12.5px; color: var(--muted);
    background: rgba(0,0,0,0.22); padding: 9px 12px; border-radius: 8px;
  }}
  .card-specs b {{ color: var(--text); }}
  .card-actions {{ display: flex; gap: 8px; margin-top: auto; }}
  .btn {{
    flex: 1; text-align: center; text-decoration: none; padding: 9px 10px;
    border-radius: 8px; font-size: 13px; font-weight: 600; transition: 0.15s;
  }}
  .btn-primary {{ background: var(--magenta); color: #fff; }}
  .btn-hot {{ background: #0284c7; color: #fff; }}
  .btn-secondary {{ background: #262b38; color: var(--text); flex: 0.25; }}
</style>
</head>
<body>
  <div class="top-bar">
    <h1>👑 Админ-панель Roland VP-540 <span style="font-size:13px;color:var(--muted);font-weight:400;">(сервер crybaby)</span></h1>
    <div class="stats-row">
      <a class="stat-box" href="/harvester" style="text-decoration:none;color:#fff;background:var(--magenta);border-color:var(--magenta);font-weight:600;">🚜 Открыть Базу Парсера 24/7 (Google Drive 5 ТБ)</a>
      <div class="stat-box">👥 Пользователей: <b>{len(users)}</b></div>
      <div class="stat-box">🗂 Работ: <b>{len(all_jobs)}</b></div>
      <div class="stat-box">✂️ Контуров: <b>{total_contours}</b></div>
      <div class="stat-box">🖨 В Hot Folder: <b>{len(hot_files)}</b></div>
      <a class="stat-box" href="/logout" style="text-decoration:none;color:#fca5a5;border-color:#ef4444;">🔓 Выйти</a>
    </div>
  </div>
  {toast_html}
  <div class="smb-banner">
    <span>🖨 <b>Сетевая папка Hot Folder для ПК с плоттером:</b></span>
    <span>Windows: <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS</code></span>
    <span>Tailscale: <code>\\\\100.76.178.11\\Roland_HotFolder\\Queue_EPS</code></span>
    <span>Mac: <code>smb://192.168.0.108/Roland_HotFolder</code></span>
  </div>
  <div class="pills">
    {"".join(user_pills)}
  </div>
  <div class="grid">
    {"".join(cards_html) if cards_html else '<p style="color:var(--muted);">Пока нет сконвертированных файлов.</p>'}
  </div>
</body>
</html>"""
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_web_admin_server() -> None:
    try:
        server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), AdminWebHandler)
        logger.info("Веб-админка запущена на http://%s:%d", WEB_HOST, WEB_PORT)
        server.serve_forever()
    except Exception as exc:
        logger.error("Ошибка запуска веб-админки на %s:%d: %s", WEB_HOST, WEB_PORT, exc)


def start_cloudflared_tunnel_supervisor() -> None:
    import atexit
    import subprocess

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
                        set_state("public_web_url", new_url)
                        admin_key = get_or_create_admin_web_key()
                        print(f"🌍 Публичный HTTPS-туннель активен: {new_url}/harvester")

                        def _notify_or_edit(u: str = new_url, k: str = admin_key) -> None:
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
                                f"<a href=\"{html.escape(u)}/harvester\">{html.escape(u)}/harvester</a>\n\n"
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
                                        bot.edit_message_text(
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
                                        sent = bot.send_message(
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
            print(f"⚠️ Ошибка HTTPS-туннеля: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    print("🔄 Синхронизация истории работ и пользователей...")
    try:
        get_or_create_admin_web_key()
        backfill_existing_jobs_and_users()
        bot.set_my_commands(
            [
                BotCommand("start", "Инструкция, размеры и текущие настройки"),
                BotCommand("privacy", "🔒 Конфиденциальность и хранение данных"),
                BotCommand("delete_my_data", "🗑 Удалить все мои макеты и данные"),
                BotCommand("harvester", "🚜 Автономный парсер базы 24/7 (Google Drive 5 ТБ)"),
                BotCommand("parse", "🔥 Заказать срочный сбор стикерпака по теме"),
                BotCommand("admin", "👑 Админ-панель (чужие работы, Hot Folder, статистика)"),
                BotCommand("spy", "🔔 Включение/выключение Live-шпиона (Админ)"),
                BotCommand("help", "Как отправлять макеты и собирать тиражи на рулон"),
            ]
        )
    except Exception as exc:
        print(f"⚠️ Предупреждение при инициализации: {exc}")

    start_cleanup_scheduler(interval_seconds=3600.0)

    web_thread = threading.Thread(target=start_web_admin_server, daemon=True)
    web_thread.start()

    if ENABLE_PUBLIC_TUNNEL:
        tunnel_thread = threading.Thread(target=start_cloudflared_tunnel_supervisor, daemon=True)
        tunnel_thread.start()
    else:
        logger.info(
            "Публичный туннель выключен (ENABLE_PUBLIC_TUNNEL=false). "
            "Для безопасного удалённого доступа используйте Tailscale или Cloudflare Access."
        )

    print("🚀 Запуск Plotter EPS Telegram Bot (Roland VP-540 Edition)...")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
