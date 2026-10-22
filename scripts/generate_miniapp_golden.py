#!/usr/bin/env python3
"""Генерирует tests/golden/miniapp_layout.json: эталон размеров и раскладки для miniapp/static/geometry.js.

Значения считает Python (miniapp/geometry.py + pricing.py), JS-тест сверяется с ними.
Запуск: python scripts/generate_miniapp_golden.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from miniapp.geometry import JobParams, item_geometry  # noqa: E402
from pricing import calculate_nesting_and_price  # noqa: E402

OUT = ROOT / "tests" / "golden" / "miniapp_layout.json"

CASES = [
    # (crop_w, crop_h, params)
    (803, 323, {"size_mm": 110}),
    (803, 323, {"size_mm": 140}),
    (477, 475, {"size_mm": 70, "offset_mm": 2.5}),
    (477, 475, {"size_mm": 70, "offset_mm": 5, "perf_cut_mode": "die_cut"}),
    (1200, 800, {"size_mm": 50, "offset_mm": 0}),
    (1200, 800, {"size_mm": 50, "offset_mm": 1.5, "perf_cut_mode": "sheet_frame"}),
    (300, 900, {"size_mm": 297, "offset_mm": 4}),
    (300, 900, {"size_mm": 30, "stretch_x": 1.5, "stretch_y": 1.0}),
    (2000, 1500, {"size_mm": 100, "stretch_x": 1.0, "stretch_y": 3.0}),
    (640, 640, {"size_mm": 1370, "offset_mm": 2.5}),
    (640, 480, {"size_mm": 5, "offset_mm": 0.5}),
    (900, 700, {"size_mm": 85, "stretch_x": 0.75, "stretch_y": 1.25, "offset_mm": 3}),
    (512, 96, {"size_mm": 5000, "offset_mm": 2.5}),
    (4000, 3000, {"size_mm": 1, "offset_mm": 2.5}),
    (4000, 3000, {"size_mm": 10, "stretch_x": 2.0, "stretch_y": 1.0, "perf_cut_mode": "die_cut"}),
]

ROLL_CASES = [(10, 1370.0), (25, 1370.0), (100, 600.0), (3, 1000.0)]


def roll_layout(cell_w: float, cell_h: float, qty: int, roll_w: float, gap: float = 3.0) -> dict:
    """Как режим nesting_qty > 1 в jpeg2eps.py."""
    usable = max(cell_w, roll_w - 10.0)
    cols = min(qty, max(1, int((usable + gap) // (cell_w + gap))))
    rows = int(math.ceil(qty / float(cols)))
    return {"cols": cols, "rows": rows, "length": round(rows * cell_h + max(0, rows - 1) * gap, 3)}


def main() -> None:
    cases = []
    for cw, ch, p in CASES:
        params = JobParams.from_dict(p)
        geo = item_geometry(cw, ch, params)
        price = calculate_nesting_and_price(geo["cell_w_mm"], geo["cell_h_mm"])
        rolls = [
            {"qty": q, "roll_width_mm": rw, **roll_layout(geo["cell_w_mm"], geo["cell_h_mm"], q, rw)}
            for q, rw in ROLL_CASES
        ]
        cases.append(
            {
                "crop_w_px": cw,
                "crop_h_px": ch,
                "params": params.to_dict(),
                "geometry": geo,
                "a4": {
                    "fit": price["a4_fit_qty"],
                    "cols": price["a4_cols"],
                    "rows": price["a4_rows"],
                    "rotated": price["a4_rotated"],
                },
                "roll": rolls,
            }
        )
    OUT.write_text(json.dumps({"version": 1, "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Записано {len(cases)} случаев в {OUT}")


if __name__ == "__main__":
    main()
