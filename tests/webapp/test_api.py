"""API мини-приложения: вход через initData, доступ к своим макетам, превью, цена, EPS."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from miniapp.auth import sign_init_data
from miniapp.server import create_app
from storage import get_job, get_user_prefs, toggle_ban_user

TOKEN = "123456:test-token"
OWNER = 1001
STRANGER = 2002
ADMIN = 9009


def headers(user_id: int) -> dict:
    init = sign_init_data(
        {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "U"})},
        TOKEN,
    )
    return {"X-Telegram-Init-Data": init}


@pytest.fixture
def delivered() -> list:
    return []


@pytest.fixture
def client(job_factory, delivered) -> TestClient:
    from storage import update_job

    job_factory("job1", OWNER)

    def fake_deliver(**kwargs):
        # Как process_and_send_job: сохраняет результат и отправляет в чат
        delivered.append(kwargs)
        update_job(kwargs["job_dir"].name, width_mm=123.4, height_mm=56.7, num_contours=1)

    def fake_converter(**kwargs):
        from PIL import Image

        Image.new("RGB", (40, 30), "white").save(kwargs["preview_png_path"])
        Path(kwargs["output_eps"]).write_text("%!PS-Adobe-3.0 EPSF-3.0\n")

    app = create_app(
        bot_token=TOKEN,
        jobs_dir=job_factory.jobs_dir,
        admin_ids={ADMIN},
        deliver=fake_deliver,
        converter=fake_converter,
        max_per_minute=3,
    )
    return TestClient(app)


def test_requires_telegram_init_data(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/job1").status_code == 401
    bad = {"X-Telegram-Init-Data": "auth_date=1&user=%7B%22id%22%3A1001%7D&hash=deadbeef"}
    assert client.get("/api/v1/jobs/job1", headers=bad).status_code == 401


def test_owner_gets_job_with_source_and_quote(client: TestClient) -> None:
    r = client.get("/api/v1/jobs/job1", headers=headers(OWNER))
    assert r.status_code == 200
    data = r.json()
    assert data["params"]["size_mm"] > 0
    assert data["source"]["crop_w_px"] > data["source"]["crop_h_px"]
    assert data["source"]["polygons"]
    assert data["quote"]["pricing"]["a4_fit_qty"] >= 1
    art = client.get("/api/v1/jobs/job1/artwork.png", headers=headers(OWNER))
    assert art.status_code == 200 and art.headers["content-type"] == "image/png"


def test_stranger_cannot_open_job_admin_can(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/job1", headers=headers(STRANGER)).status_code == 403
    assert client.get("/api/v1/jobs/job1", headers=headers(ADMIN)).status_code == 200


def test_banned_user_is_blocked(client: TestClient) -> None:
    toggle_ban_user(OWNER)
    assert client.get("/api/v1/jobs/job1", headers=headers(OWNER)).status_code == 403


def test_unknown_or_bad_job_id(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/nope", headers=headers(OWNER)).status_code == 404
    assert client.get("/api/v1/jobs/..%2Fjob1", headers=headers(OWNER)).status_code == 404


def test_list_shows_only_own_jobs(client: TestClient, job_factory) -> None:
    job_factory("job2", STRANGER)
    ids = [j["job_id"] for j in client.get("/api/v1/jobs", headers=headers(OWNER)).json()["jobs"]]
    assert ids == ["job1"]


def test_quote_follows_params(client: TestClient) -> None:
    small = client.post("/api/v1/jobs/job1/quote", json={"size_mm": 40}, headers=headers(OWNER)).json()
    big = client.post("/api/v1/jobs/job1/quote", json={"size_mm": 150}, headers=headers(OWNER)).json()
    assert small["geometry"]["art_w_mm"] == pytest.approx(40, abs=0.01)
    assert small["pricing"]["a4_fit_qty"] > big["pricing"]["a4_fit_qty"]
    bad = client.post("/api/v1/jobs/job1/quote", json={"size_mm": 0}, headers=headers(OWNER))
    assert bad.status_code == 422


def test_render_saves_params_and_delivers(client: TestClient, delivered: list) -> None:
    body = {"size_mm": 85, "offset_mm": 4, "perf_cut_mode": "die_cut", "fit_a4_sheet": True}
    r = client.post("/api/v1/jobs/job1/render", json=body, headers=headers(OWNER))
    assert r.status_code == 200, r.text
    assert r.json()["width_mm"] == 123.4
    assert delivered and delivered[0]["chat_id"] == OWNER
    job = get_job("job1")
    assert job["size_mm"] == 85 and job["offset_mm"] == 4 and job["perf_cut_mode"] == "die_cut"
    assert bool(job["fit_a4_sheet"]) is True
    meta = json.loads((Path(delivered[0]["job_dir"]) / "meta.json").read_text(encoding="utf-8"))
    assert meta["size_mm"] == 85
    assert get_user_prefs(OWNER)["offset_mm"] == 4


def test_render_reports_failed_conversion(job_factory) -> None:
    job_factory("job3", OWNER)
    app = create_app(bot_token=TOKEN, jobs_dir=job_factory.jobs_dir, admin_ids=set(), deliver=lambda **kw: None)
    r = TestClient(app).post("/api/v1/jobs/job3/render", json={}, headers=headers(OWNER))
    assert r.status_code == 500


def test_check_returns_png_and_rate_limit(client: TestClient, job_factory) -> None:
    for _ in range(3):
        r = client.post("/api/v1/jobs/job1/check", json={"size_mm": 60}, headers=headers(OWNER))
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.post("/api/v1/jobs/job1/check", json={}, headers=headers(OWNER)).status_code == 429
    # Временная папка проверки удаляется
    assert not list((job_factory.jobs_dir / "job1").glob("miniapp_check_*"))


def test_index_and_static_served(client: TestClient) -> None:
    assert "Редактор наклейки" in client.get("/").text
    assert client.get("/static/geometry.js").status_code == 200
