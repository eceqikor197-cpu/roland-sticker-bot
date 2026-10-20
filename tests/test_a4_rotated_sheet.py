"""Лист А4 с поворотом: EPS не шире альбомного А4 и все места заполнены."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from bot.jobs import inspect_eps_stats
from jpeg2eps import convert_image_to_plotter_eps


def _wide_sticker(path: Path) -> None:
    img = np.full((400, 900, 3), 255, np.uint8)
    cv2.ellipse(img, (450, 200), (400, 160), 0, 0, 360, (200, 30, 60), -1)
    Image.fromarray(img).save(path)


def test_rotated_a4_sheet_stays_a4(tmp_path: Path) -> None:
    src = tmp_path / "wide.png"
    _wide_sticker(src)
    # Ячейка ~121×55 мм: на портретном листе входит 4, повёрнутыми 6 (pricing выбирает поворот)
    out = convert_image_to_plotter_eps(src, tmp_path / "a4.eps", size_mm=110, offset_mm=2.5, fit_a4_sheet=True)
    cut_contours, perf_contours, w_mm, h_mm = inspect_eps_stats(out)
    assert w_mm <= 297.5 and h_mm <= 210.5
    assert cut_contours == 6
    assert perf_contours == 1
