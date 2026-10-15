"""
Тесты каталога объектов оклейки (MacBook) и эндпоинтов /api/v1/objects.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sticker_app.main import app
from sticker_app.objects import get_object, list_objects


@pytest.fixture
def client():
    return TestClient(app)


def test_list_objects():
    items = list_objects()
    assert len(items) >= 4
    ids = [item.id for item in items]
    assert "macbook_air_13" in ids
    assert "macbook_air_15" in ids
    assert "macbook_pro_14" in ids
    assert "macbook_pro_16" in ids

    for item in items:
        assert item.width_mm > 100
        assert item.height_mm > 100
        assert item.corner_radius_mm > 0
        assert item.mask_svg_path_mm.startswith("M")
        assert item.safe_margin_mm >= 0
        assert item.verified is False


def test_get_object_success():
    obj = get_object("macbook_air_13")
    assert obj is not None
    assert obj.id == "macbook_air_13"
    assert obj.width_mm == 304.1
    assert obj.height_mm == 214.9
    assert obj.corner_radius_mm == 8.0
    assert obj.safe_margin_mm == 4.0


def test_get_object_not_found():
    assert get_object("nonexistent_device") is None


def test_get_object_path_traversal():
    assert get_object("../../../etc/passwd") is None
    assert get_object("..\\..\\windows") is None


def test_api_list_objects(client):
    response = client.get("/api/v1/objects")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total"] >= 4
    item_ids = [item["id"] for item in data["items"]]
    assert "macbook_air_13" in item_ids


def test_api_get_object_success(client):
    response = client.get("/api/v1/objects/macbook_air_13")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "macbook_air_13"
    assert data["width_mm"] == 304.1
    assert data["height_mm"] == 214.9
    assert data["verified"] is False


def test_api_get_object_404(client):
    response = client.get("/api/v1/objects/nonexistent")
    assert response.status_code == 404
