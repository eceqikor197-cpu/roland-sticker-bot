"""
Тесты валидации схемы дизайна design.schema.json и эндпоинта /api/v1/design/validate.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sticker_app.design import (
    DesignValidationError,
    check_design_boundaries,
    validate_design_data,
)
from sticker_app.main import app

SCHEMA_FILE = Path(__file__).resolve().parent.parent.parent / "design.schema.json"


@pytest.fixture
def client():
    return TestClient(app)


def test_schema_file_exists():
    assert SCHEMA_FILE.is_file()
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["title"] == "RolandStickerbombDesign"
    assert "schema_version" in schema["required"]
    assert "object_id" in schema["required"]
    assert "placements" in schema["required"]


def test_validate_valid_design():
    data = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "created_at": 1729000000.0,
        "placements": [
            {
                "asset_id": "demo_01_01_bfecf7",
                "x_mm": 150.0,
                "y_mm": 100.0,
                "width_mm": 50.0,
                "rotation_deg": 15.0,
                "z": 0,
                "locked": False,
            }
        ],
    }
    design = validate_design_data(data)
    assert design.schema_version == "1.0"
    assert design.object_id == "macbook_air_13"
    assert len(design.placements) == 1
    assert design.placements[0].width_mm == 50.0

    errors, warnings = check_design_boundaries(design)
    assert len(errors) == 0
    assert len(warnings) == 0


def test_invalid_schema_version():
    data = {
        "schema_version": "2.0",
        "object_id": "macbook_air_13",
        "placements": [],
    }
    with pytest.raises(DesignValidationError, match="Неподдерживаемая версия схемы"):
        validate_design_data(data)


def test_max_placements_limit():
    placements = [
        {
            "asset_id": f"sticker_{i}",
            "x_mm": 100.0,
            "y_mm": 100.0,
            "width_mm": 30.0,
            "rotation_deg": 0.0,
            "z": i,
            "locked": False,
        }
        for i in range(151)
    ]
    data = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": placements,
    }
    with pytest.raises(DesignValidationError):
        validate_design_data(data)


def test_sticker_width_limits():
    # Слишком маленький (< 15 мм)
    data_too_small = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "s1",
                "x_mm": 100.0,
                "y_mm": 100.0,
                "width_mm": 14.9,
            }
        ],
    }
    with pytest.raises(DesignValidationError):
        validate_design_data(data_too_small)

    # Слишком большой (> 150 мм)
    data_too_big = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "s1",
                "x_mm": 100.0,
                "y_mm": 100.0,
                "width_mm": 150.1,
            }
        ],
    }
    with pytest.raises(DesignValidationError):
        validate_design_data(data_too_big)


def test_boundary_check_out_of_bounds():
    data = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "s_out",
                "x_mm": 5.0,
                "y_mm": 100.0,
                "width_mm": 40.0,  # min_x = -15.0 < 0
                "rotation_deg": 0.0,
            }
        ],
    }
    design = validate_design_data(data)
    errors, _ = check_design_boundaries(design)
    assert len(errors) == 1
    assert "выходит за пределы" in errors[0]


def test_boundary_check_safe_margin_warning():
    # Внутри объекта (min_x = 22.0 - 20.0 = 2.0 > 0), но нарушает safe_margin = 4.0
    data = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "s_near",
                "x_mm": 22.0,
                "y_mm": 100.0,
                "width_mm": 40.0,
                "rotation_deg": 0.0,
            }
        ],
    }
    design = validate_design_data(data)
    errors, warnings = check_design_boundaries(design)
    assert len(errors) == 0
    assert len(warnings) == 1
    assert "нарушает безопасную зону" in warnings[0]


def test_api_validate_design_valid(client):
    body = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "demo_01_01_bfecf7",
                "x_mm": 150.0,
                "y_mm": 100.0,
                "width_mm": 50.0,
                "rotation_deg": 0.0,
                "z": 1,
                "locked": False,
            }
        ],
    }
    response = client.post("/api/v1/design/validate", json=body)
    assert response.status_code == 200
    res = response.json()
    assert res["valid"] is True
    assert res["placements_count"] == 1
    assert len(res["errors"]) == 0


def test_api_validate_design_out_of_bounds(client):
    body = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "out_of_bounds_sticker",
                "x_mm": 0.0,
                "y_mm": 0.0,
                "width_mm": 50.0,
                "rotation_deg": 0.0,
            }
        ],
    }
    response = client.post("/api/v1/design/validate", json=body)
    assert response.status_code == 200
    res = response.json()
    assert res["valid"] is False
    assert len(res["errors"]) > 0


def test_api_validate_design_invalid_schema(client):
    body = {
        "schema_version": "1.0",
        "object_id": "macbook_air_13",
        "placements": [
            {
                "asset_id": "bad_width",
                "x_mm": 100.0,
                "y_mm": 100.0,
                "width_mm": 5.0,  # ge=15.0 violation
            }
        ],
    }
    response = client.post("/api/v1/design/validate", json=body)
    assert response.status_code == 422
