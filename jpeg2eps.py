#!/usr/bin/env python3
"""
jpeg2eps — Инструмент перевода JPEG/PNG стикеров и стикерпаков в формат EPS для плоттера
БЕЗ ПОТЕРИ КАЧЕСТВА исходного изображения + с построением гладких векторных контуров реза (CutContour).

Как устроен файл EPS по умолчанию (режим Print & Cut без потери качества):
1. Исходное изображение внутри каждого стикера сохраняется в 100% оригинальном разрешении и качестве
   (никакого размытия, постеризации или "съедания" мелкого текста и фотографий).
2. Внешний фон вокруг стикеров автоматически удаляется и заменяется чистой белой векторной подложкой
   по форме высечки с плавным антиалиасингом краёв.
3. Поверх макета создаётся 100% векторный слой контуров реза (кубические кривые Безье) со стандартным
   плашечным цветом (Spot Color) "CutContour" (100% Magenta, 0.25 pt) для ножа плоттера.
4. Для листов со множеством стикеров (стикерпаков) работает алгоритм Voronoi-разделения, чтобы контуры
   соседних наклеек никогда не слипались.
5. В файл встраивается полноразмерное TIFF-превью (DOS EPS Binary Header 0xC5D0D3C6) для идеального
   отображения в LibreOffice Draw, CorelDRAW, Illustrator, Affinity Designer и RIP-программах.
"""

from __future__ import annotations

import argparse
import datetime
import io
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps
from PIL.Image import DecompressionBombError
from scipy import interpolate, ndimage
import svg.path
import vtracer

MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "60000000"))
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

__all__ = ["DecompressionBombError", "MAX_IMAGE_PIXELS"]

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MM_TO_PT = 72.0 / 25.4  # 1 мм = 2.83464567 пунктов PostScript


def parse_hex_color(color_str: str) -> Optional[Tuple[float, float, float]]:
    if not color_str:
        return None
    s = color_str.strip().lower()
    if s in ("none", "transparent"):
        return None
    if s.startswith("#"):
        hex_part = s[1:]
        if len(hex_part) == 3:
            return (
                int(hex_part[0] * 2, 16) / 255.0,
                int(hex_part[1] * 2, 16) / 255.0,
                int(hex_part[2] * 2, 16) / 255.0,
            )
        elif len(hex_part) >= 6:
            return (
                int(hex_part[0:2], 16) / 255.0,
                int(hex_part[2:4], 16) / 255.0,
                int(hex_part[4:6], 16) / 255.0,
            )
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        return (
            int(m.group(1)) / 255.0,
            int(m.group(2)) / 255.0,
            int(m.group(3)) / 255.0,
        )
    return (0.0, 0.0, 0.0)


def load_original_image(image_path: Path) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """
    Загружает изображение в 100% оригинальном разрешении без какого-либо размытия или сжатия.
    Возвращает (orig_rgb, orig_alpha_or_None).
    """
    pil_img = Image.open(image_path)
    pil_img = ImageOps.exif_transpose(pil_img)

    has_alpha = pil_img.mode in ("RGBA", "LA") or (
        pil_img.mode == "P" and "transparency" in pil_img.info
    )

    if has_alpha:
        pil_rgba = pil_img.convert("RGBA")
        arr = np.array(pil_rgba, dtype=np.uint8)
        return arr[:, :, :3], arr[:, :, 3]
    else:
        pil_rgb = pil_img.convert("RGB")
        return np.array(pil_rgb, dtype=np.uint8), None


def extract_foreground_mask(
    orig_rgb: np.ndarray,
    alpha: Optional[np.ndarray],
    bg_tolerance: float = 12.0,
    keep_background: bool = False,
) -> np.ndarray:
    """
    Находит маску переднего плана (стикера или набора стикеров на листе).
    Использует сглаженную копию ТОЛЬКО для анализа фона, не изменяя пиксели самого изображения!
    """
    h, w = orig_rgb.shape[:2]
    if keep_background:
        return np.full((h, w), 255, dtype=np.uint8)

    if alpha is not None:
        border_alpha = np.concatenate(
            [alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]]
        )
        if np.median(border_alpha) < 128:
            fg_mask = (alpha > 32).astype(np.uint8) * 255
            return _clean_foreground_mask(fg_mask)

    # Лёгкая фильтрация копии ТОЛЬКО для устойчивости детектора фона к JPEG-шуму
    bgr_for_mask = cv2.cvtColor(orig_rgb, cv2.COLOR_RGB2BGR)
    bgr_for_mask = cv2.bilateralFilter(bgr_for_mask, d=5, sigmaColor=18, sigmaSpace=12)
    rgb_for_mask = cv2.cvtColor(bgr_for_mask, cv2.COLOR_BGR2RGB)

    lab = cv2.cvtColor(rgb_for_mask, cv2.COLOR_RGB2LAB).astype(np.float32)
    border_pixels = np.concatenate(
        [
            lab[0, :, :],
            lab[-1, :, :],
            lab[:, 0, :],
            lab[:, -1, :],
        ],
        axis=0,
    )
    bg_lab = np.median(border_pixels, axis=0)
    border_diffs = np.linalg.norm(border_pixels - bg_lab[None, :], axis=1)
    p99_border = float(np.percentile(border_diffs, 99))
    # Если фон по краям однородный (p99 <= 4.0), используем точный порог 7.5, чтобы сохранять даже светло-серые/белые наклейки без рамки
    effective_tol = min(bg_tolerance, max(7.5, p99_border * 2.0 + 3.0)) if p99_border <= 4.0 else bg_tolerance

    diff = np.linalg.norm(lab - bg_lab[None, None, :], axis=2)

    gray = cv2.cvtColor(rgb_for_mask, cv2.COLOR_RGB2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.hypot(gx, gy)

    bg_candidates = (diff <= effective_tol) & (grad < 32.0)

    labeled, _ = ndimage.label(bg_candidates)
    border_labels = np.unique(
        np.concatenate(
            [
                labeled[0, :],
                labeled[-1, :],
                labeled[:, 0],
                labeled[:, -1],
            ]
        )
    )
    border_labels = border_labels[border_labels > 0]
    outer_bg = np.isin(labeled, border_labels)

    fg_mask = (~outer_bg).astype(np.uint8) * 255

    if np.count_nonzero(fg_mask) < (h * w * 0.005):
        return np.full((h, w), 255, dtype=np.uint8)

    return _clean_foreground_mask(fg_mask)


def _clean_foreground_mask(fg_mask: np.ndarray) -> np.ndarray:
    """
    Удаляет мелкий мусор/вотермарки снаружи стикера и сглаживает микрозазубрины маски.
    Автоматически различает одиночный стикер и стикерпак (лист со множеством стикеров).
    """
    h, w = fg_mask.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg_mask, connectivity=8)
    if num_labels <= 1:
        return fg_mask

    areas = stats[1:, cv2.CC_STAT_AREA]
    max_area = int(np.max(areas))
    canvas_area = h * w

    significant_objects = int(np.sum(areas >= (canvas_area * 0.004)))

    if significant_objects <= 2:
        min_keep_area = max(36, int(max_area * 0.02))
    else:
        min_keep_area = max(25, int(canvas_area * 0.0003))

    cleaned = np.zeros_like(fg_mask)
    for idx in range(1, num_labels):
        if stats[idx, cv2.CC_STAT_AREA] >= min_keep_area:
            cleaned[labels == idx] = 255

    return cleaned


def decontaminate_edge_colors(
    rgb: np.ndarray, fg_mask: np.ndarray, border_px: int = 2
) -> np.ndarray:
    """
    Убирает ореол фона вдоль самой границы стикера (1-2 пикселя),
    заменяя граничные пиксели цветом ближайшего внутреннего пикселя стикера.
    Внутренние пиксели стикера остаются на 100% нетронутыми!
    """
    ksize = border_px * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    core_mask = cv2.erode(fg_mask, kernel, iterations=1)
    if np.count_nonzero(core_mask) < 30:
        return rgb
    indices = ndimage.distance_transform_edt(
        core_mask == 0, return_distances=False, return_indices=True
    )
    decontaminated = rgb.copy()
    edge_zone = (fg_mask > 0) & (core_mask == 0)
    decontaminated[edge_zone] = rgb[indices[0][edge_zone], indices[1][edge_zone]]
    return decontaminated


def fill_external_silhouette(fg_mask: np.ndarray) -> np.ndarray:
    """
    Заполняет все внутренние отверстия внутри каждого стикера,
    чтобы нож плоттера резал только внешние обтекаемые контуры без внутренних дырок.
    """
    contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(fg_mask)
    if contours:
        cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    return filled


def build_smooth_cut_contours(
    silhouette_mask: np.ndarray,
    offset_px: float,
    smoothing_sigma: float = 3.0,
) -> Tuple[np.ndarray, List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]]]:
    """
    Строит расширенную маску стикера(ов) с точным радиальным отступом (offset_px)
    с помощью Euclidean Distance Transform.
    Если на листе несколько отдельных стикеров (стикерпак), использует Voronoi-разделение,
    чтобы соседние контуры реза никогда не слипались друг с другом!
    """
    group_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    grouped_mask = cv2.morphologyEx(silhouette_mask, cv2.MORPH_CLOSE, group_kernel, iterations=1)
    grouped_mask = fill_external_silhouette(grouped_mask)

    num_stickers, sticker_labels, stats, _ = cv2.connectedComponentsWithStats(
        grouped_mask, connectivity=8
    )
    valid_labels = [
        lbl for lbl in range(1, num_stickers) if stats[lbl, cv2.CC_STAT_AREA] >= 64
    ]

    if len(valid_labels) > 3:
        median_area = float(np.median([stats[lbl, cv2.CC_STAT_AREA] for lbl in valid_labels]))
        approx_sticker_size = math.sqrt(median_area)
        offset_px = min(offset_px, max(4.0, approx_sticker_size * 0.06))
        smoothing_sigma = min(smoothing_sigma, max(1.5, offset_px * 0.25))

    if offset_px > 0.5 and len(valid_labels) <= 1:
        inv_mask = silhouette_mask == 0
        dist = ndimage.distance_transform_edt(inv_mask)
        sdf = dist - float(offset_px)
        if smoothing_sigma > 0:
            sdf = ndimage.gaussian_filter(sdf, sigma=smoothing_sigma)
        offset_mask = (sdf <= 0.0).astype(np.uint8) * 255
        offset_mask = fill_external_silhouette(offset_mask)
    elif offset_px > 0.5 and len(valid_labels) > 1:
        inv_mask = grouped_mask == 0
        dist_nearest, indices = ndimage.distance_transform_edt(
            inv_mask, return_distances=True, return_indices=True
        )
        nearest_label = sticker_labels[indices[0], indices[1]]

        k3 = np.ones((3, 3), dtype=np.uint8)
        max_lbl = cv2.dilate(nearest_label.astype(np.uint16), k3)
        min_lbl = cv2.erode(nearest_label.astype(np.uint16), k3)
        voronoi_boundary = (max_lbl != min_lbl) & inv_mask

        dist_to_voronoi = ndimage.distance_transform_edt(~voronoi_boundary)

        offset_mask = np.zeros_like(silhouette_mask)
        for lbl in valid_labels:
            lbl_mask = (sticker_labels == lbl).astype(np.uint8) * 255
            allowed_zone = (nearest_label == lbl) & (dist_to_voronoi >= 2.0)
            local_inv = lbl_mask == 0
            x, y, bw, bh, _ = stats[lbl]
            pad_b = int(math.ceil(offset_px + smoothing_sigma * 3 + 6))
            y0 = max(0, y - pad_b)
            y1 = min(silhouette_mask.shape[0], y + bh + pad_b)
            x0 = max(0, x - pad_b)
            x1 = min(silhouette_mask.shape[1], x + bw + pad_b)

            sub_inv = local_inv[y0:y1, x0:x1]
            sub_allowed = allowed_zone[y0:y1, x0:x1] | (~sub_inv)
            sub_dist = ndimage.distance_transform_edt(sub_inv)
            sub_sdf = sub_dist - float(offset_px)
            if smoothing_sigma > 0:
                sub_sdf = ndimage.gaussian_filter(sub_sdf, sigma=smoothing_sigma)
            sub_expanded = (sub_sdf <= 0.0) & sub_allowed
            sub_expanded_u8 = fill_external_silhouette(sub_expanded.astype(np.uint8) * 255)
            offset_mask[y0:y1, x0:x1] = np.maximum(
                offset_mask[y0:y1, x0:x1], sub_expanded_u8
            )
    else:
        if smoothing_sigma > 0:
            blurred = ndimage.gaussian_filter(silhouette_mask.astype(np.float32), sigma=1.5)
            offset_mask = (blurred >= 127.5).astype(np.uint8) * 255
        else:
            offset_mask = silhouette_mask.copy()
        offset_mask = fill_external_silhouette(offset_mask)

    contours, _ = cv2.findContours(offset_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    bezier_loops = []
    for cnt in contours:
        if cv2.contourArea(cnt) < 40:
            continue
        pts = cnt[:, 0, :].astype(np.float64)
        beziers = _fit_periodic_cubic_bezier(pts)
        if beziers:
            bezier_loops.append(beziers)

    return offset_mask, bezier_loops


def _fit_periodic_cubic_bezier(
    pts: np.ndarray,
) -> List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]:
    """
    Аппроксимирует замкнутый контур точек (N, 2) гладким периодическим кубическим B-сплайном
    и аналитически переводит его в цепочку кубических кривых Безье [(P0, C1, C2, P3), ...].
    """
    n = len(pts)
    if n < 6:
        return []

    diffs = np.diff(pts, axis=0, append=pts[:1])
    seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
    perimeter = float(np.sum(seg_lens))
    if perimeter < 12.0:
        return []

    num_samples = max(24, min(600, int(perimeter / 2.2)))
    cum_len = np.concatenate([[0.0], np.cumsum(seg_lens)])
    sample_locs = np.linspace(0.0, perimeter, num_samples, endpoint=False)
    x_sampled = np.interp(sample_locs, cum_len, np.append(pts[:, 0], pts[0, 0]))
    y_sampled = np.interp(sample_locs, cum_len, np.append(pts[:, 1], pts[0, 1]))

    x_closed = np.append(x_sampled, x_sampled[0])
    y_closed = np.append(y_sampled, y_sampled[0])

    try:
        s_val = len(x_closed) * 0.35
        tck, _ = interpolate.splprep([x_closed, y_closed], s=s_val, k=3, per=True)
    except Exception:
        eps = max(1.0, perimeter * 0.002)
        approx = cv2.approxPolyDP(pts.astype(np.float32), eps, True)[:, 0, :]
        segs = []
        m = len(approx)
        for i in range(m):
            p0 = (float(approx[i][0]), float(approx[i][1]))
            p3 = (float(approx[(i + 1) % m][0]), float(approx[(i + 1) % m][1]))
            c1 = (p0[0] + (p3[0] - p0[0]) / 3.0, p0[1] + (p3[1] - p0[1]) / 3.0)
            c2 = (p0[0] + 2.0 * (p3[0] - p0[0]) / 3.0, p0[1] + 2.0 * (p3[1] - p0[1]) / 3.0)
            segs.append((p0, c1, c2, p3))
        return segs

    u_knots = tck[0]
    u_unique = u_knots[3:-3]
    if len(u_unique) < 8:
        u_unique = np.linspace(0.0, 1.0, max(16, int(perimeter / 12.0) + 1))

    px, py = interpolate.splev(u_unique, tck, der=0)
    dx, dy = interpolate.splev(u_unique, tck, der=1)

    bezier_segments = []
    for i in range(len(u_unique) - 1):
        dt = float(u_unique[i + 1] - u_unique[i])
        if dt <= 1e-9:
            continue
        p0 = (float(px[i]), float(py[i]))
        p3 = (float(px[i + 1]), float(py[i + 1]))
        c1 = (float(px[i] + dx[i] * dt / 3.0), float(py[i] + dy[i] * dt / 3.0))
        c2 = (float(px[i + 1] - dx[i + 1] * dt / 3.0), float(py[i + 1] - dy[i + 1] * dt / 3.0))
        bezier_segments.append((p0, c1, c2, p3))

    return bezier_segments


def vectorize_image_with_vtracer(
    rgba_image: np.ndarray,
    filter_speckle: int = 2,
    color_precision: int = 8,
    layer_difference: int = 8,
) -> List[Tuple[Tuple[float, float, float], str]]:
    """
    Запускает многослойную цветную трассировку RGBA-изображения через vtracer (используется только при флаге --vectorize).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        in_png = Path(tmpdir) / "input.png"
        out_svg = Path(tmpdir) / "traced.svg"
        Image.fromarray(rgba_image, mode="RGBA").save(in_png, format="PNG")

        vtracer.convert_image_to_svg_py(
            str(in_png),
            str(out_svg),
            colormode="color",
            hierarchical="stacked",
            mode="spline",
            filter_speckle=filter_speckle,
            color_precision=color_precision,
            layer_difference=layer_difference,
            corner_threshold=50,
            length_threshold=3.5,
            max_iterations=12,
            splice_threshold=45,
            path_precision=2,
        )

        svg_content = out_svg.read_text(encoding="utf-8")

    return _parse_vtracer_svg_paths(svg_content)


def _parse_vtracer_svg_paths(svg_text: str) -> List[Tuple[Tuple[float, float, float], str]]:
    root = ET.fromstring(svg_text)
    layers: List[Tuple[Tuple[float, float, float], str]] = []

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag != "path":
            continue
        d_attr = elem.attrib.get("d", "").strip()
        fill_attr = elem.attrib.get("fill", "#000000")
        transform_attr = elem.attrib.get("transform", "")

        rgb = parse_hex_color(fill_attr)
        if rgb is None or not d_attr:
            continue

        tx, ty = 0.0, 0.0
        if transform_attr:
            m = re.search(
                r"translate\(\s*([+-]?\d+(?:\.\d+)?)\s*(?:[\s,]\s*([+-]?\d+(?:\.\d+)?))?\s*\)",
                transform_attr,
            )
            if m:
                tx = float(m.group(1))
                ty = float(m.group(2)) if m.group(2) is not None else 0.0

        layers.append((rgb, _apply_translate_to_d(d_attr, tx, ty)))

    return layers


def _apply_translate_to_d(d_attr: str, tx: float, ty: float) -> str:
    if abs(tx) < 1e-6 and abs(ty) < 1e-6:
        return d_attr
    return f"__TRANSLATE__({tx},{ty}):{d_attr}"


def svg_d_to_postscript_commands(
    d_with_meta: str, scale: float, height_pt: float
) -> str:
    tx, ty = 0.0, 0.0
    d_str = d_with_meta
    if d_str.startswith("__TRANSLATE__("):
        prefix, d_str = d_str.split("):", 1)
        coords = prefix[len("__TRANSLATE__(") :].split(",")
        tx, ty = float(coords[0]), float(coords[1])

    parsed_path = svg.path.parse_path(d_str)
    ps_lines: List[str] = []
    curr_end: Optional[complex] = None

    def to_ps(pt: complex) -> Tuple[float, float]:
        x = (pt.real + tx) * scale
        y = height_pt - (pt.imag + ty) * scale
        return x, y

    for seg in parsed_path:
        if isinstance(seg, svg.path.Move):
            x, y = to_ps(seg.end)
            ps_lines.append(f"{x:.3f} {y:.3f} m")
            curr_end = seg.end
        elif isinstance(seg, svg.path.Close):
            ps_lines.append("cp")
            curr_end = seg.end
        elif isinstance(seg, svg.path.Line):
            if curr_end != seg.start:
                sx, sy = to_ps(seg.start)
                ps_lines.append(f"{sx:.3f} {sy:.3f} m")
            ex, ey = to_ps(seg.end)
            ps_lines.append(f"{ex:.3f} {ey:.3f} l")
            curr_end = seg.end
        elif isinstance(seg, svg.path.CubicBezier):
            if curr_end != seg.start:
                sx, sy = to_ps(seg.start)
                ps_lines.append(f"{sx:.3f} {sy:.3f} m")
            c1x, c1y = to_ps(seg.control1)
            c2x, c2y = to_ps(seg.control2)
            ex, ey = to_ps(seg.end)
            ps_lines.append(
                f"{c1x:.3f} {c1y:.3f} {c2x:.3f} {c2y:.3f} {ex:.3f} {ey:.3f} c"
            )
            curr_end = seg.end
        elif isinstance(seg, svg.path.QuadraticBezier):
            if curr_end != seg.start:
                sx, sy = to_ps(seg.start)
                ps_lines.append(f"{sx:.3f} {sy:.3f} m")
            p0 = seg.start
            q1 = seg.control
            p3 = seg.end
            c1 = p0 + (2.0 / 3.0) * (q1 - p0)
            c2 = p3 + (2.0 / 3.0) * (q1 - p3)
            c1x, c1y = to_ps(c1)
            c2x, c2y = to_ps(c2)
            ex, ey = to_ps(p3)
            ps_lines.append(
                f"{c1x:.3f} {c1y:.3f} {c2x:.3f} {c2y:.3f} {ex:.3f} {ey:.3f} c"
            )
            curr_end = seg.end
        elif isinstance(seg, svg.path.Arc):
            if curr_end != seg.start:
                sx, sy = to_ps(seg.start)
                ps_lines.append(f"{sx:.3f} {sy:.3f} m")
            steps = 12
            for step_i in range(1, steps + 1):
                pt = seg.point(step_i / float(steps))
                px, py = to_ps(pt)
                ps_lines.append(f"{px:.3f} {py:.3f} l")
            curr_end = seg.end

    return "\n".join(ps_lines)


def bezier_loops_to_postscript(
    bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
    scale: float,
    height_pt: float,
) -> str:
    """Конвертирует контуры реза (список кубических кривых Безье) в команды PostScript."""
    lines: List[str] = []
    for loop in bezier_loops:
        if not loop:
            continue
        p0_start = loop[0][0]
        sx = p0_start[0] * scale
        sy = height_pt - p0_start[1] * scale
        lines.append(f"{sx:.3f} {sy:.3f} m")
        for _, c1, c2, p3 in loop:
            c1x, c1y = c1[0] * scale, height_pt - c1[1] * scale
            c2x, c2y = c2[0] * scale, height_pt - c2[1] * scale
            p3x, p3y = p3[0] * scale, height_pt - p3[1] * scale
            lines.append(
                f"{c1x:.3f} {c1y:.3f} {c2x:.3f} {c2y:.3f} {p3x:.3f} {p3y:.3f} c"
            )
        lines.append("cp")
    return "\n".join(lines)


def build_rounded_rect_bezier_loop(
    x0: float, y0: float, x1: float, y1: float, radius: float
) -> List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]:
    """Строит замкнутый контур скруглённого прямоугольника из кубических кривых Безье."""
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    r = max(0.5, min(radius, w * 0.45, h * 0.45))
    k = 0.5522847498 * r

    # По часовой стрелке: верхняя грань -> правый верхний угол -> правая грань -> ...
    segs = [
        # Верхняя линия
        ((x0 + r, y0), (x0 + r + (w - 2 * r) / 3.0, y0), (x1 - r - (w - 2 * r) / 3.0, y0), (x1 - r, y0)),
        # Правый верхний угол
        ((x1 - r, y0), (x1 - r + k, y0), (x1, y0 + r - k), (x1, y0 + r)),
        # Правая линия
        ((x1, y0 + r), (x1, y0 + r + (h - 2 * r) / 3.0), (x1, y1 - r - (h - 2 * r) / 3.0), (x1, y1 - r)),
        # Правый нижний угол
        ((x1, y1 - r), (x1, y1 - r + k), (x1 - r + k, y1), (x1 - r, y1)),
        # Нижняя линия
        ((x1 - r, y1), (x1 - r - (w - 2 * r) / 3.0, y1), (x0 + r + (w - 2 * r) / 3.0, y1), (x0 + r, y1)),
        # Левый нижний угол
        ((x0 + r, y1), (x0 + r - k, y1), (x0, y1 - r + k), (x0, y1 - r)),
        # Левая линия
        ((x0, y1 - r), (x0, y1 - r - (h - 2 * r) / 3.0), (x0, y0 + r + (h - 2 * r) / 3.0), (x0, y0 + r)),
        # Левый верхний угол
        ((x0, y0 + r), (x0, y0 + r - k), (x0 + r - k, y0), (x0 + r, y0)),
    ]
    return segs


def _loops_to_cv_polys(
    bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
) -> List[np.ndarray]:
    polys = []
    for loop in bezier_loops:
        pts = []
        for p0, c1, c2, p3 in loop:
            for t in np.linspace(0.0, 1.0, 12, endpoint=False):
                mt = 1.0 - t
                x = (
                    mt**3 * p0[0]
                    + 3 * mt**2 * t * c1[0]
                    + 3 * mt * t**2 * c2[0]
                    + t**3 * p3[0]
                )
                y = (
                    mt**3 * p0[1]
                    + 3 * mt**2 * t * c1[1]
                    + 3 * mt * t**2 * c2[1]
                    + t**3 * p3[1]
                )
                pts.append([int(round(x)), int(round(y))])
        if len(pts) >= 3:
            polys.append(np.array(pts, dtype=np.int32))
    return polys


def render_preview_rgb(
    width_px: int,
    height_px: int,
    vector_layers: List[Tuple[Tuple[float, float, float], str]],
    cut_bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
    include_white_backing: bool = True,
    include_cut_contour: bool = True,
    lossless_rgb_artwork: Optional[np.ndarray] = None,
    perf_cut_bezier_loops: Optional[List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]]] = None,
) -> np.ndarray:
    """
    Строит полноразмерное 100% чёткое превью для встроенного TIFF-блока EPS (без сжатия/уменьшения разрешения!),
    отображая:
    - CutContour (надсечка плёнки) — ярко-розовым (Magenta #EC008C)
    - PerfCutContour (сквозная резка с подложкой) — ярко-голубым (Cyan #00AEEF)
    """
    canvas = np.full((height_px, width_px, 3), 232, dtype=np.uint8)

    cut_polys = _loops_to_cv_polys(cut_bezier_loops)
    perf_polys = _loops_to_cv_polys(perf_cut_bezier_loops or [])

    if include_white_backing:
        if perf_polys:
            cv2.fillPoly(canvas, perf_polys, (255, 255, 255), lineType=cv2.LINE_AA)
        if cut_polys:
            cv2.fillPoly(canvas, cut_polys, (255, 255, 255), lineType=cv2.LINE_AA)

    if lossless_rgb_artwork is not None:
        if cut_polys:
            mask = np.zeros((height_px, width_px), dtype=np.uint8)
            cv2.fillPoly(mask, cut_polys, 255, lineType=cv2.LINE_AA)
            alpha_m = (mask.astype(np.float32) / 255.0)[:, :, None]
            canvas = (
                lossless_rgb_artwork.astype(np.float32) * alpha_m
                + canvas.astype(np.float32) * (1.0 - alpha_m)
            ).astype(np.uint8)
        else:
            canvas = lossless_rgb_artwork.copy()
    else:
        for rgb_val, d_with_meta in vector_layers:
            tx, ty = 0.0, 0.0
            d_str = d_with_meta
            if d_str.startswith("__TRANSLATE__("):
                prefix, d_str = d_str.split("):", 1)
                coords = prefix[len("__TRANSLATE__(") :].split(",")
                tx, ty = float(coords[0]), float(coords[1])

            parsed = svg.path.parse_path(d_str)
            subpaths = []
            curr_pts = []
            for seg in parsed:
                if isinstance(seg, svg.path.Move):
                    if len(curr_pts) >= 3:
                        subpaths.append(np.array(curr_pts, dtype=np.int32))
                    curr_pts = [
                        [int(round(seg.end.real + tx)), int(round(seg.end.imag + ty))]
                    ]
                elif isinstance(seg, svg.path.Close):
                    if len(curr_pts) >= 3:
                        subpaths.append(np.array(curr_pts, dtype=np.int32))
                    curr_pts = []
                elif isinstance(seg, svg.path.Line):
                    curr_pts.append(
                        [int(round(seg.end.real + tx)), int(round(seg.end.imag + ty))]
                    )
                else:
                    for t in (0.25, 0.5, 0.75, 1.0):
                        pt = seg.point(t)
                        curr_pts.append(
                            [int(round(pt.real + tx)), int(round(pt.imag + ty))]
                        )
            if len(curr_pts) >= 3:
                subpaths.append(np.array(curr_pts, dtype=np.int32))

            if subpaths:
                c_rgb = (
                    int(round(rgb_val[0] * 255)),
                    int(round(rgb_val[1] * 255)),
                    int(round(rgb_val[2] * 255)),
                )
                cv2.fillPoly(canvas, subpaths, c_rgb, lineType=cv2.LINE_AA)

    thickness = max(2, int(round(max(width_px, height_px) / 380.0)))
    if include_cut_contour and cut_polys:
        cv2.polylines(
            canvas,
            cut_polys,
            isClosed=True,
            color=(236, 0, 140),
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )

    if perf_polys:
        cv2.polylines(
            canvas,
            perf_polys,
            isClosed=True,
            color=(0, 174, 239),
            thickness=max(2, thickness + 1),
            lineType=cv2.LINE_AA,
        )

    return canvas


def write_eps_file(
    output_eps: Path,
    title: str,
    width_px: int,
    height_px: int,
    scale_px_to_pt: float,
    vector_layers: List[Tuple[Tuple[float, float, float], str]],
    cut_bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
    include_white_backing: bool = True,
    include_cut_contour: bool = True,
    spot_color_name: str = "CutContour",
    lossless_rgb_artwork: Optional[np.ndarray] = None,
    preview_png_path: Optional[Path] = None,
    perf_cut_bezier_loops: Optional[List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]]] = None,
    perf_spot_color_name: str = "PerfCutContour",
    perf_cut_passes: int = 2,
) -> None:
    """
    Записывает файл PostScript EPS (%!PS-Adobe-3.0 EPSF-3.0)
    со стандартным заголовком DOS EPS Binary Header (0xC5D0D3C6) и полноразмерным TIFF-превью.
    Поддерживает два спотовых контура Roland VersaWorks: CutContour и PerfCutContour.
    """
    width_pt = width_px * scale_px_to_pt
    height_pt = height_px * scale_px_to_pt
    bbox_w = int(math.ceil(width_pt))
    bbox_h = int(math.ceil(height_pt))
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_cut = bool(include_cut_contour and cut_bezier_loops)
    has_perf = bool(perf_cut_bezier_loops)

    ps_chunks: List[str] = [
        "%!PS-Adobe-3.0 EPSF-3.0",
        "%%Creator: jpeg2eps Plotter Tool (Roland Print & Cut)",
        f"%%Title: ({title})",
        f"%%CreationDate: ({now_str})",
        f"%%BoundingBox: 0 0 {bbox_w} {bbox_h}",
        f"%%HiResBoundingBox: 0.0000 0.0000 {width_pt:.4f} {height_pt:.4f}",
        "%%DocumentProcessColors: Cyan Magenta Yellow Black",
    ]
    if has_cut and has_perf:
        ps_chunks.extend(
            [
                f"%%DocumentCustomColors: ({spot_color_name}) ({perf_spot_color_name})",
                f"%%CMYKCustomColor: 0.0000 1.0000 0.0000 0.0000 ({spot_color_name})",
                f"%%+ 1.0000 0.0000 0.0000 0.0000 ({perf_spot_color_name})",
            ]
        )
    elif has_cut:
        ps_chunks.extend(
            [
                f"%%DocumentCustomColors: ({spot_color_name})",
                f"%%CMYKCustomColor: 0.0000 1.0000 0.0000 0.0000 ({spot_color_name})",
            ]
        )
    elif has_perf:
        ps_chunks.extend(
            [
                f"%%DocumentCustomColors: ({perf_spot_color_name})",
                f"%%CMYKCustomColor: 1.0000 0.0000 0.0000 0.0000 ({perf_spot_color_name})",
            ]
        )

    ps_chunks.extend(
        [
            "%%LanguageLevel: 2",
            "%%Pages: 1",
            "%%EndComments",
            "%%BeginProlog",
            "/bd { bind def } bind def",
            "/m { moveto } bd",
            "/l { lineto } bd",
            "/c { curveto } bd",
            "/cp { closepath } bd",
            "/np { newpath } bd",
            "/f { eofill } bd",
            "/s { stroke } bd",
            "/rgb { setrgbcolor } bd",
            "%%EndProlog",
            "%%Page: 1 1",
            "gsave",
        ]
    )

    cut_ps_path = ""
    if cut_bezier_loops:
        cut_ps_path = bezier_loops_to_postscript(
            cut_bezier_loops, scale_px_to_pt, height_pt
        )

    perf_ps_path = ""
    if perf_cut_bezier_loops:
        perf_ps_path = bezier_loops_to_postscript(
            perf_cut_bezier_loops, scale_px_to_pt, height_pt
        )

    # 1. Векторная белая подложка
    if include_white_backing:
        if perf_ps_path:
            ps_chunks.extend(
                [
                    "% --- White Sheet/Die Backing Layer ---",
                    "1.000 1.000 1.000 rgb",
                    "np",
                    perf_ps_path,
                    "f",
                ]
            )
        if cut_ps_path:
            ps_chunks.extend(
                [
                    "% --- White Sticker Backing Layer ---",
                    "1.000 1.000 1.000 rgb",
                    "np",
                    cut_ps_path,
                    "f",
                ]
            )

    # 2. Изображение внутри стикеров (100% оригинальное качество без потерь)
    if lossless_rgb_artwork is not None:
        ps_chunks.append("% --- Lossless Original Artwork Layer (100% Quality) ---")
        ps_chunks.append("gsave")
        if cut_ps_path:
            ps_chunks.extend(["np", cut_ps_path, "clip", "np"])
        jpg_buf = io.BytesIO()
        Image.fromarray(lossless_rgb_artwork, mode="RGB").save(
            jpg_buf, format="JPEG", quality=98, subsampling=0
        )
        hex_lines = _encode_ascii_hex(jpg_buf.getvalue())
        ps_chunks.extend(
            [
                "/DeviceRGB setcolorspace",
                f"{width_pt:.4f} {height_pt:.4f} scale",
                "<<",
                "  /ImageType 1",
                f"  /Width {width_px}",
                f"  /Height {height_px}",
                "  /BitsPerComponent 8",
                "  /Decode [0 1 0 1 0 1]",
                f"  /ImageMatrix [{width_px} 0 0 -{height_px} 0 {height_px}]",
                "  /DataSource currentfile /ASCIIHexDecode filter /DCTDecode filter",
                ">> image",
                hex_lines,
                ">",
                "grestore",
            ]
        )
    else:
        ps_chunks.append("% --- Multi-Layer Vector Artwork ---")
        for rgb_val, d_with_meta in vector_layers:
            cmd_str = svg_d_to_postscript_commands(
                d_with_meta, scale_px_to_pt, height_pt
            )
            if not cmd_str.strip():
                continue
            r, g, b = rgb_val
            ps_chunks.extend(
                [
                    f"{r:.4f} {g:.4f} {b:.4f} rgb",
                    "np",
                    cmd_str,
                    "f",
                ]
            )

    # 3. Верхний векторный слой контура надсечки CutContour (Spot Color 100% Magenta, 0.25 pt)
    if has_cut and cut_ps_path:
        ps_chunks.extend(
            [
                f"% --- Plotter CutContour Spot Color Vector Layer ({spot_color_name}) ---",
                f"[ /Separation ({spot_color_name}) /DeviceCMYK {{ dup 0 mul exch dup 1 mul exch dup 0 mul exch 0 mul }} ] setcolorspace",
                "1.0 setcolor",
                "0.25 setlinewidth",
                "1 setlinejoin",
                "1 setlinecap",
                "np",
                cut_ps_path,
                "s",
            ]
        )

    # 4. Векторный слой сквозной высечки PerfCutContour (Spot Color 100% Cyan, 0.25 pt для Roland VersaWorks)
    if has_perf and perf_ps_path:
        passes = max(1, min(5, int(perf_cut_passes)))
        ps_chunks.extend(
            [
                f"% --- Plotter PerfCutContour Spot Color Vector Layer ({perf_spot_color_name}, passes={passes}) ---",
                f"[ /Separation ({perf_spot_color_name}) /DeviceCMYK {{ dup 1 mul exch dup 0 mul exch dup 0 mul exch 0 mul }} ] setcolorspace",
                "1.0 setcolor",
                "0.25 setlinewidth",
                "1 setlinejoin",
                "1 setlinecap",
            ]
        )
        for pass_i in range(passes):
            shift_pt = pass_i * 0.004
            if shift_pt > 0:
                ps_chunks.extend(
                    [
                        f"% --- PerfCutContour Pass #{pass_i + 1} of {passes} ---",
                        "gsave",
                        f"{shift_pt:.4f} {shift_pt:.4f} translate",
                        "np",
                        perf_ps_path,
                        "s",
                        "grestore",
                    ]
                )
            else:
                ps_chunks.extend(
                    [
                        f"% --- PerfCutContour Pass #1 of {passes} ---",
                        "np",
                        perf_ps_path,
                        "s",
                    ]
                )

    ps_chunks.extend(
        [
            "grestore",
            "showpage",
            "%%Trailer",
            "%%EOF",
            "",
        ]
    )

    ps_bytes = "\r\n".join(ps_chunks).encode("utf-8")

    preview_rgb = render_preview_rgb(
        width_px=width_px,
        height_px=height_px,
        vector_layers=vector_layers,
        cut_bezier_loops=cut_bezier_loops,
        include_white_backing=include_white_backing,
        include_cut_contour=include_cut_contour,
        lossless_rgb_artwork=lossless_rgb_artwork,
        perf_cut_bezier_loops=perf_cut_bezier_loops,
    )
    pil_preview = Image.fromarray(preview_rgb, mode="RGB")
    if preview_png_path is not None:
        pil_preview.save(preview_png_path, format="PNG")

    # Если холст очень большой (например, спуск тиража на рулон), ограничиваем встраиваемый TIFF до 1800px для скорости открытия
    tiff_pil = pil_preview
    max_tiff_dim = 1800
    if max(width_px, height_px) > max_tiff_dim:
        sc = max_tiff_dim / float(max(width_px, height_px))
        tiff_pil = pil_preview.resize(
            (max(1, int(round(width_px * sc))), max(1, int(round(height_px * sc)))),
            Image.Resampling.LANCZOS,
        )

    tiff_buf = io.BytesIO()
    tiff_pil.save(
        tiff_buf, format="TIFF", compression="raw"
    )
    tiff_bytes = tiff_buf.getvalue()

    ps_offset = 30
    ps_length = len(ps_bytes)
    tiff_offset = ps_offset + ps_length
    tiff_length = len(tiff_bytes)

    dos_eps_header = struct.pack(
        "<4sIIIIIIH",
        b"\xc5\xd0\xd3\xc6",
        ps_offset,
        ps_length,
        0,
        0,
        tiff_offset,
        tiff_length,
        0xFFFF,
    )

    with open(output_eps, "wb") as f:
        f.write(dos_eps_header)
        f.write(ps_bytes)
        f.write(tiff_bytes)


def _encode_ascii_hex(data: bytes, line_width: int = 76) -> str:
    hex_str = data.hex().upper()
    return "\n".join(
        hex_str[i : i + line_width] for i in range(0, len(hex_str), line_width)
    )


def write_svg_preview(
    output_svg: Path,
    width_px: int,
    height_px: int,
    scale_px_to_pt: float,
    vector_layers: List[Tuple[Tuple[float, float, float], str]],
    cut_bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
    include_white_backing: bool = True,
    include_cut_contour: bool = True,
    lossless_rgb_artwork: Optional[np.ndarray] = None,
) -> None:
    """Записывает дополнительный SVG-файл с изображением и векторным контуром реза CutContour."""
    import base64

    width_mm = (width_px * scale_px_to_pt) / MM_TO_PT
    height_mm = (height_px * scale_px_to_pt) / MM_TO_PT

    cut_d_parts: List[str] = []
    for loop in cut_bezier_loops:
        if not loop:
            continue
        p0 = loop[0][0]
        cut_d_parts.append(f"M {p0[0]:.2f} {p0[1]:.2f}")
        for _, c1, c2, p3 in loop:
            cut_d_parts.append(
                f"C {c1[0]:.2f} {c1[1]:.2f}, {c2[0]:.2f} {c2[1]:.2f}, {p3[0]:.2f} {p3[1]:.2f}"
            )
        cut_d_parts.append("Z")
    cut_d = " ".join(cut_d_parts)

    svg_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.2f}mm" height="{height_mm:.2f}mm" viewBox="0 0 {width_px} {height_px}">',
    ]

    if include_white_backing and cut_d:
        svg_lines.append(
            f'  <path id="WhiteBacking" d="{cut_d}" fill="#FFFFFF" stroke="none" />'
        )

    if lossless_rgb_artwork is not None:
        if cut_d:
            svg_lines.extend(
                [
                    "  <defs>",
                    '    <clipPath id="StickerClip">',
                    f'      <path d="{cut_d}" />',
                    "    </clipPath>",
                    "  </defs>",
                ]
            )
        png_buf = io.BytesIO()
        Image.fromarray(lossless_rgb_artwork, mode="RGB").save(png_buf, format="PNG")
        b64 = base64.b64encode(png_buf.getvalue()).decode("ascii")
        clip_attr = ' clip-path="url(#StickerClip)"' if cut_d else ""
        svg_lines.append(
            f'  <image id="Artwork" width="{width_px}" height="{height_px}" href="data:image/png;base64,{b64}"{clip_attr} />'
        )
    else:
        svg_lines.append('  <g id="Artwork">')
        for rgb_val, d_with_meta in vector_layers:
            tx, ty = 0.0, 0.0
            d_str = d_with_meta
            if d_str.startswith("__TRANSLATE__("):
                prefix, d_str = d_str.split("):", 1)
                coords = prefix[len("__TRANSLATE__(") :].split(",")
                tx, ty = float(coords[0]), float(coords[1])
            r_i = int(round(rgb_val[0] * 255))
            g_i = int(round(rgb_val[1] * 255))
            b_i = int(round(rgb_val[2] * 255))
            hex_c = f"#{r_i:02X}{g_i:02X}{b_i:02X}"
            transform_str = (
                f' transform="translate({tx:.2f},{ty:.2f})"'
                if (abs(tx) > 1e-4 or abs(ty) > 1e-4)
                else ""
            )
            svg_lines.append(
                f'    <path d="{d_str}" fill="{hex_c}" fill-rule="evenodd"{transform_str} />'
            )
        svg_lines.append("  </g>")

    if include_cut_contour and cut_d:
        svg_lines.append(
            f'  <path id="CutContour" d="{cut_d}" fill="none" stroke="#EC008C" stroke-width="1.5" />'
        )

    svg_lines.append("</svg>")
    output_svg.write_text("\n".join(svg_lines), encoding="utf-8")


def _translate_bezier_loops(
    loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]],
    dx: float,
    dy: float,
) -> List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]]:
    out = []
    for loop in loops:
        new_loop = []
        for p0, c1, c2, p3 in loop:
            new_loop.append(
                (
                    (p0[0] + dx, p0[1] + dy),
                    (c1[0] + dx, c1[1] + dy),
                    (c2[0] + dx, c2[1] + dy),
                    (p3[0] + dx, p3[1] + dy),
                )
            )
        out.append(new_loop)
    return out

from pricing import calculate_nesting_and_price



def render_a4_comparison_preview(
    sticker_preview_rgb: np.ndarray,
    item_w_mm: float,
    item_h_mm: float,
    a4_info: dict,
) -> np.ndarray:
    """
    Рисует сдвоенное превью:
    - Слева: крупный план макета с контурами CutContour (розовый) и PerfCutContour (голубой).
    - Справа: наглядный макет листа А4 (210 × 297 мм) с сантиметровой сеткой,
      где в реальном масштабе 1:1 показана текущая наклейка и пунктиром — сколько ещё таких поместится на А4.
    """
    sh, sw = sticker_preview_rgb.shape[:2]
    target_h = max(620, min(960, sh))
    scale_left = target_h / float(max(1, sh))
    left_w = max(320, min(900, int(round(sw * scale_left))))
    left_img = cv2.resize(
        sticker_preview_rgb,
        (left_w, target_h),
        interpolation=cv2.INTER_AREA if scale_left < 1.0 else cv2.INTER_LINEAR,
    )

    right_w = max(380, int(round(target_h * 0.76)))
    panel = np.full((target_h, right_w, 3), (30, 34, 45), dtype=np.uint8)

    # Масштаб для отображения листа А4 (210×297 мм) ИЛИ если стикер больше А4 — чтобы влезло и то, и другое
    max_w_mm = max(210.0, item_w_mm + 16.0)
    max_h_mm = max(297.0, item_h_mm + 16.0)
    avail_w_px = right_w - 64
    avail_h_px = target_h - 110
    px_per_mm_a4 = min(avail_w_px / max_w_mm, avail_h_px / max_h_mm)

    a4_w_px = max(40, int(round(210.0 * px_per_mm_a4)))
    a4_h_px = max(56, int(round(297.0 * px_per_mm_a4)))

    x0 = (right_w - a4_w_px) // 2
    y0 = 56
    x1 = x0 + a4_w_px
    y1 = y0 + a4_h_px

    # Тень и белый лист А4
    cv2.rectangle(panel, (x0 + 5, y0 + 5), (x1 + 5, y1 + 5), (15, 17, 23), -1)
    cv2.rectangle(panel, (x0, y0), (x1, y1), (252, 252, 252), -1)

    # Сантиметровая сетка на листе А4 (каждые 50 мм — заметная линия, каждые 10 мм — риски по краям)
    for mm_x in range(10, 210, 10):
        gx = x0 + int(round(mm_x * px_per_mm_a4))
        if mm_x % 50 == 0:
            cv2.line(panel, (gx, y0), (gx, y1), (230, 233, 240), 1)
            cv2.putText(
                panel,
                f"{mm_x // 10}",
                (gx - 6, y0 - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.36,
                (180, 190, 210),
                1,
                cv2.LINE_AA,
            )
        else:
            cv2.line(panel, (gx, y0), (gx, y0 + 5), (200, 205, 215), 1)

    for mm_y in range(10, 297, 10):
        gy = y0 + int(round(mm_y * px_per_mm_a4))
        if mm_y % 50 == 0:
            cv2.line(panel, (x0, gy), (x1, gy), (230, 233, 240), 1)
            cv2.putText(
                panel,
                f"{mm_y // 10}",
                (x0 - 22, gy + 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.36,
                (180, 190, 210),
                1,
                cv2.LINE_AA,
            )
        else:
            cv2.line(panel, (x0, gy), (x0 + 5, gy), (200, 205, 215), 1)

    cv2.rectangle(panel, (x0, y0), (x1, y1), (0, 174, 239), 2)

    # Размещаем стикер(ы) на листе А4 в реальном масштабе
    rotated_on_a4 = bool(a4_info.get("a4_rotated", False))
    a4_cols = int(a4_info.get("a4_cols", 1))
    a4_rows = int(a4_info.get("a4_rows", 1))
    a4_fit_qty = int(a4_info.get("a4_fit_qty", 1))

    sticker_src = sticker_preview_rgb
    draw_w_mm, draw_h_mm = item_w_mm, item_h_mm
    if rotated_on_a4 and item_w_mm <= 287.0 and item_h_mm <= 200.0:
        sticker_src = cv2.rotate(sticker_preview_rgb, cv2.ROTATE_90_CLOCKWISE)
        draw_w_mm, draw_h_mm = item_h_mm, item_w_mm

    st_w_px = max(12, int(round(draw_w_mm * px_per_mm_a4)))
    st_h_px = max(12, int(round(draw_h_mm * px_per_mm_a4)))
    gap_px = max(2, int(round(3.0 * px_per_mm_a4)))
    margin_px = max(3, int(round(5.0 * px_per_mm_a4)))

    st_thumb = cv2.resize(sticker_src, (st_w_px, st_h_px), interpolation=cv2.INTER_AREA)

    # Если стикер помещается на А4 — рисуем сетку на А4 (первый стикер ярко, остальные полупрозрачно с пунктиром)
    if st_w_px <= a4_w_px and st_h_px <= a4_h_px:
        total_grid_w = a4_cols * st_w_px + max(0, a4_cols - 1) * gap_px
        total_grid_h = a4_rows * st_h_px + max(0, a4_rows - 1) * gap_px
        start_x = x0 + max(margin_px, (a4_w_px - total_grid_w) // 2)
        start_y = y0 + max(margin_px, (a4_h_px - total_grid_h) // 2)

        for r_i in range(a4_rows):
            for c_i in range(a4_cols):
                cx = start_x + c_i * (st_w_px + gap_px)
                cy = start_y + r_i * (st_h_px + gap_px)
                if cx + st_w_px > x1 or cy + st_h_px > y1:
                    continue
                if r_i == 0 and c_i == 0:
                    panel[cy : cy + st_h_px, cx : cx + st_w_px] = st_thumb
                    cv2.rectangle(panel, (cx, cy), (cx + st_w_px, cy + st_h_px), (236, 0, 140), 2)
                else:
                    roi = panel[cy : cy + st_h_px, cx : cx + st_w_px]
                    blended = cv2.addWeighted(roi, 0.55, st_thumb, 0.45, 0)
                    panel[cy : cy + st_h_px, cx : cx + st_w_px] = blended
                    cv2.rectangle(panel, (cx, cy), (cx + st_w_px, cy + st_h_px), (220, 140, 190), 1)
    else:
        # Стикер крупнее листа А4 — рисуем его поверх по центру, чтобы было видно сравнение с листом А4
        cx = max(4, (right_w - st_w_px) // 2)
        cy = max(48, y0 + (a4_h_px - st_h_px) // 2)
        cl_w = min(st_w_px, right_w - cx - 4)
        cl_h = min(st_h_px, target_h - cy - 40)
        if cl_w > 10 and cl_h > 10:
            roi = panel[cy : cy + cl_h, cx : cx + cl_w]
            sub = st_thumb[:cl_h, :cl_w]
            panel[cy : cy + cl_h, cx : cx + cl_w] = cv2.addWeighted(roi, 0.25, sub, 0.75, 0)
            cv2.rectangle(panel, (cx, cy), (cx + cl_w, cy + cl_h), (236, 0, 140), 2)

    # Заголовок и подпись на правой панели
    cv2.putText(
        panel,
        "LIST A4 (21 x 29.7 cm)",
        (x0, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    footer_txt = (
        f"Sticker: {item_w_mm/10.0:.1f}x{item_h_mm/10.0:.1f} cm | Na A4: {a4_fit_qty} pcs"
        if st_w_px <= a4_w_px and st_h_px <= a4_h_px
        else f"Sticker: {item_w_mm/10.0:.1f}x{item_h_mm/10.0:.1f} cm (> A4)"
    )
    cv2.putText(
        panel,
        footer_txt,
        (18, target_h - 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (0, 210, 255),
        1,
        cv2.LINE_AA,
    )

    divider = np.full((target_h, 4, 3), (60, 68, 85), dtype=np.uint8)
    return np.hstack([left_img, divider, panel])


def convert_image_to_plotter_eps(
    input_path: Path,
    output_eps: Optional[Path] = None,
    size_mm: float = 100.0,
    offset_mm: float = 2.5,
    bg_tolerance: float = 12.0,
    keep_background: bool = False,
    no_cut_contour: bool = False,
    vectorize_artwork: bool = False,
    also_svg: bool = False,
    filter_speckle: int = 2,
    color_precision: int = 8,
    preview_png_path: Optional[Path] = None,
    perf_cut_mode: str = "none",
    nesting_qty: int = 1,
    roll_width_mm: float = 1370.0,
    stretch_x: float = 1.0,
    stretch_y: float = 1.0,
    fit_a4_sheet: bool = False,
    render_a4_guide_on_preview: bool = False,
    perf_cut_passes: int = 2,
) -> Path:
    """
    Полный цикл обработки изображения и сохранения готового .eps файла для плоттера Roland.
    Поддерживает:
    - 100% сохранение качества оригинала (Lossless Print & Cut)
    - Независимое растяжение/сжатие по осям (stretch_x, stretch_y)
    - Авто-раскладку на стандартный лист А4 (210×297 мм, fit_a4_sheet=True) или на рулон (nesting_qty > 1)
    - Двойной контур реза: CutContour (надсечка) + PerfCutContour (рамка стикерпака 'sheet_frame' или сквозная вырубка 'die_cut')
    """
    input_path = input_path.resolve()
    if output_eps is None:
        output_eps = input_path.with_suffix(".eps")
    else:
        output_eps = output_eps.resolve()

    # 1. Загружаем изображение в 100% оригинальном качестве без размытия
    orig_rgb, alpha = load_original_image(input_path)

    # 2. Выделение переднего плана (удаление внешнего фона с сохранением белых обводок)
    fg_mask = extract_foreground_mask(
        orig_rgb, alpha, bg_tolerance=bg_tolerance, keep_background=keep_background
    )

    # 3. Заполнение внутренних дырок силуэта для чистого внешнего контура
    silhouette = fill_external_silhouette(fg_mask)

    # Кадрируем по рамке силуэта перед добавлением полей под отступ (offset)
    ys, xs = np.nonzero(silhouette)
    if len(xs) > 0 and len(ys) > 0:
        x0, x1 = int(np.min(xs)), int(np.max(xs)) + 1
        y0, y1 = int(np.min(ys)), int(np.max(ys)) + 1
        orig_rgb = orig_rgb[y0:y1, x0:x1]
        fg_mask = fg_mask[y0:y1, x0:x1]
        silhouette = silhouette[y0:y1, x0:x1]

    # 3b. Независимое масштабирование и растяжение/сжатие по осям (stretch_x, stretch_y) БЕЗ ограничений размера холста
    stretch_x = max(0.05, float(stretch_x))
    stretch_y = max(0.05, float(stretch_y))
    oh, ow = silhouette.shape[:2]
    base_longest_px = float(max(ow, oh))
    base_w_mm = (float(ow) / base_longest_px) * max(1.0, float(size_mm))
    base_h_mm = (float(oh) / base_longest_px) * max(1.0, float(size_mm))
    target_obj_w_mm = base_w_mm * stretch_x
    _target_obj_h_mm = base_h_mm * stretch_y

    # Изменяем пропорцию пиксельной сетки только если соотношение stretch_x / stretch_y отличается от 1:1
    if abs(stretch_x - stretch_y) > 0.005 * max(stretch_x, stretch_y):
        min_s = min(stretch_x, stretch_y)
        rx = stretch_x / min_s
        ry = stretch_y / min_s
        # Ограничиваем только внутренний растровый буфер (до 4500px), не ограничивая физический размер в мм/метрах
        max_dim_After = max(ow * rx, oh * ry)
        if max_dim_After > 4500.0:
            cap_f = 4500.0 / max_dim_After
            rx *= cap_f
            ry *= cap_f
        new_w = max(32, int(round(ow * rx)))
        new_h = max(32, int(round(oh * ry)))
        if new_w != ow or new_h != oh:
            orig_rgb = cv2.resize(orig_rgb, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
            fg_mask = cv2.resize(fg_mask, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            _, fg_mask = cv2.threshold(fg_mask, 127, 255, cv2.THRESH_BINARY)
            silhouette = fill_external_silhouette(fg_mask)

    obj_h, obj_w = silhouette.shape[:2]
    longest_obj_px = float(max(obj_w, obj_h))

    px_per_mm = float(obj_w) / max(1.0, target_obj_w_mm)
    offset_px = max(0.0, float(offset_mm) * px_per_mm)

    extra_perf_mm = 3.5 if perf_cut_mode in ("sheet_frame", "die_cut") else 0.0
    extra_perf_px = extra_perf_mm * px_per_mm

    pad = int(math.ceil(offset_px + extra_perf_px + max(10.0, longest_obj_px * 0.025)))
    rgb_padded = np.pad(
        orig_rgb, ((pad, pad), (pad, pad), (0, 0)), mode="constant", constant_values=255
    )
    fg_padded = np.pad(
        fg_mask, ((pad, pad), (pad, pad)), mode="constant", constant_values=0
    )
    sil_padded = np.pad(
        silhouette, ((pad, pad), (pad, pad)), mode="constant", constant_values=0
    )

    # 4. Построение гладкого векторного внешнего контура надсечки CutContour
    smoothing_sigma = max(2.0, offset_px * 0.22) if offset_px > 0 else 1.5
    _, cut_bezier_loops = build_smooth_cut_contours(
        sil_padded, offset_px=offset_px, smoothing_sigma=smoothing_sigma
    )

    # 4b. Построение контура сквозной высечки PerfCutContour (для Roland VersaWorks)
    perf_cut_bezier_loops: List[List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]]] = []
    canvas_h, canvas_w = sil_padded.shape[:2]

    if perf_cut_mode == "sheet_frame" and not fit_a4_sheet:
        # Скруглённая рамка сквозного реза вокруг всего стикерпака / макета
        margin_px = max(4.0, 2.0 * px_per_mm)
        corner_r_px = max(8.0, 4.5 * px_per_mm)
        perf_cut_bezier_loops = [
            build_rounded_rect_bezier_loop(
                margin_px,
                margin_px,
                canvas_w - margin_px,
                canvas_h - margin_px,
                corner_r_px,
            )
        ]
    elif perf_cut_mode == "die_cut":
        # Фигурная сквозная высечка вокруг каждой наклейки с отступом +1.8 мм наружу от CutContour
        die_offset_px = offset_px + max(3.0, 1.8 * px_per_mm)
        _, perf_cut_bezier_loops = build_smooth_cut_contours(
            sil_padded,
            offset_px=die_offset_px,
            smoothing_sigma=max(2.5, die_offset_px * 0.24),
        )

    # 5. Подготовка графики внутри контуров
    vector_layers: List[Tuple[Tuple[float, float, float], str]] = []
    lossless_rgb_for_eps: Optional[np.ndarray] = None

    if not vectorize_artwork:
        decontam_rgb = (
            decontaminate_edge_colors(rgb_padded, fg_padded, border_px=2)
            if not keep_background
            else rgb_padded
        )
        alpha_float = ndimage.gaussian_filter(
            fg_padded.astype(np.float32) / 255.0, sigma=0.65
        )[:, :, None]
        core_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        core_inside = cv2.erode(fg_padded, core_k, iterations=1) > 0
        composite = (
            decontam_rgb.astype(np.float32) * alpha_float
            + 255.0 * (1.0 - alpha_float)
        ).astype(np.uint8)
        composite[core_inside] = rgb_padded[core_inside]
        lossless_rgb_for_eps = composite
    else:
        decontam_rgb = (
            decontaminate_edge_colors(rgb_padded, fg_padded, border_px=2)
            if not keep_background
            else rgb_padded
        )
        rgba_for_trace = np.zeros(
            (rgb_padded.shape[0], rgb_padded.shape[1], 4), dtype=np.uint8
        )
        rgba_for_trace[:, :, :3] = decontam_rgb
        rgba_for_trace[:, :, 3] = fg_padded
        vector_layers = vectorize_image_with_vtracer(
            rgba_for_trace,
            filter_speckle=filter_speckle,
            color_precision=color_precision,
        )

    single_item_w_mm = canvas_w / px_per_mm
    single_item_h_mm = canvas_h / px_per_mm

    # 6a. Режим авто-заполнения листа А4 (210 × 297 мм)
    if fit_a4_sheet and lossless_rgb_for_eps is not None:
        a4_calc = calculate_nesting_and_price(single_item_w_mm, single_item_h_mm, qty=1, roll_width_mm=roll_width_mm)
        cols = max(1, int(a4_calc["a4_cols"]))
        rows = max(1, int(a4_calc["a4_rows"]))
        a4_rot = bool(a4_calc["a4_rotated"])

        sheet_w_mm = 297.0 if a4_rot else 210.0
        sheet_h_mm = 210.0 if a4_rot else 297.0
        gap_mm = 3.0

        # Ограничиваем плотность пикселей листа А4 разумным пределом (~2600px по длинной стороне = ~225 DPI)
        target_a4_px_per_mm = min(px_per_mm, 2600.0 / 297.0)
        cell_scale = target_a4_px_per_mm / px_per_mm

        if cell_scale < 0.999:
            new_cw = max(32, int(round(canvas_w * cell_scale)))
            new_ch = max(32, int(round(canvas_h * cell_scale)))
            tile_rgb = cv2.resize(lossless_rgb_for_eps, (new_cw, new_ch), interpolation=cv2.INTER_AREA)
            px_per_mm = target_a4_px_per_mm

            def _scale_lps(lps):
                return [
                    [
                        (
                            (p0[0] * cell_scale, p0[1] * cell_scale),
                            (c1[0] * cell_scale, c1[1] * cell_scale),
                            (c2[0] * cell_scale, c2[1] * cell_scale),
                            (p3[0] * cell_scale, p3[1] * cell_scale),
                        )
                        for p0, c1, c2, p3 in lp
                    ]
                    for lp in lps
                ]

            base_cut_loops = _scale_lps(cut_bezier_loops)
            base_perf_loops = _scale_lps(perf_cut_bezier_loops)
            canvas_w, canvas_h = new_cw, new_ch
        else:
            tile_rgb = lossless_rgb_for_eps
            base_cut_loops = cut_bezier_loops
            base_perf_loops = perf_cut_bezier_loops

        gap_px = max(2, int(round(gap_mm * px_per_mm)))
        grid_w = cols * canvas_w + max(0, cols - 1) * gap_px
        grid_h = rows * canvas_h + max(0, rows - 1) * gap_px

        sheet_w = max(grid_w + 16, int(round(sheet_w_mm * px_per_mm)))
        sheet_h = max(grid_h + 16, int(round(sheet_h_mm * px_per_mm)))
        sheet_rgb = np.full((sheet_h, sheet_w, 3), 255, dtype=np.uint8)

        ox = max(4, (sheet_w - grid_w) // 2)
        oy = max(4, (sheet_h - grid_h) // 2)

        all_cuts = []
        all_perfs = []
        for r_idx in range(rows):
            for c_idx in range(cols):
                dx = ox + c_idx * (canvas_w + gap_px)
                dy = oy + r_idx * (canvas_h + gap_px)
                if dy + canvas_h <= sheet_h and dx + canvas_w <= sheet_w:
                    sheet_rgb[dy : dy + canvas_h, dx : dx + canvas_w] = tile_rgb
                    all_cuts.extend(_translate_bezier_loops(base_cut_loops, float(dx), float(dy)))
                    if base_perf_loops:
                        all_perfs.extend(_translate_bezier_loops(base_perf_loops, float(dx), float(dy)))

        # Добавляем голубую рамку сквозной высечки PerfCutContour по периметру листа А4
        margin_a4_px = max(4.0, 2.0 * px_per_mm)
        corner_a4_px = max(8.0, 4.0 * px_per_mm)
        all_perfs.append(
            build_rounded_rect_bezier_loop(
                margin_a4_px,
                margin_a4_px,
                sheet_w - margin_a4_px,
                sheet_h - margin_a4_px,
                corner_a4_px,
            )
        )

        lossless_rgb_for_eps = sheet_rgb
        cut_bezier_loops = all_cuts
        perf_cut_bezier_loops = all_perfs
        canvas_w, canvas_h = sheet_w, sheet_h

    # 6b. Если заказан тираж на рулон (nesting_qty > 1), раскладываем копии в сетку с зазором 3 мм
    nesting_qty = max(1, int(nesting_qty))
    if not fit_a4_sheet and nesting_qty > 1 and lossless_rgb_for_eps is not None:
        item_w_mm = canvas_w / px_per_mm
        _item_h_mm = canvas_h / px_per_mm
        gap_mm = 3.0
        usable_roll_mm = max(item_w_mm, float(roll_width_mm) - 10.0)
        cols = max(1, int((usable_roll_mm + gap_mm) // (item_w_mm + gap_mm)))
        cols = min(nesting_qty, cols)
        rows = int(math.ceil(nesting_qty / float(cols)))

        # Масштабируем разрешение одной ячейки при больших тиражах, чтобы общий холст не превышал ~3400px
        max_sheet_px = 3400.0
        est_w_px = cols * canvas_w
        est_h_px = rows * canvas_h
        cell_scale = min(1.0, max_sheet_px / float(max(est_w_px, est_h_px)))

        if cell_scale < 0.999:
            new_cw = max(32, int(round(canvas_w * cell_scale)))
            new_ch = max(32, int(round(canvas_h * cell_scale)))
            tile_rgb = cv2.resize(lossless_rgb_for_eps, (new_cw, new_ch), interpolation=cv2.INTER_AREA)
            px_per_mm *= cell_scale

            def _scale_loops(lps):
                return [
                    [
                        (
                            (p0[0] * cell_scale, p0[1] * cell_scale),
                            (c1[0] * cell_scale, c1[1] * cell_scale),
                            (c2[0] * cell_scale, c2[1] * cell_scale),
                            (p3[0] * cell_scale, p3[1] * cell_scale),
                        )
                        for p0, c1, c2, p3 in lp
                    ]
                    for lp in lps
                ]

            base_cut_loops = _scale_loops(cut_bezier_loops)
            base_perf_loops = _scale_loops(perf_cut_bezier_loops)
            canvas_w, canvas_h = new_cw, new_ch
        else:
            tile_rgb = lossless_rgb_for_eps
            base_cut_loops = cut_bezier_loops
            base_perf_loops = perf_cut_bezier_loops

        gap_px = max(2, int(round(gap_mm * px_per_mm)))
        sheet_w = cols * canvas_w + max(0, cols - 1) * gap_px
        sheet_h = rows * canvas_h + max(0, rows - 1) * gap_px
        sheet_rgb = np.full((sheet_h, sheet_w, 3), 255, dtype=np.uint8)
        all_cuts = []
        all_perfs = []

        placed = 0
        for r_idx in range(rows):
            for c_idx in range(cols):
                if placed >= nesting_qty:
                    break
                dx = c_idx * (canvas_w + gap_px)
                dy = r_idx * (canvas_h + gap_px)
                sheet_rgb[dy : dy + canvas_h, dx : dx + canvas_w] = tile_rgb
                all_cuts.extend(_translate_bezier_loops(base_cut_loops, float(dx), float(dy)))
                if base_perf_loops:
                    all_perfs.extend(_translate_bezier_loops(base_perf_loops, float(dx), float(dy)))
                placed += 1

        lossless_rgb_for_eps = sheet_rgb
        cut_bezier_loops = all_cuts
        perf_cut_bezier_loops = all_perfs
        canvas_w, canvas_h = sheet_w, sheet_h

    scale_px_to_pt = MM_TO_PT / px_per_mm

    # 7. Запись итогового EPS-файла
    write_eps_file(
        output_eps=output_eps,
        title=input_path.name,
        width_px=canvas_w,
        height_px=canvas_h,
        scale_px_to_pt=scale_px_to_pt,
        vector_layers=vector_layers,
        cut_bezier_loops=cut_bezier_loops,
        include_white_backing=(offset_mm > 0 and not keep_background) or (perf_cut_mode != "none") or fit_a4_sheet,
        include_cut_contour=(not no_cut_contour),
        spot_color_name="CutContour",
        lossless_rgb_artwork=lossless_rgb_for_eps,
        preview_png_path=preview_png_path,
        perf_cut_bezier_loops=perf_cut_bezier_loops,
        perf_spot_color_name="PerfCutContour",
        perf_cut_passes=perf_cut_passes,
    )

    # 7b. Если включён режим визуального гида на листе А4 для превью (в Telegram-боте для одиночного макета)
    if render_a4_guide_on_preview and preview_png_path is not None and preview_png_path.exists() and not fit_a4_sheet and nesting_qty == 1:
        try:
            raw_prev = np.array(Image.open(preview_png_path).convert("RGB"))
            a4_info = calculate_nesting_and_price(single_item_w_mm, single_item_h_mm, qty=1, roll_width_mm=roll_width_mm)
            comp_prev = render_a4_comparison_preview(raw_prev, single_item_w_mm, single_item_h_mm, a4_info)
            Image.fromarray(comp_prev, mode="RGB").save(preview_png_path, format="PNG")
        except Exception:
            pass

    if also_svg:
        svg_path = output_eps.with_suffix(".svg")
        write_svg_preview(
            output_svg=svg_path,
            width_px=canvas_w,
            height_px=canvas_h,
            scale_px_to_pt=scale_px_to_pt,
            vector_layers=vector_layers,
            cut_bezier_loops=cut_bezier_loops,
            include_white_backing=(offset_mm > 0 and not keep_background),
            include_cut_contour=(not no_cut_contour),
            lossless_rgb_artwork=lossless_rgb_for_eps,
        )

    return output_eps


def collect_input_files(paths: List[str]) -> List[Path]:
    collected: List[Path] = []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            collected.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    collected.append(child)
    return collected


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jpeg2eps",
        description=(
            "Конвертер JPEG/PNG стикеров и стикерпаков в EPS без потери качества "
            "с построением векторных контуров реза CutContour для плоттеров."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Путь к одному или нескольким файлам (JPEG/PNG/WebP) или папкам с картинками",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=None,
        help="Папка для сохранения .eps (по умолчанию сохраняется рядом с исходным файлом)",
    )
    parser.add_argument(
        "--offset-mm",
        type=float,
        default=2.5,
        help="Отступ контура реза (CutContour) вокруг стикера в миллиметрах (по умолчанию: 2.5 мм, 0 = вплотную)",
    )
    parser.add_argument(
        "--size-mm",
        type=float,
        default=100.0,
        help="Физический размер большей стороны в миллиметрах в EPS (по умолчанию: 100 мм)",
    )
    parser.add_argument(
        "--bg-tolerance",
        type=float,
        default=12.0,
        help="Чувствительность удаления белого/однородного фона с краёв (по умолчанию: 12.0)",
    )
    parser.add_argument(
        "--keep-bg",
        action="store_true",
        help="Не вырезать внешний фон изображения",
    )
    parser.add_argument(
        "--no-cut-contour",
        action="store_true",
        help="Не добавлять линию реза CutContour",
    )
    parser.add_argument(
        "--vectorize",
        action="store_true",
        help="Перерисовать саму картинку внутри контура в векторные кривые (для простых плоских логотипов)",
    )
    parser.add_argument(
        "--contour-only",
        action="store_true",
        help="Оставить исходную картинку внутри без потери качества + векторный контур реза (включено по умолчанию)",
    )
    parser.add_argument(
        "--svg",
        action="store_true",
        help="Дополнительно сохранять .svg файл рядом с .eps",
    )
    parser.add_argument(
        "--speckle",
        type=int,
        default=2,
        help="Фильтрация мелкого векторного мусора при --vectorize (по умолчанию: 2)",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=8,
        help="Точность цветов при --vectorize от 1 до 8 (по умолчанию: 8)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Показать созданный файл в Finder и воспроизвести звук по завершении (используется из Finder)",
    )
    return parser


def notify_and_reveal_in_finder(created_files: List[Path], error_msg: Optional[str] = None) -> None:
    if error_msg:
        subprocess.Popen(
            ["/usr/bin/afplay", "/System/Library/Sounds/Basso.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    if created_files:
        subprocess.Popen(
            ["/usr/bin/afplay", "/System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["/usr/bin/open", "-R"] + [str(p) for p in created_files],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    raw_inputs = list(args.inputs) if args.inputs else []
    if not raw_inputs and not sys.stdin.isatty():
        for line in sys.stdin:
            s = line.strip()
            if s:
                raw_inputs.append(s)

    files = collect_input_files(raw_inputs)
    if not files:
        msg = "⚠️ Не найдено подходящих изображений (JPEG/PNG/WebP)."
        print(msg, file=sys.stderr)
        if args.notify:
            notify_and_reveal_in_finder([], error_msg=msg)
        return 1

    out_dir: Optional[Path] = None
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

    created_eps_files: List[Path] = []
    for fpath in files:
        target_eps = (out_dir / f"{fpath.stem}.eps") if out_dir else fpath.with_suffix(".eps")
        try:
            print(f"⏳ Конвертация без потери качества + CutContour: {fpath.name} -> {target_eps.name} ...")
            convert_image_to_plotter_eps(
                input_path=fpath,
                output_eps=target_eps,
                size_mm=args.size_mm,
                offset_mm=args.offset_mm,
                bg_tolerance=args.bg_tolerance,
                keep_background=args.keep_bg,
                no_cut_contour=args.no_cut_contour,
                vectorize_artwork=args.vectorize,
                also_svg=args.svg,
                filter_speckle=args.speckle,
                color_precision=args.precision,
            )
            print(f"✅ Готово: {target_eps}")
            created_eps_files.append(target_eps)
        except Exception as exc:
            print(f"❌ Ошибка при обработке {fpath.name}: {exc}", file=sys.stderr)

    if args.notify:
        if created_eps_files:
            notify_and_reveal_in_finder(created_eps_files)
        else:
            notify_and_reveal_in_finder([], error_msg="Ошибка конвертации")

    return 0 if created_eps_files else 1


if __name__ == "__main__":
    sys.exit(main())
