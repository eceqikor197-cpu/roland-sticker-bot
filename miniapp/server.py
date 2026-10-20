"""HTTP-сервер мини-приложения «Редактор наклейки» (Telegram Mini App).

Отдаёт статику редактора и API для живого превью и генерации EPS.
Работает отдельным процессом рядом с ботом: та же база bot_storage.db и папка bot_workspace/jobs.
Готовый EPS отправляет в чат тем же кодом бота (bot.jobs.process_and_send_job), опрос Telegram не запускает.

Запуск: python -m miniapp   (MINIAPP_HOST / MINIAPP_PORT, по умолчанию 127.0.0.1:8096)
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import ADMIN_IDS, JOBS_DIR, PLOTTER_BOT_TOKEN
from miniapp import geometry
from miniapp.auth import InitDataError, validate_init_data
from miniapp.geometry import JobParams
from miniapp.source import artwork_path, prepare_source
from pricing import calculate_nesting_and_price, load_pricing_config
from storage import (
    get_job as storage_get_job,
    get_user_prefs as storage_get_user_prefs,
    is_user_banned as storage_is_user_banned,
    list_all_jobs as storage_list_all_jobs,
    update_job as storage_update_job,
    update_user_prefs as storage_update_user_prefs,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INIT_DATA_HEADER = "X-Telegram-Init-Data"

SIZE_PRESETS_MM = [30, 50, 70, 100, 150, 210, 297]
OFFSET_PRESETS_MM = [0, 1.5, 2.5, 4]
QTY_PRESETS = [10, 25, 50, 100]

# Параметры, которые редактор сохраняет в meta.json / storage
PARAM_KEYS = (
    "size_mm",
    "stretch_x",
    "stretch_y",
    "offset_mm",
    "keep_background",
    "perf_cut_mode",
    "perf_cut_passes",
    "nesting_qty",
    "fit_a4_sheet",
    "roll_width_mm",
)


class ParamsIn(BaseModel):
    size_mm: float = Field(100.0, ge=geometry.MIN_SIZE_MM, le=geometry.MAX_SIZE_MM)
    stretch_x: float = Field(1.0, ge=geometry.MIN_STRETCH, le=geometry.MAX_STRETCH)
    stretch_y: float = Field(1.0, ge=geometry.MIN_STRETCH, le=geometry.MAX_STRETCH)
    offset_mm: float = Field(2.5, ge=0.0, le=geometry.MAX_OFFSET_MM)
    keep_background: bool = False
    perf_cut_mode: str = Field("none", pattern="^(none|sheet_frame|die_cut)$")
    perf_cut_passes: int = Field(2, ge=1, le=5)
    nesting_qty: int = Field(1, ge=1, le=geometry.MAX_NESTING_QTY)
    fit_a4_sheet: bool = False
    roll_width_mm: float = Field(1370.0, ge=300.0, le=1600.0)


class Viewer(BaseModel):
    user_id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    is_admin: bool = False


def _job_params(meta: Dict[str, Any], prefs: Dict[str, Any]) -> JobParams:
    merged = dict(prefs)
    merged.update({k: meta[k] for k in PARAM_KEYS if meta.get(k) is not None})
    return JobParams.from_dict(merged)


def _quote(source: Dict[str, Any], params: JobParams) -> Dict[str, Any]:
    """Размеры и цена как в подписи бота (process_and_send_job), без конвертации."""
    geo = geometry.item_geometry(source["crop_w_px"], source["crop_h_px"], params)
    cw, ch = geo["cell_w_mm"], geo["cell_h_mm"]
    base = calculate_nesting_and_price(cw, ch, qty=max(params.nesting_qty, 25), roll_width_mm=params.roll_width_mm)
    qty = int(base["a4_fit_qty"]) if params.fit_a4_sheet else params.nesting_qty
    curr = calculate_nesting_and_price(cw, ch, qty=qty, roll_width_mm=params.roll_width_mm)
    return {"geometry": geo, "pricing": curr, "pricing_25": base}


def create_app(
    bot_token: Optional[str] = None,
    jobs_dir: Optional[Path] = None,
    admin_ids: Optional[set[int]] = None,
    deliver: Optional[Callable[..., None]] = None,
    converter: Optional[Callable[..., Any]] = None,
    max_per_minute: int = 6,
) -> FastAPI:
    """Собирает приложение. Аргументы нужны для тестов; в бою всё берётся из config и бота."""
    token = bot_token if bot_token is not None else PLOTTER_BOT_TOKEN
    jobs_root = Path(jobs_dir or JOBS_DIR)
    admins = set(admin_ids if admin_ids is not None else ADMIN_IDS)

    app = FastAPI(title="Roland Sticker Mini App", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.locks = {}
    app.state.locks_guard = threading.Lock()
    app.state.recent = {}

    def job_lock(job_id: str) -> threading.Lock:
        with app.state.locks_guard:
            return app.state.locks.setdefault(job_id, threading.Lock())

    def check_rate(user_id: int, is_admin: bool) -> None:
        """Не больше max_per_minute конвертаций в минуту на человека (как ConversionLimiter бота)."""
        if is_admin:
            return
        now = time.time()
        with app.state.locks_guard:
            stamps = [t for t in app.state.recent.get(user_id, []) if now - t < 60.0]
            if len(stamps) >= max_per_minute:
                raise HTTPException(429, f"Не больше {max_per_minute} конвертаций в минуту, подождите немного")
            stamps.append(now)
            app.state.recent[user_id] = stamps

    def viewer(request: Request) -> Viewer:
        init_data = request.headers.get(INIT_DATA_HEADER, "")
        try:
            user = validate_init_data(init_data, token)
        except InitDataError as exc:
            raise HTTPException(401, f"Откройте редактор из Telegram ({exc})") from exc
        uid = int(user["id"])
        is_admin = uid in admins
        if not is_admin and storage_is_user_banned(uid, admin_ids=list(admins)):
            raise HTTPException(403, "Доступ ограничен администратором")
        return Viewer(
            user_id=uid,
            first_name=str(user.get("first_name", "")),
            last_name=str(user.get("last_name", "")),
            username=str(user.get("username", "")),
            is_admin=is_admin,
        )

    def load_job(job_id: str, who: Viewer) -> tuple[Path, Dict[str, Any]]:
        if not job_id.isalnum() or len(job_id) > 64:
            raise HTTPException(404, "Макет не найден")
        job_dir = jobs_root / job_id
        meta = storage_get_job(job_id)
        if not meta or not job_dir.is_dir():
            raise HTTPException(404, "Макет не найден или уже удалён с сервера")
        if not who.is_admin and int(meta.get("user_id", 0)) != who.user_id:
            raise HTTPException(403, "Это не ваш макет")
        if not (job_dir / str(meta.get("input_filename", "input.jpg"))).exists():
            raise HTTPException(404, "Исходный файл макета уже удалён с сервера")
        return job_dir, meta

    def get_source(job_dir: Path, meta: Dict[str, Any], keep_bg: bool) -> Dict[str, Any]:
        with job_lock(job_dir.name):
            return prepare_source(job_dir, str(meta.get("input_filename", "input.jpg")), keep_bg)

    @app.get("/api/v1/config")
    def api_config(who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        cfg = load_pricing_config()
        return {
            "user": who.model_dump(),
            "limits": {
                "size_mm": [geometry.MIN_SIZE_MM, geometry.MAX_SIZE_MM],
                "stretch": [geometry.MIN_STRETCH, geometry.MAX_STRETCH],
                "offset_mm": [0.0, geometry.MAX_OFFSET_MM],
                "nesting_qty": [1, geometry.MAX_NESTING_QTY],
            },
            "presets": {"size_mm": SIZE_PRESETS_MM, "offset_mm": OFFSET_PRESETS_MM, "qty": QTY_PRESETS},
            "roll_widths_mm": list(geometry.ROLL_WIDTHS_MM),
            "a4": cfg.get("a4", {}),
            "roll": cfg.get("roll", {}),
        }

    @app.get("/api/v1/jobs")
    def api_jobs(who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        jobs = storage_list_all_jobs(filter_user_id=who.user_id, limit=30)
        items = []
        for j in jobs:
            job_dir = jobs_root / str(j.get("job_id", ""))
            if not job_dir.is_dir():
                continue
            items.append(
                {
                    "job_id": j["job_id"],
                    "name": j.get("orig_stem", "sticker"),
                    "width_mm": j.get("width_mm"),
                    "height_mm": j.get("height_mm"),
                    "updated_at": j.get("updated_at") or j.get("created_at"),
                    "has_preview": (job_dir / "preview_cutcontour.png").exists(),
                }
            )
        return {"jobs": items}

    @app.get("/api/v1/jobs/{job_id}")
    def api_job(job_id: str, who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        job_dir, meta = load_job(job_id, who)
        params = _job_params(meta, storage_get_user_prefs(int(meta.get("user_id", who.user_id))))
        source = get_source(job_dir, meta, params.keep_background)
        return {
            "job_id": job_id,
            "name": meta.get("orig_stem", "sticker"),
            "params": params.to_dict(),
            "source": source,
            "quote": _quote(source, params),
            "in_hot_folder": bool(meta.get("in_hot_folder")),
        }

    @app.get("/api/v1/jobs/{job_id}/source")
    def api_source(job_id: str, keep_bg: int = 0, who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        job_dir, meta = load_job(job_id, who)
        return get_source(job_dir, meta, bool(keep_bg))

    @app.get("/api/v1/jobs/{job_id}/artwork.png")
    def api_artwork(job_id: str, keep_bg: int = 0, who: Viewer = Depends(viewer)) -> FileResponse:
        job_dir, meta = load_job(job_id, who)
        get_source(job_dir, meta, bool(keep_bg))
        return FileResponse(artwork_path(job_dir, bool(keep_bg)), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/v1/jobs/{job_id}/preview.png")
    def api_preview(job_id: str, who: Viewer = Depends(viewer)) -> FileResponse:
        job_dir, _ = load_job(job_id, who)
        p = job_dir / "preview_cutcontour.png"
        if not p.exists():
            raise HTTPException(404, "Превью ещё не построено")
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/jobs/{job_id}/quote")
    def api_quote(job_id: str, body: ParamsIn, who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        job_dir, meta = load_job(job_id, who)
        params = JobParams.from_dict(body.model_dump())
        source = get_source(job_dir, meta, params.keep_background)
        return _quote(source, params)

    @app.post("/api/v1/jobs/{job_id}/check")
    def api_check(job_id: str, body: ParamsIn, who: Viewer = Depends(viewer)) -> Response:
        """Настоящая конвертация во временную папку: отдаёт PNG превью контура, в чат ничего не шлёт."""
        job_dir, meta = load_job(job_id, who)
        check_rate(who.user_id, who.is_admin)
        params = JobParams.from_dict(body.model_dump())
        convert = converter or _default_converter()
        tmp = Path(tempfile.mkdtemp(prefix="miniapp_check_", dir=str(job_dir)))
        try:
            preview = tmp / "preview.png"
            convert(
                input_path=job_dir / str(meta.get("input_filename", "input.jpg")),
                output_eps=tmp / "check.eps",
                size_mm=params.size_mm,
                offset_mm=params.offset_mm,
                bg_tolerance=12.0,
                keep_background=params.keep_background,
                preview_png_path=preview,
                perf_cut_mode=params.perf_cut_mode,
                nesting_qty=params.nesting_qty,
                roll_width_mm=params.roll_width_mm,
                stretch_x=params.stretch_x,
                stretch_y=params.stretch_y,
                fit_a4_sheet=params.fit_a4_sheet,
                render_a4_guide_on_preview=False,
                perf_cut_passes=params.perf_cut_passes,
            )
            return Response(preview.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Ошибка точной проверки макета %s", job_id)
            raise HTTPException(500, "Не удалось построить превью") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @app.post("/api/v1/jobs/{job_id}/render")
    def api_render(job_id: str, body: ParamsIn, who: Viewer = Depends(viewer)) -> Dict[str, Any]:
        """Сохраняет параметры, делает EPS и отправляет превью и файл в чат (как кнопки бота)."""
        job_dir, meta = load_job(job_id, who)
        params = JobParams.from_dict(body.model_dump())
        lock = job_lock(job_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Этот макет уже конвертируется, подождите")
        try:
            check_rate(who.user_id, who.is_admin)
            values = params.to_dict()
            storage_update_job(
                job_id,
                **{k: (int(v) if isinstance(v, bool) else v) for k, v in values.items()},
                menu_tab="main",
            )
            _sync_meta_file(job_dir, values)
            owner_id = int(meta.get("user_id", who.user_id))
            if owner_id == who.user_id:
                storage_update_user_prefs(who.user_id, **values)
            send = deliver or _default_deliver()
            # storage_update_job выше уже сдвинул updated_at, успех конвертации видно по более позднему
            before = time.time()
            send(
                chat_id=who.user_id,
                user_id=owner_id,
                job_dir=job_dir,
                status_msg_id=None,
                was_compressed_photo=False,
                action_title="Мини-приложение",
            )
        finally:
            lock.release()
        fresh = storage_get_job(job_id) or {}
        if float(fresh.get("updated_at") or 0.0) < before:
            # process_and_send_job сам сообщил об ошибке в чат, здесь только говорим редактору
            raise HTTPException(500, "Не удалось сделать EPS, подробности в чате с ботом")
        return {
            "ok": True,
            "width_mm": fresh.get("width_mm"),
            "height_mm": fresh.get("height_mm"),
            "num_contours": fresh.get("num_contours"),
            "pricing": fresh.get("pricing"),
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _sync_meta_file(job_dir: Path, values: Dict[str, Any]) -> None:
    """meta.json дублирует storage (бот читает оба), обновляем его так же, как кнопки бота."""
    from bot.jobs import load_json_file, save_json_file

    meta_file = job_dir / "meta.json"
    data = load_json_file(meta_file, {})
    data.update(values)
    data["menu_tab"] = "main"
    save_json_file(meta_file, data)


def _default_converter() -> Callable[..., Any]:
    from jpeg2eps import convert_image_to_plotter_eps

    return convert_image_to_plotter_eps


def _default_deliver() -> Callable[..., None]:
    # Импорт telegram_bot создаёт объект бота и регистрирует хендлеры, но опрос Telegram не запускает:
    # он стартует только в telegram_bot.main(). Отправка идёт через тот же Bot API.
    import telegram_bot

    return telegram_bot.process_and_send_job
