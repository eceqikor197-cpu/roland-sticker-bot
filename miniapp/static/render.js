// Отрисовка превью на canvas: наклейка, лист А4, рулон. Все размеры в мм, пиксели только здесь.
//
// Подложка и контур реза строятся из силуэта: путь силуэта заливается и обводится линией
// толщиной 2×кант со скруглёнными стыками. Это расширение формы кругом радиуса канта,
// как dilate в jpeg2eps.py (там ещё есть сглаживание, поэтому углы у сервера чуть мягче).

import { a4Sheet, rollLayout } from './geometry.js';

export const COLORS = {
  cut: '#ec008c',
  perf: '#00aeef',
  backing: '#ffffff',
  board: '#232c38',
  sheet: '#fcfcfc',
  grid: '#e3e7ee',
  gridStrong: '#cfd6e0',
  dashed: '#96a0b4',
  label: '#c9d3dd',
};

// Описание наклейки для отрисовки: геометрия, силуэт, картинка, параметры
// sticker = { geo, polygons, art (ImageBitmap|HTMLImageElement|null), params }

function tracePath(ctx, st, k) {
  const { geo, polygons } = st;
  const pad = geo.pad_mm;
  ctx.beginPath();
  for (const poly of polygons) {
    poly.forEach(([x, y], i) => {
      const px = (pad + x * geo.art_w_mm) * k;
      const py = (pad + y * geo.art_h_mm) * k;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.closePath();
  }
}

function fillDilated(ctx, st, k, radiusMm, color) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  tracePath(ctx, st, k);
  ctx.fill();
  if (radiusMm > 0) {
    ctx.lineWidth = 2 * radiusMm * k;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.stroke();
  }
  ctx.restore();
}

function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.ceil(w));
  c.height = Math.max(1, Math.ceil(h));
  return c;
}

// Линия по границе расширенной формы: заливка радиусом r минус заливка радиусом r - толщина
function outlineDilated(ctx, st, k, radiusMm, color, lineWidthPx) {
  const w = ctx.canvas.width;
  const h = ctx.canvas.height;
  const off = makeCanvas(w, h);
  const o = off.getContext('2d');
  if (radiusMm * k > lineWidthPx) {
    fillDilated(o, st, k, radiusMm, color);
    o.globalCompositeOperation = 'destination-out';
    fillDilated(o, st, k, radiusMm - lineWidthPx / k, '#000');
  } else {
    o.strokeStyle = color;
    o.lineWidth = lineWidthPx;
    o.lineJoin = 'round';
    tracePath(o, st, k);
    o.stroke();
  }
  ctx.drawImage(off, 0, 0);
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

// Одна ячейка (наклейка с полями) в масштабе k px/мм. Прозрачный фон.
export function renderCell(st, k, { lineWidthPx = 1.5 } = {}) {
  const { geo, params } = st;
  const c = makeCanvas(geo.cell_w_mm * k, geo.cell_h_mm * k);
  const ctx = c.getContext('2d');
  const perf = params.perf_cut_mode;
  const off = params.offset_mm;
  const backing = (off > 0 && !params.keep_background) || perf !== 'none' || params.fit_a4_sheet;
  // Вырубка: +1.8 мм наружу от контура реза, как в jpeg2eps.py
  const dieMm = off + 1.8;

  if (perf === 'die_cut') {
    fillDilated(ctx, st, k, dieMm, 'rgba(255,255,255,0.10)');
  }
  if (backing) {
    fillDilated(ctx, st, k, off, COLORS.backing);
  }
  if (st.art) {
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(st.art, geo.pad_mm * k, geo.pad_mm * k, geo.art_w_mm * k, geo.art_h_mm * k);
  }
  outlineDilated(ctx, st, k, off, COLORS.cut, lineWidthPx);
  if (perf === 'die_cut') {
    outlineDilated(ctx, st, k, dieMm, COLORS.perf, lineWidthPx);
  } else if (perf === 'sheet_frame' && !params.fit_a4_sheet) {
    const m = 2 * k;
    ctx.save();
    ctx.strokeStyle = COLORS.perf;
    ctx.lineWidth = lineWidthPx;
    roundRect(ctx, m, m, c.width - 2 * m, c.height - 2 * m, 4.5 * k);
    ctx.stroke();
    ctx.restore();
  }
  return c;
}

function prepare(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = COLORS.board;
  ctx.fillRect(0, 0, w, h);
  return { ctx, w, h, dpr };
}

function label(ctx, text, x, y, align = 'center', color = COLORS.label, size = 12) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.font = `${size}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x, y);
  ctx.restore();
}

const fmt = (mm) => (mm >= 100 ? Math.round(mm) : Math.round(mm * 10) / 10).toLocaleString('ru-RU');

function checker(ctx, x, y, w, h, cell = 8) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.fillStyle = '#2b3542';
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = '#323d4b';
  for (let yy = 0; yy < h; yy += cell) {
    for (let xx = (yy / cell) % 2 ? cell : 0; xx < w; xx += cell * 2) ctx.fillRect(x + xx, y + yy, cell, cell);
  }
  ctx.restore();
}

// Вкладка «Наклейка»: крупно, с размерами по контуру реза
export function drawSticker(canvas, st) {
  const { ctx, w, h, dpr } = prepare(canvas);
  const { geo } = st;
  const margin = 34;
  const k = Math.min((w - 2 * margin) / geo.cell_w_mm, (h - 2 * margin) / geo.cell_h_mm);
  const cw = geo.cell_w_mm * k;
  const ch = geo.cell_h_mm * k;
  const x0 = (w - cw) / 2;
  const y0 = (h - ch) / 2 - 6;
  checker(ctx, x0, y0, cw, ch);
  const cell = renderCell(st, k * dpr, { lineWidthPx: 1.6 * dpr });
  ctx.drawImage(cell, x0, y0, cw, ch);

  // Размерные линии по контуру реза
  const off = st.params.offset_mm;
  const cutX0 = x0 + (geo.pad_mm - off) * k;
  const cutY0 = y0 + (geo.pad_mm - off) * k;
  const cutW = geo.cut_w_mm * k;
  const cutH = geo.cut_h_mm * k;
  ctx.save();
  ctx.strokeStyle = COLORS.label;
  ctx.lineWidth = 1;
  const yb = y0 + ch + 12;
  ctx.beginPath();
  ctx.moveTo(cutX0, yb); ctx.lineTo(cutX0 + cutW, yb);
  ctx.moveTo(cutX0, yb - 4); ctx.lineTo(cutX0, yb + 4);
  ctx.moveTo(cutX0 + cutW, yb - 4); ctx.lineTo(cutX0 + cutW, yb + 4);
  const xr = x0 + cw + 12;
  ctx.moveTo(xr, cutY0); ctx.lineTo(xr, cutY0 + cutH);
  ctx.moveTo(xr - 4, cutY0); ctx.lineTo(xr + 4, cutY0);
  ctx.moveTo(xr - 4, cutY0 + cutH); ctx.lineTo(xr + 4, cutY0 + cutH);
  ctx.stroke();
  ctx.restore();
  label(ctx, `${fmt(geo.cut_w_mm)} мм`, cutX0 + cutW / 2, yb + 12);
  ctx.save();
  ctx.translate(xr + 13, cutY0 + cutH / 2);
  ctx.rotate(-Math.PI / 2);
  label(ctx, `${fmt(geo.cut_h_mm)} мм`, 0, 0);
  ctx.restore();
}

function sheetGrid(ctx, x, y, k, wMm, hMm) {
  ctx.save();
  for (let mm = 10; mm < wMm; mm += 10) {
    ctx.strokeStyle = mm % 50 === 0 ? COLORS.gridStrong : COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + mm * k, y);
    ctx.lineTo(x + mm * k, y + hMm * k);
    ctx.stroke();
  }
  for (let mm = 10; mm < hMm; mm += 10) {
    ctx.strokeStyle = mm % 50 === 0 ? COLORS.gridStrong : COLORS.grid;
    ctx.beginPath();
    ctx.moveTo(x, y + mm * k);
    ctx.lineTo(x + wMm * k, y + mm * k);
    ctx.stroke();
  }
  ctx.restore();
}

// Вкладка «Лист А4»: в режиме А4 все копии как в файле, иначе одна наклейка и пунктиром сколько ещё войдёт
export function drawA4(canvas, st) {
  const { ctx, w, h, dpr } = prepare(canvas);
  const { geo, params } = st;
  const sheet = a4Sheet(geo.cell_w_mm, geo.cell_h_mm);
  const viewW = Math.max(sheet.sheetW, geo.cell_w_mm + 10);
  const viewH = Math.max(sheet.sheetH, geo.cell_h_mm + 10);
  const k = Math.min((w - 40) / viewW, (h - 44) / viewH);
  const sx = (w - sheet.sheetW * k) / 2;
  const sy = (h - sheet.sheetH * k) / 2 + 6;

  ctx.fillStyle = 'rgba(0,0,0,.35)';
  ctx.fillRect(sx + 4, sy + 4, sheet.sheetW * k, sheet.sheetH * k);
  ctx.fillStyle = COLORS.sheet;
  ctx.fillRect(sx, sy, sheet.sheetW * k, sheet.sheetH * k);
  sheetGrid(ctx, sx, sy, k, sheet.sheetW, sheet.sheetH);

  const cell = renderCell(st, k * dpr, { lineWidthPx: Math.max(1, 1.2 * dpr) });
  const cw = geo.cell_w_mm * k;
  const ch = geo.cell_h_mm * k;
  const off = params.offset_mm;

  if (sheet.fits) {
    sheet.cells.forEach((c, i) => {
      const x = sx + c.x * k;
      const y = sy + c.y * k;
      if (params.fit_a4_sheet || i === 0) {
        ctx.drawImage(cell, x, y, cw, ch);
      } else {
        ctx.save();
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = COLORS.dashed;
        ctx.lineWidth = 1;
        ctx.strokeRect(x + (geo.pad_mm - off) * k, y + (geo.pad_mm - off) * k, geo.cut_w_mm * k, geo.cut_h_mm * k);
        ctx.restore();
      }
    });
    if (params.fit_a4_sheet) {
      ctx.save();
      ctx.strokeStyle = COLORS.perf;
      ctx.lineWidth = 1.4;
      roundRect(ctx, sx + 2 * k, sy + 2 * k, (sheet.sheetW - 4) * k, (sheet.sheetH - 4) * k, 4 * k);
      ctx.stroke();
      ctx.restore();
    }
  } else {
    ctx.save();
    ctx.globalAlpha = 0.85;
    ctx.drawImage(cell, (w - cw) / 2, (h - ch) / 2, cw, ch);
    ctx.restore();
  }
  ctx.save();
  ctx.strokeStyle = COLORS.perf;
  ctx.globalAlpha = 0.6;
  ctx.lineWidth = 1;
  ctx.strokeRect(sx, sy, sheet.sheetW * k, sheet.sheetH * k);
  ctx.restore();

  const title = sheet.sheetW > sheet.sheetH ? 'А4 альбомный, 297 × 210 мм' : 'А4, 210 × 297 мм';
  label(ctx, title, w / 2, 14);
  return sheet;
}

// Вкладка «Рулон»: раскладка тиража по ширине рулона, как в файле
export function drawRoll(canvas, st) {
  const { ctx, w, h, dpr } = prepare(canvas);
  const { geo, params } = st;
  const qty = params.fit_a4_sheet ? 1 : params.nesting_qty;
  const lay = rollLayout(geo.cell_w_mm, geo.cell_h_mm, qty, params.roll_width_mm);
  const rollW = params.roll_width_mm;
  const lenMm = lay.length + 20;
  const k = Math.min((w - 24) / rollW, (h - 48) / lenMm);
  const rx = (w - rollW * k) / 2;
  const ry = 26;

  ctx.fillStyle = '#eef1f5';
  ctx.fillRect(rx, ry, rollW * k, lenMm * k);
  ctx.save();
  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = COLORS.dashed;
  ctx.beginPath();
  ctx.moveTo(rx, ry + lenMm * k);
  ctx.lineTo(rx + rollW * k, ry + lenMm * k);
  ctx.stroke();
  ctx.restore();

  const cw = geo.cell_w_mm * k;
  const ch = geo.cell_h_mm * k;
  const ox = rx + 5 * k;
  const oy = ry + 10 * k;
  if (cw * dpr >= 3 && ch * dpr >= 3) {
    const cell = renderCell(st, k * dpr, { lineWidthPx: Math.max(0.8, dpr) });
    lay.cells.forEach((c) => ctx.drawImage(cell, ox + c.x * k, oy + c.y * k, cw, ch));
  } else {
    // Наклейки мельче пикселя: показываем занятую область
    ctx.fillStyle = 'rgba(236,0,140,.35)';
    ctx.fillRect(ox, oy, lay.width * k, lay.length * k);
  }
  label(ctx, `рулон ${fmt(rollW)} мм`, w / 2, 12);
  label(ctx, `${lay.cols} в ряд × ${lay.rows} ряд., длина ${(lay.length / 1000).toFixed(2)} м`, w / 2, Math.min(h - 10, ry + lenMm * k + 14));
  return lay;
}
