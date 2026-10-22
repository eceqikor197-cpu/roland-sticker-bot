// Редактор наклейки (Telegram Mini App): состояние, элементы управления, запросы к API.

import { a4Fit, itemGeometry, rollLayout } from './geometry.js';
import { drawA4, drawRoll, drawSticker } from './render.js';

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

// initData от Telegram; вне Telegram можно передать в #tgWebAppData=... (для отладки)
const initData = tg?.initData || new URLSearchParams(location.hash.slice(1)).get('tgWebAppData') || '';

// Ползунок охватывает ходовые размеры; поля Ш/В принимают всё, что разрешает сервер (limits в /config)
const SLIDER_MIN = 5;
const SLIDER_MAX = 1370;
const limit = (key, fallback) => state.config?.limits?.[key] || fallback;
const SIZE_CHIPS_MM = [30, 50, 70, 100, 150, 210, 297];
const OFFSET_CHIPS = [0, 1.5, 2.5, 4];
const QTY_CHIPS = [10, 25, 50, 100];
const ROLL_WIDTHS = [600, 1000, 1370];

const state = {
  config: null,
  jobId: null,
  name: '',
  params: null,
  sources: {}, // keep_bg (0/1) -> { data, art }
  quote: null,
  view: 'sticker',
  lock: true,
  busy: false,
};

// ---------- API ----------

async function api(path, { method = 'GET', body, raw = false } = {}) {
  const res = await fetch(`api/v1/${path}`, {
    method,
    headers: {
      'X-Telegram-Init-Data': initData,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === 'string' ? j.detail : 'Неверные параметры';
    } catch { /* тело не JSON */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return raw ? res.blob() : res.json();
}

async function loadImage(blob) {
  if (window.createImageBitmap) return createImageBitmap(blob);
  const img = new Image();
  img.src = URL.createObjectURL(blob);
  await img.decode();
  return img;
}

async function getSource(keepBg) {
  const key = keepBg ? 1 : 0;
  if (!state.sources[key]) {
    const [data, blob] = await Promise.all([
      api(`jobs/${state.jobId}/source?keep_bg=${key}`),
      api(`jobs/${state.jobId}/artwork.png?keep_bg=${key}`, { raw: true }),
    ]);
    state.sources[key] = { data, art: await loadImage(blob) };
  }
  return state.sources[key];
}

// ---------- Утилиты ----------

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const r1 = (v) => Math.round(v * 10) / 10;
const fmtMm = (mm) => (mm >= 100 ? String(Math.round(mm)) : String(r1(mm)).replace('.', ','));
const fmtRub = (v) => `${Math.round(v).toLocaleString('ru-RU')} ₽`;

function haptic() {
  try { tg?.HapticFeedback?.selectionChanged(); } catch { /* старые клиенты */ }
}

let toastTimer = null;
function toast(text, ms = 3500) {
  const el = $('toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// Ползунок размера логарифмический: 5 мм … 1370 мм (за краями просто упирается)
const sizeToRange = (mm) => clamp(Math.round((Math.log(mm / SLIDER_MIN) / Math.log(SLIDER_MAX / SLIDER_MIN)) * 1000), 0, 1000);
const rangeToSize = (v) => {
  const mm = SLIDER_MIN * (SLIDER_MAX / SLIDER_MIN) ** (v / 1000);
  return mm < 100 ? Math.round(mm * 2) / 2 : Math.round(mm);
};

function mode(p = state.params) {
  if (p.fit_a4_sheet) return 'a4';
  return p.nesting_qty > 1 ? 'roll' : 'single';
}

function currentSticker() {
  const src = state.sources[state.params.keep_background ? 1 : 0];
  if (!src) return null;
  const geo = itemGeometry(src.data.crop_w_px, src.data.crop_h_px, state.params);
  return { geo, polygons: src.data.polygons, art: src.art, params: state.params };
}

// ---------- Отрисовка ----------

let rafPending = false;
function redraw() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    const st = currentSticker();
    if (!st) return;
    const canvas = $('preview');
    if (state.view === 'a4') drawA4(canvas, st);
    else if (state.view === 'roll') drawRoll(canvas, st);
    else drawSticker(canvas, st);
    renderSummary(st);
  });
}

function renderSummary(st) {
  const { geo, params } = st;
  const fit = a4Fit(geo.cell_w_mm, geo.cell_h_mm);
  const q = state.quote?.pricing;
  const q25 = state.quote?.pricing_25;
  const parts = [`<span>Наклейка <b>${fmtMm(geo.cut_w_mm)} × ${fmtMm(geo.cut_h_mm)} мм</b></span>`];
  const m = mode();
  if (m === 'a4') {
    parts.push(`<span>На А4: <b>${fit.fit} шт</b></span>`);
    if (q) parts.push(`<span class="price">лист ~${fmtRub(q.a4_retail_rub)} · себест. ${fmtRub(q.a4_cost_rub)}</span>`);
  } else if (m === 'roll') {
    const lay = rollLayout(geo.cell_w_mm, geo.cell_h_mm, params.nesting_qty, params.roll_width_mm);
    parts.push(`<span><b>${params.nesting_qty} шт</b>, ${lay.cols} в ряд, ${(lay.length / 1000).toFixed(2).replace('.', ',')} м</span>`);
    if (q) parts.push(`<span class="price">~${fmtRub(q.retail_rub)} (${String(q.unit_rub).replace('.', ',')} ₽/шт) · себест. ${fmtRub(q.cost_rub)}</span>`);
  } else {
    parts.push(`<span>На А4 войдёт: <b>${fit.fit} шт</b></span>`);
    if (q && q25) parts.push(`<span class="price">25 шт ~${fmtRub(q25.retail_rub)} · лист А4 ~${fmtRub(q.a4_retail_rub)}</span>`);
  }
  $('summary').innerHTML = parts.join('');
  updateMainButton(fit.fit);
}

// ---------- Цена (сервер, с задержкой) ----------

let quoteTimer = null;
let quoteSeq = 0;
function scheduleQuote() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(async () => {
    const seq = ++quoteSeq;
    try {
      const q = await api(`jobs/${state.jobId}/quote`, { method: 'POST', body: state.params });
      if (seq === quoteSeq) {
        state.quote = q;
        redraw();
      }
    } catch (e) {
      if (seq === quoteSeq) console.warn('quote', e);
    }
  }, 180);
}

// ---------- Элементы управления ----------

function chips(container, values, labelFn, onPick) {
  container.innerHTML = '';
  for (const v of values) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.dataset.val = String(v);
    b.textContent = labelFn(v);
    b.addEventListener('click', () => { haptic(); onPick(v); });
    container.appendChild(b);
  }
}

function markChips(container, isActive) {
  for (const b of container.querySelectorAll('.chip')) b.classList.toggle('active', isActive(Number(b.dataset.val)));
}

function seg(container, onPick) {
  for (const b of container.querySelectorAll('button')) {
    b.addEventListener('click', () => { haptic(); onPick(b.dataset.val); });
  }
}

function markSeg(container, val) {
  for (const b of container.querySelectorAll('button')) b.classList.toggle('active', b.dataset.val === String(val));
}

function setParams(patch, { reloadSource = false } = {}) {
  Object.assign(state.params, patch);
  syncControls();
  scheduleQuote();
  if (reloadSource) {
    $('loading').hidden = false;
    getSource(state.params.keep_background)
      .then(() => { $('loading').hidden = true; redraw(); })
      .catch((e) => { $('loading').hidden = true; toast(e.message); });
  } else {
    redraw();
  }
}

function syncControls() {
  const p = state.params;
  const st = currentSticker();
  $('size-range').value = String(sizeToRange(p.size_mm));
  $('size-label').textContent = `${fmtMm(p.size_mm / 10)} см`;
  markChips($('size-chips'), (v) => Math.abs(p.size_mm - v) < 0.6 && Math.abs(p.stretch_x - p.stretch_y) < 1e-3);
  if (st) {
    if (document.activeElement !== $('w-input')) $('w-input').value = String(r1(st.geo.art_w_mm));
    if (document.activeElement !== $('h-input')) $('h-input').value = String(r1(st.geo.art_h_mm));
    $('cut-size').textContent = `с кантом по контуру реза ${fmtMm(st.geo.cut_w_mm)} × ${fmtMm(st.geo.cut_h_mm)} мм`;
  }
  $('lock-btn').textContent = state.lock ? '🔒' : '🔓';
  $('reset-stretch').hidden = Math.abs(p.stretch_x - p.stretch_y) < 1e-3 && Math.abs(p.stretch_x - 1) < 1e-3;

  $('offset-range').value = String(p.offset_mm);
  $('offset-label').textContent = p.offset_mm > 0 ? `${String(p.offset_mm).replace('.', ',')} мм` : 'без канта';
  markChips($('offset-chips'), (v) => Math.abs(p.offset_mm - v) < 0.05);

  markSeg($('bg-seg'), p.keep_background ? 1 : 0);
  markSeg($('perf-seg'), p.perf_cut_mode);
  $('passes-box').hidden = p.perf_cut_mode === 'none' && !p.fit_a4_sheet;
  markSeg($('passes-seg'), Math.min(3, p.perf_cut_passes));

  const m = mode();
  markSeg($('mode-seg'), m);
  $('roll-box').hidden = m !== 'roll';
  if (document.activeElement !== $('qty-input')) $('qty-input').value = String(p.nesting_qty);
  markChips($('qty-chips'), (v) => p.nesting_qty === v);
  markSeg($('roll-seg'), p.roll_width_mm);
}

function setView(view) {
  state.view = view;
  for (const t of document.querySelectorAll('.tab')) t.classList.toggle('active', t.dataset.view === view);
  redraw();
}

// Ширина/высота рисунка в мм. С замком меняется размер, без замка — растяжение одной оси.
function applyWH(axis, valueMm) {
  const st = currentSticker();
  if (!st || !(valueMm > 0) || !Number.isFinite(valueMm)) return;
  const p = state.params;
  const cur = axis === 'w' ? st.geo.art_w_mm : st.geo.art_h_mm;
  const ratio = valueMm / cur;
  if (state.lock) {
    const [lo, hi] = limit('size_mm', [1, 100000]);
    setParams({ size_mm: clamp(p.size_mm * ratio, lo, hi) });
  } else {
    const [lo, hi] = limit('stretch', [0.05, 100]);
    const key = axis === 'w' ? 'stretch_x' : 'stretch_y';
    setParams({ [key]: clamp(p[key] * ratio, lo, hi) });
  }
}

function bindControls() {
  chips($('size-chips'), SIZE_CHIPS_MM, (v) => `${fmtMm(v / 10)} см`, (v) => setParams({ size_mm: v, stretch_x: 1, stretch_y: 1 }));
  $('size-range').addEventListener('input', (e) => setParams({ size_mm: rangeToSize(Number(e.target.value)) }));
  for (const [id, axis] of [['w-input', 'w'], ['h-input', 'h']]) {
    $(id).addEventListener('change', (e) => applyWH(axis, Number(String(e.target.value).replace(',', '.'))));
    $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
  }
  $('lock-btn').addEventListener('click', () => { haptic(); state.lock = !state.lock; syncControls(); });
  $('reset-stretch').addEventListener('click', () => setParams({ stretch_x: 1, stretch_y: 1 }));

  chips($('offset-chips'), OFFSET_CHIPS, (v) => (v ? `${String(v).replace('.', ',')} мм` : 'без канта'), (v) => setParams({ offset_mm: v }));
  $('offset-range').addEventListener('input', (e) => setParams({ offset_mm: Number(e.target.value) }));

  seg($('bg-seg'), (v) => setParams({ keep_background: v === '1' }, { reloadSource: true }));
  seg($('perf-seg'), (v) => setParams({ perf_cut_mode: v }));
  seg($('passes-seg'), (v) => setParams({ perf_cut_passes: Number(v) }));
  seg($('mode-seg'), (v) => {
    if (v === 'a4') { setParams({ fit_a4_sheet: true, nesting_qty: 1 }); setView('a4'); }
    else if (v === 'roll') {
      setParams({ fit_a4_sheet: false, nesting_qty: state.params.nesting_qty > 1 ? state.params.nesting_qty : 25 });
      setView('roll');
    } else { setParams({ fit_a4_sheet: false, nesting_qty: 1 }); setView('sticker'); }
  });
  $('qty-input').addEventListener('change', (e) => setParams({ nesting_qty: clamp(Math.round(Number(e.target.value) || 2), 2, 500) }));
  chips($('qty-chips'), QTY_CHIPS, (v) => `${v} шт`, (v) => setParams({ nesting_qty: v }));
  const rollSeg = $('roll-seg');
  for (const wmm of ROLL_WIDTHS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.val = String(wmm);
    b.textContent = `${(wmm / 1000).toFixed(2).replace('.', ',')} м`;
    rollSeg.appendChild(b);
  }
  seg(rollSeg, (v) => setParams({ roll_width_mm: Number(v) }));

  for (const t of document.querySelectorAll('.tab')) t.addEventListener('click', () => { haptic(); setView(t.dataset.view); });
  $('render-btn').addEventListener('click', render);
  $('check-btn').addEventListener('click', check);
  $('modal-close').addEventListener('click', () => { $('modal').hidden = true; });
  window.addEventListener('resize', redraw);
}

// ---------- Действия ----------

function setBusy(busy, text) {
  state.busy = busy;
  $('render-btn').disabled = busy;
  $('check-btn').disabled = busy;
  $('render-btn').textContent = busy && text ? text : 'Получить EPS';
  if (tg?.MainButton) {
    if (busy) tg.MainButton.showProgress(false);
    else tg.MainButton.hideProgress();
  }
}

function updateMainButton(a4fit) {
  if (!tg?.MainButton || state.busy) return;
  const m = mode();
  const extra = m === 'a4' ? ` · ${a4fit} шт на А4` : m === 'roll' ? ` · ${state.params.nesting_qty} шт` : '';
  tg.MainButton.setText(`Получить EPS${extra}`);
}

async function render() {
  if (state.busy) return;
  setBusy(true, 'Делаю EPS…');
  try {
    await api(`jobs/${state.jobId}/render`, { method: 'POST', body: state.params });
    try { tg?.HapticFeedback?.notificationOccurred('success'); } catch { /* старые клиенты */ }
    toast('Готово! Превью и EPS уже в чате с ботом.', 5000);
    if (tg?.showConfirm) {
      tg.showConfirm('EPS отправлен в чат. Закрыть редактор?', (ok) => { if (ok) tg.close(); });
    }
  } catch (e) {
    try { tg?.HapticFeedback?.notificationOccurred('error'); } catch { /* старые клиенты */ }
    toast(e.message, 6000);
  } finally {
    setBusy(false);
    redraw();
  }
}

async function check() {
  if (state.busy) return;
  setBusy(true);
  $('check-btn').textContent = 'Строю настоящий контур…';
  try {
    const blob = await api(`jobs/${state.jobId}/check`, { method: 'POST', body: state.params, raw: true });
    const img = $('modal-img');
    if (img.src) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
    $('modal').hidden = false;
  } catch (e) {
    toast(e.message, 6000);
  } finally {
    setBusy(false);
    $('check-btn').textContent = 'Точная проверка контура';
  }
}

// ---------- Экраны ----------

function show(id) {
  for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== id;
}

function showError(text) {
  $('error-text').textContent = text;
  show('view-error');
  tg?.MainButton?.hide();
}

async function openEditor(jobId) {
  state.jobId = jobId;
  state.sources = {};
  show('view-editor');
  $('loading').hidden = false;
  const job = await api(`jobs/${jobId}`);
  state.name = job.name;
  state.params = job.params;
  state.quote = job.quote;
  const key = job.params.keep_background ? 1 : 0;
  const blob = await api(`jobs/${jobId}/artwork.png?keep_bg=${key}`, { raw: true });
  state.sources[key] = { data: job.source, art: await loadImage(blob) };
  $('loading').hidden = true;
  syncControls();
  setView(mode() === 'a4' ? 'a4' : mode() === 'roll' ? 'roll' : 'sticker');
  if (tg?.MainButton) {
    tg.MainButton.setParams({ is_visible: true });
    tg.MainButton.offClick(render);
    tg.MainButton.onClick(render);
    $('render-btn').hidden = true;
  }
  if (tg?.BackButton && state.cameFromList) {
    tg.BackButton.show();
    tg.BackButton.offClick(backToList);
    tg.BackButton.onClick(backToList);
  }
}

function backToList() {
  tg?.BackButton?.hide();
  tg?.MainButton?.hide();
  openList().catch((e) => showError(e.message));
}

async function openList() {
  show('view-list');
  const box = $('job-list');
  box.innerHTML = '<p class="hint">Загружаю…</p>';
  const { jobs } = await api('jobs');
  box.innerHTML = '';
  if (!jobs.length) {
    box.innerHTML = '<p class="hint">Пока пусто.</p>';
    return;
  }
  for (const j of jobs) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'job';
    const size = j.width_mm ? `${fmtMm(j.width_mm)} × ${fmtMm(j.height_mm)} мм` : 'ещё не конвертирован';
    const date = j.updated_at ? new Date(j.updated_at * 1000).toLocaleDateString('ru-RU') : '';
    b.innerHTML = `<div class="thumb"></div><div class="name"></div><div class="meta"></div>`;
    b.querySelector('.name').textContent = j.name;
    b.querySelector('.meta').textContent = [size, date].filter(Boolean).join(' · ');
    b.addEventListener('click', () => {
      state.cameFromList = true;
      openEditor(j.job_id).catch((e) => showError(e.message));
    });
    box.appendChild(b);
    if (j.has_preview) {
      api(`jobs/${j.job_id}/preview.png`, { raw: true })
        .then((blob) => { b.querySelector('.thumb').style.backgroundImage = `url(${URL.createObjectURL(blob)})`; })
        .catch(() => {});
    }
  }
}

function startJobId() {
  const q = new URLSearchParams(location.search).get('job');
  if (q) return q;
  const sp = tg?.initDataUnsafe?.start_param || '';
  return sp.startsWith('job_') ? sp.slice(4) : '';
}

async function main() {
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.disableVerticalSwipes?.(); } catch { /* старые клиенты */ }
  }
  bindControls();
  if (!initData) {
    showError('Откройте редактор кнопкой в чате с ботом.');
    return;
  }
  try {
    const jobId = startJobId();
    if (jobId) await openEditor(jobId);
    else await openList();
  } catch (e) {
    showError(e.message);
  }
}

main();
