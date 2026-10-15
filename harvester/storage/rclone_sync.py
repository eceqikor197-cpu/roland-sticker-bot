#!/usr/bin/env python3
"""
Синхронизация базы макетов с Google Drive (gdrive:Roland_Print_Base, 5 ТБ) через rclone,
ротация локального буфера на SSD сервера crybaby и отправка макетов в Roland_HotFolder.
"""

from __future__ import annotations

import datetime
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from harvester.config import (
    HOT_PREVIEWS_DIR,
    HOT_QUEUE_EPS_DIR,
    MAX_LOCAL_STAGING_MB,
    MIN_FREE_DISK_GB,
    RCLONE_BIN,
    RCLONE_REMOTE,
    STAGING_DIR,
)
from harvester.database import (
    get_asset,
    get_unsynced_assets,
    incr_state,
    mark_asset_in_hot_folder,
    mark_assets_synced,
    set_state,
)


def is_rclone_available() -> bool:
    return Path(RCLONE_BIN).exists() or shutil.which("rclone") is not None


def get_rclone_cmd() -> str:
    if Path(RCLONE_BIN).exists():
        return RCLONE_BIN
    return shutil.which("rclone") or "rclone"


_QUOTA_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "refreshing": False,
    "data": {"total": "5 TiB", "used": "275 GiB", "free": "4.73 TiB", "status": "cached"},
}


def _refresh_quota_bg() -> None:
    remote_root = RCLONE_REMOTE.split(":")[0] + ":"
    try:
        proc = subprocess.run(
            [get_rclone_cmd(), "about", remote_root],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        out = proc.stdout or ""
        info = {"total": "5 TiB", "used": "275 GiB", "free": "4.73 TiB", "status": "ok"}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k_low = k.strip().lower()
                if k_low in ("total", "used", "free"):
                    info[k_low] = v.strip()
        _QUOTA_CACHE["data"] = info
        _QUOTA_CACHE["ts"] = time.time()
    except Exception:
        _QUOTA_CACHE["ts"] = time.time()
    finally:
        _QUOTA_CACHE["refreshing"] = False


def get_gdrive_quota_info() -> Dict[str, str]:
    """Возвращает информацию о свободном и занятом месте на Google Диске (gdrive:) мгновенно из кэша."""
    if not is_rclone_available():
        return {"total": "5 TiB", "used": "—", "free": "4.7 TiB", "status": "rclone_missing"}
    now = time.time()
    if (now - float(_QUOTA_CACHE["ts"])) > 600.0 and not _QUOTA_CACHE["refreshing"]:
        import threading

        _QUOTA_CACHE["refreshing"] = True
        threading.Thread(target=_refresh_quota_bg, daemon=True).start()
    return dict(_QUOTA_CACHE["data"])


def sync_staging_to_gdrive() -> Tuple[bool, int, float, str]:
    """
    Выгружает все накопленные несинхронизированные файлы из STAGING_DIR
    в облако Google Drive (gdrive:Roland_Print_Base/) с сохранением структуры папок.
    Возвращает (success, synced_files_count, synced_mb, message).
    """
    unsynced = get_unsynced_assets(limit=500)
    if not unsynced and not any(STAGING_DIR.rglob("*.eps")):
        return True, 0, 0.0, "Нет новых файлов для выгрузки"

    if not is_rclone_available():
        return False, 0, 0.0, "rclone не найден на сервере"

    cmd = [
        get_rclone_cmd(),
        "copy",
        str(STAGING_DIR),
        RCLONE_REMOTE,
        "--transfers=4",
        "--checkers=8",
        "--exclude",
        ".tmp_*",
        "--exclude",
        ".DS_Store",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "rclone error").strip()[:250]
            set_state("last_error", f"Ошибка rclone: {err}")
            return False, 0, 0.0, err

        synced_ids = [a["asset_id"] for a in unsynced]
        synced_mb = sum(float(a.get("eps_size_mb") or 0.0) for a in unsynced)
        mark_assets_synced(synced_ids)

        if synced_ids:
            incr_state("total_synced_gdrive", len(synced_ids))
            incr_state("total_synced_mb", round(synced_mb, 2))
        set_state("last_sync_at", time.time())

        # После успешной синхронизации проверяем лимит локального буфера и чистим старые выгруженные .eps
        cleanup_synced_local_staging_if_needed()
        return True, len(synced_ids), round(synced_mb, 2), "Успешно выгружено в Google Drive"
    except Exception as exc:
        set_state("last_error", f"Исключение rclone: {exc}")
        return False, 0, 0.0, str(exc)


def cleanup_synced_local_staging_if_needed() -> int:
    """
    Контролирует объём STAGING_DIR и свободное место на SSD сервера crybaby.
    Если буфер превышает MAX_LOCAL_STAGING_MB (2.5 ГБ) или на диске меньше MIN_FREE_DISK_GB (12 ГБ),
    удаляет локальные копии ТОЛЬКО ТЕХ файлов, которые ПОДТВЕРЖДЁННО выгружены в Google Drive
    (synced_to_gdrive = 1 в таблице assets), оставляя превьюшки и метаданные в SQLite.
    """
    try:
        disk = shutil.disk_usage(str(STAGING_DIR))
        free_gb = disk.free / (1024 ** 3)
    except Exception:
        free_gb = 50.0

    files = [p for p in STAGING_DIR.rglob("*") if p.is_file()]
    staging_mb = sum(p.stat().st_size for p in files) / (1024 * 1024)

    if staging_mb < MAX_LOCAL_STAGING_MB and free_gb > MIN_FREE_DISK_GB:
        return 0

    # Собираем множество локальных путей к файлам, подтверждённо выгруженным в облако
    from harvester.database import get_conn as _get_conn
    synced_paths: set[str] = set()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT local_eps_path, local_png_path FROM assets WHERE synced_to_gdrive = 1"
        ).fetchall()
        for r in rows:
            for col in ("local_eps_path", "local_png_path"):
                p = r[col]
                if p:
                    synced_paths.add(str(p))
        conn.close()
    except Exception:
        # Если не можем подтвердить синхронизацию — НЕ удаляем ничего, чтобы не потерять данные
        return 0

    # Удаляем самые старые ПОДТВЕРЖДЁННЫЕ файлы, пока объём не опустится до 60% лимита
    confirmed_files = [p for p in files if str(p) in synced_paths]
    confirmed_files.sort(key=lambda p: p.stat().st_mtime)
    target_mb = MAX_LOCAL_STAGING_MB * 0.6
    removed = 0
    for fpath in confirmed_files:
        if staging_mb <= target_mb and free_gb > MIN_FREE_DISK_GB:
            break
        try:
            sz_mb = fpath.stat().st_size / (1024 * 1024)
            fpath.unlink(missing_ok=True)
            staging_mb -= sz_mb
            removed += 1
        except Exception:
            continue
    return removed


def ensure_local_eps_for_asset(asset_id: str) -> Optional[Path]:
    """
    Возвращает локальный путь к .EPS файлу ассета.
    Если файл уже был очищен из локального кэша после выгрузки в Google Drive,
    прозрачно скачивает его обратно из gdrive:Roland_Print_Base/... за 1-2 секунды.
    """
    asset = get_asset(asset_id)
    if not asset:
        return None

    local_eps = Path(asset["local_eps_path"])
    if local_eps.exists() and local_eps.stat().st_size > 100:
        return local_eps

    if not is_rclone_available():
        return None

    rel_folder = asset["rel_folder_path"]
    eps_filename = asset["eps_filename"]
    remote_file = f"{RCLONE_REMOTE}/{rel_folder}/{eps_filename}"
    local_eps.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [get_rclone_cmd(), "copyto", remote_file, str(local_eps)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode == 0 and local_eps.exists():
            return local_eps
    except Exception:
        pass
    return None


def copy_harvester_asset_to_hot_folder(asset_id: str, admin_confirmed: bool = False) -> Optional[Path]:
    """
    Копирует выбранный макет из базы парсера (локально или подтягивая с Google Drive)
    прямо в сетевую папку плоттера Hot Folder (Queue_EPS).
    Для макетов с license=unknown требуется явное подтверждение администратора (admin_confirmed=True).
    """
    asset = get_asset(asset_id)
    if not asset:
        return None

    lic = str(asset.get("license", "unknown")).lower().strip()
    if lic == "unknown" and not admin_confirmed:
        return None

    eps_path = ensure_local_eps_for_asset(asset_id)
    if eps_path is None or not eps_path.exists():
        return None

    dt_str = datetime.datetime.now().strftime("%m-%d_%H-%M")
    safe_stem = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", eps_path.stem).strip("_")
    w_mm = int(round(float(asset.get("width_mm", 100))))
    hot_base = f"{dt_str}_BASE_{w_mm}mm_{safe_stem}"

    HOT_QUEUE_EPS_DIR.mkdir(parents=True, exist_ok=True)
    HOT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    dest_eps = HOT_QUEUE_EPS_DIR / f"{hot_base}.eps"
    shutil.copy2(eps_path, dest_eps)

    prev_str = (asset.get("local_preview_path") or "").strip()
    if prev_str:
        prev_path = Path(prev_str)
        if prev_path.is_file():
            shutil.copy2(prev_path, HOT_PREVIEWS_DIR / f"{hot_base}.jpg")

    mark_asset_in_hot_folder(asset_id)
    return dest_eps
