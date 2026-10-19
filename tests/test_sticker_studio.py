"""Проверки API Sticker Studio на временной базе комбайна."""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ASSET_COLUMNS = [
    "asset_id", "topic_id", "category_lvl1", "category_lvl2", "folder_slug", "topic_name", "asset_type",
    "title", "source_name", "source_url", "phash_hex", "width_px", "height_px", "width_mm", "height_mm",
    "rel_folder_path", "eps_filename", "png_filename", "local_eps_path", "local_png_path",
    "local_preview_path", "gdrive_folder_path", "created_at",
]


def make_harvester_db(path: Path, png_dir: Path) -> None:
    from PIL import Image, ImageDraw

    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE assets ({', '.join(c + ' TEXT' for c in ASSET_COLUMNS)})")
    rows = [
        ("a1", "02_Авто_Наклейки", "JDM", "Turbo"),
        ("a2", "02_Авто_Наклейки", "JDM", "Drift King"),
        ("a3", "03_Одиночные_Стикеры", "Аниме", "Котик в очках"),
    ]
    for i, (aid, c1, c2, title) in enumerate(rows):
        png = png_dir / f"{aid}.png"
        img = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle((4, 4, 196, 116), 20, fill=(236, 0, 140, 255))
        img.save(png)
        values = dict.fromkeys(ASSET_COLUMNS, "")
        values.update(
            asset_id=aid, topic_id=1, category_lvl1=c1, category_lvl2=c2, topic_name=c2, asset_type="single",
            title=title, width_px=200, height_px=120, width_mm=100, height_mm=60,
            local_png_path=str(png), created_at=i,
        )
        conn.execute(
            f"INSERT INTO assets VALUES ({', '.join('?' for _ in ASSET_COLUMNS)})",
            [values[c] for c in ASSET_COLUMNS],
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    make_harvester_db(tmp_path / "harvester.db", tmp_path)
    monkeypatch.setenv("STUDIO_HARVESTER_DB", str(tmp_path / "harvester.db"))
    monkeypatch.setenv("STUDIO_DB", str(tmp_path / "studio.db"))
    import sticker_studio.server as server

    importlib.reload(server)
    with TestClient(server.app) as c:
        yield c


def test_categories_and_search(client):
    cats = client.get("/api/categories").json()
    assert {c["name"] for c in cats} == {"02_Авто_Наклейки", "03_Одиночные_Стикеры"}

    res = client.get("/api/stickers", params={"c2": "JDM"}).json()
    assert [s["id"] for s in res["items"]] == ["a2", "a1"]
    assert res["has_more"] is False

    res = client.get("/api/stickers", params={"q": "котик"}).json()
    assert [s["title"] for s in res["items"]] == ["Котик в очках"]

    res = client.get("/api/stickers", params={"limit": 2}).json()
    assert len(res["items"]) == 2 and res["has_more"] is True


def test_sticker_image(client):
    r = client.get("/api/stickers/a1/image")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get("/api/stickers/nope/image").status_code == 404


def test_layout_save_update_load(client):
    body = {"surface_id": "macbook_air_13", "title": "test", "data": {"version": 1, "layouts": {}}}
    lid = client.post("/api/layouts", json=body).json()["layout_id"]
    body["data"]["layouts"] = {"macbook_air_13:lid": [{"sid": "a1"}]}
    assert client.post(f"/api/layouts?layout_id={lid}", json=body).json()["layout_id"] == lid
    saved = client.get(f"/api/layouts/{lid}").json()
    assert saved["data"]["layouts"]["macbook_air_13:lid"][0]["sid"] == "a1"
    assert client.get("/api/layouts/missing").status_code == 404


def test_static_editor(client):
    assert "Sticker Studio" in client.get("/").text
    assert client.get("/surfaces.json").json()["devices"][0]["id"] == "macbook_air_13"
