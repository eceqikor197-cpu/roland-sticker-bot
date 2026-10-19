#!/usr/bin/env python3
"""
Sticker Studio — бэкенд веб-редактора раскладки стикеров.

Читает каталог стикеров из базы комбайна (harvester.db, таблица assets) только на чтение,
отдаёт PNG стикеров на холст и хранит сохранённые раскладки в отдельной базе studio.db.

Запуск:
    pip install -r sticker_studio/requirements.txt
    python -m sticker_studio.server            # http://0.0.0.0:8095

Переменные окружения:
    STUDIO_HARVESTER_DB  путь к harvester.db (по умолчанию bot_workspace/harvester/harvester.db)
    STUDIO_DB            путь к studio.db (по умолчанию bot_workspace/studio/studio.db)
    STUDIO_HOST, STUDIO_PORT
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
HARVESTER_DB = Path(os.getenv("STUDIO_HARVESTER_DB", BASE_DIR / "bot_workspace" / "harvester" / "harvester.db"))
STUDIO_DB = Path(os.getenv("STUDIO_DB", BASE_DIR / "bot_workspace" / "studio" / "studio.db"))

MAX_LAYOUT_BYTES = 512 * 1024


# ==========================================
# БАЗЫ ДАННЫХ
# ==========================================

def harvester_conn() -> sqlite3.Connection:
    if not HARVESTER_DB.exists():
        raise HTTPException(503, f"База комбайна не найдена: {HARVESTER_DB}")
    # Открываем только на чтение, чтобы не мешать воркеру комбайна
    conn = sqlite3.connect(f"file:{HARVESTER_DB}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Встроенный LIKE в SQLite не понимает регистр кириллицы — ищем через Python-lower
    conn.create_function("ulower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
    return conn


def studio_conn() -> sqlite3.Connection:
    STUDIO_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STUDIO_DB), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_studio_db() -> None:
    conn = studio_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS layouts (
                layout_id TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                data_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def sticker_image_path(row: sqlite3.Row) -> Optional[Path]:
    """PNG с прозрачным фоном для холста; превью — запасной вариант."""
    for key in ("local_png_path", "local_preview_path"):
        raw = row[key] if key in row.keys() else ""
        if raw:
            p = Path(raw)
            if p.is_file():
                return p
    return None


def sticker_to_json(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["asset_id"],
        "title": row["title"],
        "category_lvl1": row["category_lvl1"],
        "category_lvl2": row["category_lvl2"],
        "topic": row["topic_name"],
        "type": row["asset_type"],
        "width_mm": float(row["width_mm"] or 100.0),
        "height_mm": float(row["height_mm"] or 100.0),
        "width_px": int(row["width_px"] or 0),
        "height_px": int(row["height_px"] or 0),
        "image_url": f"/api/stickers/{row['asset_id']}/image",
    }


# ==========================================
# API
# ==========================================

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_studio_db()
    yield


app = FastAPI(title="Sticker Studio", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "harvester_db": HARVESTER_DB.exists()}


@app.get("/api/categories")
def categories() -> List[Dict[str, Any]]:
    conn = harvester_conn()
    try:
        rows = conn.execute(
            """
            SELECT category_lvl1, category_lvl2, COUNT(*) AS n
            FROM assets GROUP BY category_lvl1, category_lvl2
            ORDER BY category_lvl1, category_lvl2
            """
        ).fetchall()
    finally:
        conn.close()
    tree: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        c1 = tree.setdefault(r["category_lvl1"], {"name": r["category_lvl1"], "count": 0, "children": []})
        c1["count"] += r["n"]
        c1["children"].append({"name": r["category_lvl2"], "count": r["n"]})
    return list(tree.values())


@app.get("/api/stickers")
def stickers(
    c1: Optional[str] = None,
    c2: Optional[str] = None,
    q: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=200),
) -> Dict[str, Any]:
    clauses: List[str] = []
    params: List[Any] = []
    if c1:
        clauses.append("category_lvl1 = ?")
        params.append(c1)
    if c2:
        clauses.append("category_lvl2 = ?")
        params.append(c2)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(ulower(title) LIKE ? OR ulower(topic_name) LIKE ? OR ulower(folder_slug) LIKE ? OR ulower(category_lvl2) LIKE ?)"
        )
        params.extend([like] * 4)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = harvester_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM assets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit + 1, offset],
        ).fetchall()
    finally:
        conn.close()
    has_more = len(rows) > limit
    return {"items": [sticker_to_json(r) for r in rows[:limit]], "has_more": has_more}


@app.get("/api/stickers/{asset_id}")
def sticker(asset_id: str) -> Dict[str, Any]:
    conn = harvester_conn()
    try:
        row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Стикер не найден")
    return sticker_to_json(row)


@app.get("/api/stickers/{asset_id}/image")
def sticker_image(asset_id: str) -> FileResponse:
    conn = harvester_conn()
    try:
        row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
    finally:
        conn.close()
    path = sticker_image_path(row) if row else None
    if not path:
        raise HTTPException(404, "Изображение не найдено")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


class LayoutIn(BaseModel):
    surface_id: str = Field(..., max_length=64)
    title: str = Field("", max_length=120)
    data: Dict[str, Any]


@app.post("/api/layouts")
def save_layout(layout: LayoutIn, layout_id: Optional[str] = None) -> Dict[str, Any]:
    data_json = json.dumps(layout.data, ensure_ascii=False)
    if len(data_json.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise HTTPException(413, "Раскладка слишком большая")
    now = time.time()
    conn = studio_conn()
    try:
        if layout_id and conn.execute("SELECT 1 FROM layouts WHERE layout_id = ?", (layout_id,)).fetchone():
            conn.execute(
                "UPDATE layouts SET surface_id = ?, title = ?, data_json = ?, updated_at = ? WHERE layout_id = ?",
                (layout.surface_id, layout.title, data_json, now, layout_id),
            )
        else:
            layout_id = secrets.token_urlsafe(8)
            conn.execute(
                "INSERT INTO layouts (layout_id, surface_id, title, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (layout_id, layout.surface_id, layout.title, data_json, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return {"layout_id": layout_id}


@app.get("/api/layouts/{layout_id}")
def get_layout(layout_id: str) -> Dict[str, Any]:
    conn = studio_conn()
    try:
        row = conn.execute("SELECT * FROM layouts WHERE layout_id = ?", (layout_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Раскладка не найдена")
    return {
        "layout_id": row["layout_id"],
        "surface_id": row["surface_id"],
        "title": row["title"],
        "data": json.loads(row["data_json"]),
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("STUDIO_HOST", "0.0.0.0"), port=int(os.getenv("STUDIO_PORT", "8095")))


if __name__ == "__main__":
    main()
