"""Геометрия мини-апки совпадает с настоящим конвертером и с эталоном для JS."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.jobs import inspect_eps_stats
from jpeg2eps import convert_image_to_plotter_eps
from miniapp.geometry import MAX_SIZE_MM, MAX_STRETCH, JobParams, item_geometry
from miniapp.source import prepare_source
from pricing import calculate_nesting_and_price

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "miniapp_layout.json"

PARAM_CASES = [
    {"size_mm": 110, "offset_mm": 2.5},
    {"size_mm": 60, "offset_mm": 0.0},
    {"size_mm": 80, "offset_mm": 4.0, "perf_cut_mode": "die_cut"},
    {"size_mm": 80, "offset_mm": 1.5, "perf_cut_mode": "sheet_frame"},
    {"size_mm": 90, "offset_mm": 2.5, "stretch_x": 1.4, "stretch_y": 1.0},
    {"size_mm": 70, "offset_mm": 3.0, "stretch_x": 0.8, "stretch_y": 1.2},
    {"size_mm": 2, "offset_mm": 2.5},
    {"size_mm": 3000, "offset_mm": 2.5, "stretch_x": 1.0, "stretch_y": 2.0},
]


@pytest.mark.parametrize("p", PARAM_CASES)
def test_cell_size_matches_converter(tmp_path: Path, sticker_png: Path, p: dict) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    shutil.copy(sticker_png, job_dir / "input.png")
    src = prepare_source(job_dir, "input.png", keep_background=False)
    params = JobParams.from_dict(p)
    geo = item_geometry(src["crop_w_px"], src["crop_h_px"], params)

    eps = convert_image_to_plotter_eps(
        sticker_png,
        tmp_path / "out.eps",
        size_mm=params.size_mm,
        offset_mm=params.offset_mm,
        perf_cut_mode=params.perf_cut_mode,
        stretch_x=params.stretch_x,
        stretch_y=params.stretch_y,
    )
    _, _, w_mm, h_mm = inspect_eps_stats(eps)
    # inspect_eps_stats округляет до 0.1 мм
    assert abs(w_mm - geo["cell_w_mm"]) <= 0.11
    assert abs(h_mm - geo["cell_h_mm"]) <= 0.11


def test_source_is_cached_and_normalized(tmp_path: Path, sticker_png: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    shutil.copy(sticker_png, job_dir / "input.png")
    first = prepare_source(job_dir, "input.png", keep_background=False)
    assert (job_dir / "miniapp" / "art_cut.png").exists()
    assert first["polygons"], "у эллипса должен быть контур"
    xs = [x for poly in first["polygons"] for x, _ in poly]
    ys = [y for poly in first["polygons"] for _, y in poly]
    assert 0.0 <= min(xs) < 0.05 and 0.95 < max(xs) <= 1.0
    assert 0.0 <= min(ys) < 0.05 and 0.95 < max(ys) <= 1.0
    # Повторный вызов берёт кеш и не трогает исходник
    (job_dir / "input.png").unlink()
    assert prepare_source(job_dir, "input.png", keep_background=False) == first


def test_params_are_clamped() -> None:
    p = JobParams.from_dict(
        {"size_mm": 10_000_000, "offset_mm": -3, "stretch_x": 500, "perf_cut_mode": "laser", "nesting_qty": 0}
    )
    assert p.size_mm == MAX_SIZE_MM
    assert p.offset_mm == 0.0
    assert p.stretch_x == MAX_STRETCH
    assert p.perf_cut_mode == "none"
    assert p.nesting_qty == 1


def test_large_sizes_are_allowed() -> None:
    """Баннер в несколько метров и сильное растяжение больше не обрезаются до 1,37 м и 10×."""
    p = JobParams.from_dict({"size_mm": 5000, "stretch_x": 20})
    assert p.size_mm == 5000.0
    assert p.stretch_x == 20.0


def test_garbage_params_fall_back_to_defaults() -> None:
    p = JobParams.from_dict({"size_mm": "nan", "stretch_x": "inf", "stretch_y": "abc"})
    assert p.size_mm == 100.0
    assert p.stretch_x == 1.0
    assert p.stretch_y == 1.0


def test_golden_is_up_to_date() -> None:
    """Эталон для JS посчитан текущим Python-кодом (иначе: python scripts/generate_miniapp_golden.py)."""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in data["cases"]:
        params = JobParams.from_dict(case["params"])
        geo = item_geometry(case["crop_w_px"], case["crop_h_px"], params)
        assert geo == case["geometry"]
        price = calculate_nesting_and_price(geo["cell_w_mm"], geo["cell_h_mm"])
        assert case["a4"] == {
            "fit": price["a4_fit_qty"],
            "cols": price["a4_cols"],
            "rows": price["a4_rows"],
            "rotated": price["a4_rotated"],
        }


@pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")
def test_js_geometry_matches_python() -> None:
    res = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "webapp" / "geometry.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
