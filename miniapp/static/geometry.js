// Геометрия наклейки и раскладки для живого превью.
// Порт miniapp/geometry.py (размеры ячейки как в jpeg2eps.py) и раскладки из pricing.py / jpeg2eps.py.
// Совпадение с Python проверяется тестом geometry.test.mjs по tests/golden/miniapp_layout.json.

export const EXTRA_PERF_MM = 3.5;
export const STRETCH_RASTER_CAP_PX = 4500;
export const MAX_PX_PER_MM = 60;
export const GAP_MM = 3.0;
export const A4 = { w: 210, h: 297, usableW: 200, usableH: 287 };
export const ROLL_MARGIN_MM = 10.0;

// Python round(): банковское округление до чётного на .5
export function pyRound(x) {
  const r = Math.round(x);
  if (Math.abs(x % 1) === 0.5 && r % 2 !== 0) return r - 1;
  return r;
}

const round = (v, d) => {
  const k = 10 ** d;
  return pyRound(v * k) / k;
};

// Размеры одной наклейки в мм по рамке силуэта исходника (см. item_geometry в miniapp/geometry.py)
export function itemGeometry(cropW, cropH, p) {
  const ow = Math.max(1, cropW | 0);
  const oh = Math.max(1, cropH | 0);
  const sx = Math.max(0.05, p.stretch_x);
  const sy = Math.max(0.05, p.stretch_y);
  const size = Math.max(1, p.size_mm);

  const baseLongest = Math.max(ow, oh);
  const baseW = (ow / baseLongest) * size;
  const baseH = (oh / baseLongest) * size;
  const targetW = baseW * sx;
  const targetH = baseH * sy;

  let objW = ow;
  let objH = oh;
  if (Math.abs(sx - sy) > 0.005 * Math.max(sx, sy)) {
    const minS = Math.min(sx, sy);
    let rx = sx / minS;
    let ry = sy / minS;
    const maxAfter = Math.max(ow * rx, oh * ry);
    if (maxAfter > STRETCH_RASTER_CAP_PX) {
      const cap = STRETCH_RASTER_CAP_PX / maxAfter;
      rx *= cap;
      ry *= cap;
    }
    objW = Math.max(32, pyRound(ow * rx));
    objH = Math.max(32, pyRound(oh * ry));
  }

  const densityCap = MAX_PX_PER_MM / (objW / Math.max(1, targetW));
  if (densityCap < 1) {
    const w = objW;
    objW = Math.max(32, pyRound(w * densityCap));
    objH = Math.max(32, pyRound(objH * densityCap));
  }

  const longestObj = Math.max(objW, objH);
  const pxPerMm = objW / Math.max(1, targetW);
  const offsetPx = Math.max(0, p.offset_mm * pxPerMm);
  const perf = p.perf_cut_mode === 'sheet_frame' || p.perf_cut_mode === 'die_cut';
  const extraPx = (perf ? EXTRA_PERF_MM : 0) * pxPerMm;
  const pad = Math.ceil(offsetPx + extraPx + Math.max(10, longestObj * 0.025));

  const artW = objW / pxPerMm;
  const artH = objH / pxPerMm;
  return {
    art_w_mm: round(artW, 2),
    art_h_mm: round(artH, 2),
    target_h_mm: round(targetH, 2),
    cut_w_mm: round(artW + 2 * p.offset_mm, 2),
    cut_h_mm: round(artH + 2 * p.offset_mm, 2),
    cell_w_mm: round((objW + 2 * pad) / pxPerMm, 3),
    cell_h_mm: round((objH + 2 * pad) / pxPerMm, 3),
    pad_mm: round(pad / pxPerMm, 3),
  };
}

// Сколько ячеек влезает на А4 (та же формула, что a4_* в calculate_nesting_and_price)
export function a4Fit(cellW, cellH, gap = GAP_MM) {
  const f = (a, b) => Math.max(0, Math.floor((a + gap) / (b + gap)));
  const cols1 = f(A4.usableW, cellW);
  const rows1 = f(A4.usableH, cellH);
  const cols2 = f(A4.usableW, cellH);
  const rows2 = f(A4.usableH, cellW);
  if (cols2 * rows2 > cols1 * rows1) {
    return { fit: Math.max(1, cols2 * rows2), cols: Math.max(1, cols2), rows: Math.max(1, rows2), rotated: true };
  }
  return { fit: Math.max(1, cols1 * rows1), cols: Math.max(1, cols1), rows: Math.max(1, rows1), rotated: false };
}

// Как ячейки лягут на лист в EPS: при повороте лист альбомный, наклейки не поворачиваются,
// поэтому по ширине листа идёт a4.rows ячеек, по высоте a4.cols (см. режим fit_a4_sheet в jpeg2eps.py).
export function a4Sheet(cellW, cellH, gap = GAP_MM) {
  const fit = a4Fit(cellW, cellH, gap);
  const sheetW = fit.rotated ? A4.h : A4.w;
  const sheetH = fit.rotated ? A4.w : A4.h;
  const cols = fit.rotated ? fit.rows : fit.cols;
  const rows = fit.rotated ? fit.cols : fit.rows;
  const gridW = cols * cellW + Math.max(0, cols - 1) * gap;
  const gridH = rows * cellH + Math.max(0, rows - 1) * gap;
  const fits = cellW <= A4.usableW + 1e-9 && cellH <= A4.usableH + 1e-9
    || cellW <= A4.usableH + 1e-9 && cellH <= A4.usableW + 1e-9;
  const ox = (sheetW - gridW) / 2;
  const oy = (sheetH - gridH) / 2;
  const cells = [];
  if (fits) {
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        cells.push({ x: ox + c * (cellW + gap), y: oy + r * (cellH + gap) });
      }
    }
  }
  return { sheetW, sheetH, cols, rows, fit: fits ? cells.length : 0, fits, cells };
}

// Раскладка тиража на рулон, как в режиме nesting_qty > 1 в jpeg2eps.py (без поворота)
export function rollLayout(cellW, cellH, qty, rollWidth, gap = GAP_MM) {
  const n = Math.max(1, qty | 0);
  const usable = Math.max(cellW, rollWidth - ROLL_MARGIN_MM);
  const cols = Math.min(n, Math.max(1, Math.floor((usable + gap) / (cellW + gap))));
  const rows = Math.ceil(n / cols);
  const cells = [];
  for (let i = 0; i < n; i++) {
    const r = Math.floor(i / cols);
    const c = i % cols;
    cells.push({ x: c * (cellW + gap), y: r * (cellH + gap) });
  }
  return {
    cols,
    rows,
    width: cols * cellW + Math.max(0, cols - 1) * gap,
    length: rows * cellH + Math.max(0, rows - 1) * gap,
    cells,
  };
}
