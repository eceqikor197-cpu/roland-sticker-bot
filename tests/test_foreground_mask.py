"""Выделение стикера на фоне: мягкая тень не должна попадать в контур реза."""

from __future__ import annotations

import cv2
import numpy as np

from jpeg2eps import extract_foreground_mask


def _sticker_with_drop_shadow() -> np.ndarray:
    size = 600
    img = np.full((size, size, 3), 232.0, dtype=np.float32)
    shadow = np.zeros((size, size), dtype=np.float32)
    cv2.circle(shadow, (325, 330), 250, 1.0, -1)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 20)
    img -= shadow[:, :, None] * 70.0
    img = np.clip(img, 0, 255).astype(np.uint8)
    cv2.circle(img, (295, 295), 250, (60, 20, 90), -1)
    cv2.circle(img, (295, 295), 235, (230, 80, 230), 7)
    return img


def test_drop_shadow_is_not_part_of_sticker() -> None:
    mask = extract_foreground_mask(_sticker_with_drop_shadow(), None)
    ys, xs = np.nonzero(mask)
    # Круг 295±250 → рамка 45..545; тень уходила бы вправо-вниз до ~600
    assert xs.max() <= 550
    assert ys.max() <= 550
    assert xs.min() >= 40
    assert ys.min() >= 40
