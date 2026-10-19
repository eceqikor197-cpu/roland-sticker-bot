// Sticker Studio — редактор раскладки стикеров.
// Все координаты раскладки хранятся в миллиметрах относительно левого верхнего угла поверхности,
// поэтому стикер 50 мм на экране всегда пропорционален реальному MacBook.

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.initData) {
    tg.ready();
    tg.expand();
    document.documentElement.dataset.theme = tg.colorScheme === "light" ? "light" : "dark";
  }

  const DEFAULT_STICKER_MM = 60;   // стартовая длинная сторона нового стикера
  const MIN_STICKER_MM = 10;
  const STAGE_PAD_PX = 24;
  const PAGE_SIZE = 60;
  const DRAFT_KEY = "sticker-studio-draft-v1";

  const $ = (id) => document.getElementById(id);
  const state = {
    surfaces: null,
    deviceId: null,
    zoneId: "lid",
    colorId: null,
    keepLogo: false,
    // Раскладка по каждой зоне: { "macbook_air_13:lid": [ {uid, sid, url, x, y, w, h, rot, flip}, ... ] }
    layouts: {},
    layoutId: null,
    history: [],
    future: [],
    selectedUid: null,
    catalog: { c1: null, c2: null, q: "", offset: 0, items: [] },
  };
  const stickerMeta = new Map(); // sid -> данные из каталога
  const imageCache = new Map();  // url -> Promise<HTMLImageElement>
  let uidCounter = 1;

  // ==========================================
  // УТИЛИТЫ
  // ==========================================

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  }

  function loadImage(url) {
    if (!imageCache.has(url)) {
      imageCache.set(url, new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = url;
      }));
    }
    return imageCache.get(url);
  }

  function device() { return state.surfaces.devices.find((d) => d.id === state.deviceId); }
  function zone() { return device().zones.find((z) => z.id === state.zoneId); }
  function layoutKey() { return `${state.deviceId}:${state.zoneId}`; }
  function items() { return (state.layouts[layoutKey()] ||= []); }
  function snapshot() { return JSON.stringify(state.layouts); }

  function commit() {
    state.history.push(state._last);
    if (state.history.length > 100) state.history.shift();
    state.future = [];
    state._last = snapshot();
    saveDraft();
    updateButtons();
  }

  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(serialize()));
    } catch (e) { /* приватный режим — просто не сохраняем черновик */ }
  }

  function serialize() {
    return {
      version: 1,
      device: state.deviceId,
      zone: state.zoneId,
      color: state.colorId,
      keepLogo: state.keepLogo,
      layouts: state.layouts,
    };
  }

  function restore(data) {
    if (!data || data.version !== 1) return false;
    if (!state.surfaces.devices.some((d) => d.id === data.device)) return false;
    state.deviceId = data.device;
    state.zoneId = data.zone || "lid";
    state.colorId = data.color || device().colors[0];
    state.keepLogo = !!data.keepLogo;
    state.layouts = data.layouts || {};
    for (const list of Object.values(state.layouts)) {
      for (const it of list) uidCounter = Math.max(uidCounter, (it.uid || 0) + 1);
    }
    return true;
  }

  // ==========================================
  // СЦЕНА KONVA
  // ==========================================

  const container = $("stage");
  const stage = new Konva.Stage({ container, width: container.clientWidth, height: container.clientHeight });
  const layer = new Konva.Layer();
  stage.add(layer);

  // world — группа в миллиметрах, масштабируется под экран
  const world = new Konva.Group();
  const bgGroup = new Konva.Group({ listening: false });
  const stickersGroup = new Konva.Group();
  const overlayGroup = new Konva.Group({ listening: false });
  world.add(bgGroup, stickersGroup, overlayGroup);
  layer.add(world);

  const transformer = new Konva.Transformer({
    rotateEnabled: true,
    keepRatio: true,
    enabledAnchors: ["top-left", "top-right", "bottom-left", "bottom-right"],
    anchorSize: 14,
    anchorCornerRadius: 7,
    borderStroke: "#00aeef",
    anchorStroke: "#00aeef",
    rotationSnaps: [0, 45, 90, 135, 180, 225, 270, 315],
    rotationSnapTolerance: 4,
    boundBoxFunc: (oldBox, newBox) => {
      const minPx = MIN_STICKER_MM * world.scaleX();
      return Math.min(Math.abs(newBox.width), Math.abs(newBox.height)) < minPx ? oldBox : newBox;
    },
  });
  layer.add(transformer);

  function fitWorld() {
    if (!state.surfaces) return;
    const z = zone();
    const w = container.clientWidth;
    const h = container.clientHeight;
    stage.size({ width: w, height: h });
    const scale = Math.min((w - STAGE_PAD_PX * 2) / z.width, (h - STAGE_PAD_PX * 2) / z.height);
    world.scale({ x: scale, y: scale });
    world.position({ x: (w - z.width * scale) / 2, y: (h - z.height * scale) / 2 });
    layer.batchDraw();
  }

  function shade(hex, amount) {
    const n = parseInt(hex.slice(1), 16);
    const ch = (s) => Math.max(0, Math.min(255, ((n >> s) & 255) + amount));
    return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
  }

  function isDark(hex) {
    const n = parseInt(hex.slice(1), 16);
    return ((n >> 16) & 255) * 0.299 + ((n >> 8) & 255) * 0.587 + (n & 255) * 0.114 < 128;
  }

  // Силуэт яблока в квадрате 24×24 (центр примерно 12,12)
  const APPLE_PATH = "M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701";

  function drawSurface() {
    const z = zone();
    const fill = state.surfaces.colors[state.colorId].fill;
    const dark = isDark(fill);
    bgGroup.destroyChildren();
    overlayGroup.destroyChildren();

    // Алюминиевый корпус: мягкий градиент, тень на столе, фаска по краю
    bgGroup.add(new Konva.Rect({
      width: z.width, height: z.height, cornerRadius: z.radius,
      fillLinearGradientStartPoint: { x: 0, y: 0 },
      fillLinearGradientEndPoint: { x: z.width * 0.6, y: z.height },
      fillLinearGradientColorStops: [0, shade(fill, dark ? 10 : 8), 0.55, fill, 1, shade(fill, dark ? -8 : -14)],
      shadowColor: "#000", shadowBlur: 22, shadowOpacity: 0.45, shadowOffset: { x: 0, y: 6 },
    }));
    // Блик анодированного алюминия
    bgGroup.add(new Konva.Rect({
      width: z.width, height: z.height, cornerRadius: z.radius,
      fillLinearGradientStartPoint: { x: 0, y: 0 },
      fillLinearGradientEndPoint: { x: z.width, y: z.height * 0.4 },
      fillLinearGradientColorStops: [0, "rgba(255,255,255,0)", 0.35, `rgba(255,255,255,${dark ? 0.05 : 0.22})`, 0.5, "rgba(255,255,255,0)"],
    }));
    bgGroup.add(new Konva.Rect({
      width: z.width, height: z.height, cornerRadius: z.radius,
      stroke: shade(fill, dark ? -18 : -40), strokeWidth: 0.5,
    }));
    bgGroup.add(new Konva.Rect({
      x: 0.7, y: 0.7, width: z.width - 1.4, height: z.height - 1.4, cornerRadius: Math.max(0, z.radius - 0.7),
      stroke: `rgba(255,255,255,${dark ? 0.12 : 0.6})`, strokeWidth: 0.35,
    }));

    if (z.logo) drawLogo(z.logo, fill, dark);
    if (z.id === "deck") drawDeckEdge(z, fill, dark);

    // Клавиатура, тачпад и динамики рисуем поверх стикеров: на них клеить нельзя
    for (const f of forbiddenZones()) {
      if (f.isLogo) {
        overlayGroup.add(new Konva.Ellipse({
          x: f.cx, y: f.cy, radiusX: f.rx + 2, radiusY: f.ry + 2,
          stroke: "#00aeef", strokeWidth: 0.6, dash: [2, 1.5],
        }));
      } else if (f.name === "Клавиатура") drawKeyboard(f, fill, dark);
      else if (f.name === "Тачпад") drawTrackpad(f, fill, dark);
      else if (f.name === "Динамик") drawGrille(f, fill, dark);
    }

    // Обрезка стикеров по краю корпуса
    stickersGroup.clipFunc((ctx) => {
      roundRectPath(ctx, 0, 0, z.width, z.height, z.radius);
    });
    fitWorld();
  }

  // Полированный логотип: зеркальный отблеск на светлых корпусах, тёмный глянец на тёмных
  function drawLogo(logo, fill, dark) {
    const s = (logo.ry * 2) / 24;
    bgGroup.add(new Konva.Path({
      data: APPLE_PATH,
      x: logo.cx - 12 * s, y: logo.cy - 12 * s, scaleX: s, scaleY: s,
      fillLinearGradientStartPoint: { x: 2, y: 0 },
      fillLinearGradientEndPoint: { x: 22, y: 24 },
      fillLinearGradientColorStops: dark
        ? [0, shade(fill, 34), 0.5, shade(fill, 10), 1, shade(fill, 28)]
        : [0, "#ffffff", 0.45, shade(fill, -30), 0.55, shade(fill, -6), 1, "#fbfbfb"],
      shadowColor: dark ? "#000" : "#fff", shadowBlur: 0.6, shadowOpacity: 0.5, shadowOffset: { x: 0, y: 0.15 },
    }));
  }

  // Выемка для открытия крышки у переднего края
  function drawDeckEdge(z, fill, dark) {
    bgGroup.add(new Konva.Rect({
      x: z.width / 2 - 22, y: z.height - 2.4, width: 44, height: 2.4, cornerRadius: [2.4, 2.4, 0, 0],
      fill: shade(fill, dark ? -14 : -22), opacity: 0.7,
    }));
  }

  // Раскладка клавиатуры MacBook: ширина клавиш в долях, строки нормируются по ширине блока
  const KEY_ROWS = [
    { h: 0.82, keys: [["esc", 1.5], ...Array.from({ length: 12 }, () => ["", 1]), ["touchid", 1]] },
    { keys: [..."`1234567890-=".split("").map((k) => [k, 1]), ["delete", 1.5]] },
    { keys: [["tab", 1.5], ..."QWERTYUIOP[]\\".split("").map((k) => [k, 1])] },
    { keys: [["caps lock", 1.8], ..."ASDFGHJKL;'".split("").map((k) => [k, 1]), ["return", 1.7]] },
    { keys: [["shift", 2.3], ..."ZXCVBNM,./".split("").map((k) => [k, 1]), ["shift", 2.2]] },
    { keys: [["fn", 1], ["control", 1], ["option", 1], ["command", 1.25], ["", 5], ["command", 1.25], ["option", 1], ["arrows", 3]] },
  ];

  function drawKeyboard(f, fill, dark) {
    // Колодец клавиатуры чуть темнее корпуса
    overlayGroup.add(new Konva.Rect({
      x: f.x, y: f.y, width: f.w, height: f.h, cornerRadius: f.r || 3,
      fill: shade(fill, dark ? -10 : -26),
      stroke: shade(fill, dark ? -20 : -45), strokeWidth: 0.3,
    }));
    const pad = 1.2;
    const gap = 1.1;
    const units = KEY_ROWS.reduce((sum, r) => sum + (r.h || 1), 0);
    const unitH = (f.h - pad * 2 - gap * (KEY_ROWS.length - 1)) / units;
    const keyFill = "#1b1b1d";
    const textFill = "#d8d8dc";
    let y = f.y + pad;
    for (const row of KEY_ROWS) {
      const rh = unitH * (row.h || 1);
      const total = row.keys.reduce((sum, [, u]) => sum + u, 0);
      const unitW = (f.w - pad * 2 - gap * (row.keys.length - 1)) / total;
      let x = f.x + pad;
      for (const [label, u] of row.keys) {
        const w = unitW * u;
        if (label === "arrows") {
          drawArrows(x, y, w, rh, keyFill, textFill);
        } else {
          overlayGroup.add(new Konva.Rect({
            x, y, width: w, height: rh, cornerRadius: 1.1,
            fill: keyFill, shadowColor: "#000", shadowBlur: 0.4, shadowOpacity: 0.5, shadowOffset: { x: 0, y: 0.25 },
          }));
          if (label === "touchid") {
            overlayGroup.add(new Konva.Circle({ x: x + w / 2, y: y + rh / 2, radius: Math.min(w, rh) * 0.36, stroke: "#4a4a4f", strokeWidth: 0.3 }));
          } else if (label) {
            const big = label.length === 1;
            overlayGroup.add(new Konva.Text({
              x: big ? x : x + 1.2, y: big ? y : y + rh - 3.4, width: big ? w : w - 2, height: big ? rh : 3,
              text: label, fontSize: big ? Math.min(3.4, rh * 0.32) : 1.7,
              fontFamily: "-apple-system, 'Helvetica Neue', Arial, sans-serif",
              fill: textFill, align: big ? "center" : "left", verticalAlign: big ? "middle" : "top",
            }));
          }
        }
        x += w + gap;
      }
      y += rh + gap;
    }
  }

  // Стрелки в виде перевёрнутой «Т»
  function drawArrows(x, y, w, h, keyFill, textFill) {
    const gap = 0.6;
    const kw = (w - gap * 2) / 3;
    const kh = (h - gap) / 2;
    const keys = [
      [x, y + kh + gap, "◀"], [x + kw + gap, y, "▲"], [x + kw + gap, y + kh + gap, "▼"], [x + (kw + gap) * 2, y + kh + gap, "▶"],
    ];
    for (const [kx, ky, t] of keys) {
      overlayGroup.add(new Konva.Rect({ x: kx, y: ky, width: kw, height: kh, cornerRadius: 0.8, fill: keyFill }));
      overlayGroup.add(new Konva.Text({
        x: kx, y: ky, width: kw, height: kh, text: t, fontSize: 1.6, fill: textFill, align: "center", verticalAlign: "middle",
      }));
    }
  }

  function drawTrackpad(f, fill, dark) {
    overlayGroup.add(new Konva.Rect({
      x: f.x, y: f.y, width: f.w, height: f.h, cornerRadius: f.r || 6,
      fillLinearGradientStartPoint: { x: f.x, y: f.y },
      fillLinearGradientEndPoint: { x: f.x + f.w, y: f.y + f.h },
      fillLinearGradientColorStops: [0, shade(fill, dark ? 6 : 6), 1, shade(fill, dark ? -4 : -10)],
      stroke: shade(fill, dark ? -16 : -38), strokeWidth: 0.35,
    }));
    overlayGroup.add(new Konva.Rect({
      x: f.x + 0.5, y: f.y + 0.5, width: f.w - 1, height: f.h - 1, cornerRadius: Math.max(0, (f.r || 6) - 0.5),
      stroke: `rgba(255,255,255,${dark ? 0.08 : 0.5})`, strokeWidth: 0.25,
    }));
  }

  // Решётка динамика: одна фигура вместо сотен кружков
  function drawGrille(f, fill, dark) {
    const color = shade(fill, dark ? -22 : -70);
    overlayGroup.add(new Konva.Shape({
      sceneFunc: (ctx) => {
        ctx.fillStyle = color;
        for (let y = f.y + 1.5; y <= f.y + f.h - 1.5; y += 1.6) {
          for (let x = f.x + 1.5; x <= f.x + f.w - 1.5; x += 1.6) {
            ctx.beginPath();
            ctx.arc(x, y, 0.35, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      },
    }));
  }

  function roundRectPath(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function forbiddenZones() {
    const z = zone();
    const list = [...(z.forbidden || [])];
    if (z.logo && state.keepLogo) list.push({ isLogo: true, name: "Логотип", ...z.logo });
    return list;
  }

  // ==========================================
  // СТИКЕРЫ НА ХОЛСТЕ
  // ==========================================

  function nodeFor(uid) { return stickersGroup.findOne(`#s${uid}`); }

  async function renderItems() {
    transformer.nodes([]);
    stickersGroup.destroyChildren();
    const list = items();
    const imgs = await Promise.all(list.map((it) => loadImage(it.url).catch(() => null)));
    // Пока грузились картинки, пользователь мог переключить зону
    if (list !== items()) return;
    stickersGroup.destroyChildren();
    list.forEach((it, i) => { if (imgs[i]) stickersGroup.add(makeNode(it, imgs[i])); });
    selectUid(list.some((it) => it.uid === state.selectedUid) ? state.selectedUid : null);
    checkWarnings();
    layer.batchDraw();
  }

  function makeNode(it, img) {
    const node = new Konva.Image({
      id: `s${it.uid}`,
      image: img,
      x: it.x, y: it.y,
      width: it.w, height: it.h,
      offsetX: it.w / 2, offsetY: it.h / 2,
      rotation: it.rot,
      scaleX: it.flip ? -1 : 1,
      draggable: true,
      shadowColor: "#000", shadowBlur: 2.5, shadowOpacity: 0.35, shadowOffset: { x: 0.4, y: 0.8 },
    });
    node.on("mousedown touchstart", () => selectUid(it.uid));
    node.on("dragmove", checkWarnings);
    node.on("dragend transformend", () => syncFromNode(it.uid));
    return node;
  }

  function syncFromNode(uid) {
    const it = items().find((i) => i.uid === uid);
    const node = nodeFor(uid);
    if (!it || !node) return;
    // Трансформер меняет scale — переводим его обратно в ширину/высоту в мм
    const sx = Math.abs(node.scaleX());
    const sy = Math.abs(node.scaleY());
    it.w = node.width() * sx;
    it.h = node.height() * sy;
    it.x = node.x();
    it.y = node.y();
    it.rot = node.rotation();
    node.setAttrs({
      width: it.w, height: it.h, offsetX: it.w / 2, offsetY: it.h / 2,
      scaleX: it.flip ? -1 : 1, scaleY: 1,
    });
    commit();
    checkWarnings();
    updateSizeLabel();
  }

  async function addSticker(meta, xMm, yMm, wMm, rot) {
    const z = zone();
    const aspect = meta.height_px && meta.width_px
      ? meta.height_px / meta.width_px
      : meta.height_mm / meta.width_mm;
    let w = wMm || Math.min(meta.width_mm, DEFAULT_STICKER_MM);
    let h = w * aspect;
    if (!wMm && h > DEFAULT_STICKER_MM) { h = DEFAULT_STICKER_MM; w = h / aspect; }
    // Без точки броска ставим в центр, а если там клавиатура — в первое свободное место
    let x = xMm ?? z.width / 2;
    let y = yMm ?? z.height / 2;
    const zones = forbiddenZones();
    if (xMm == null && hitsForbidden(x, y, w, h, 0, zones)) {
      const free = [];
      for (let fy = h / 2 + 4; fy <= z.height - h / 2 - 4; fy += 5) {
        for (let fx = w / 2 + 4; fx <= z.width - w / 2 - 4; fx += 5) {
          if (!hitsForbidden(fx, fy, w, h, 0, zones)) free.push([fx, fy]);
        }
      }
      if (free.length) {
        free.sort((a, b) => Math.hypot(a[0] - x, a[1] - y) - Math.hypot(b[0] - x, b[1] - y));
        [x, y] = free[0];
      }
    }
    const it = {
      uid: uidCounter++,
      sid: meta.id,
      url: meta.image_url,
      x, y,
      w, h,
      rot: rot || 0,
      flip: false,
    };
    items().push(it);
    commit();
    const img = await loadImage(it.url);
    stickersGroup.add(makeNode(it, img));
    selectUid(it.uid);
    checkWarnings();
    layer.batchDraw();
    return it;
  }

  function selectUid(uid) {
    state.selectedUid = uid;
    const node = uid ? nodeFor(uid) : null;
    transformer.nodes(node ? [node] : []);
    transformer.moveToTop();
    updateButtons();
    updateSizeLabel();
    layer.batchDraw();
  }

  function selectedItem() { return items().find((i) => i.uid === state.selectedUid); }

  function updateSizeLabel() {
    const it = selectedItem();
    $("size-label").textContent = it ? `${Math.round(it.w)} × ${Math.round(it.h)} мм` : "";
  }

  function updateButtons() {
    const has = !!selectedItem();
    for (const id of ["btn-front", "btn-back", "btn-flip", "btn-dup", "btn-del"]) $(id).disabled = !has;
    $("btn-undo").disabled = state.history.length === 0;
    $("btn-redo").disabled = state.future.length === 0;
  }

  // Подсвечиваем стикеры, которые заходят на клавиатуру/тачпад/логотип
  function checkWarnings() {
    const zones = forbiddenZones();
    const hits = new Set();
    for (const node of stickersGroup.children) {
      const box = node.getClientRect({ relativeTo: world, skipShadow: true });
      const bad = zones.some((f) => {
        const r = f.isLogo
          ? { x: f.cx - f.rx, y: f.cy - f.ry, w: f.rx * 2, h: f.ry * 2 }
          : f;
        // Небольшой допуск, чтобы край стикера мог касаться зоны
        const t = 1.5;
        const over = box.x + t < r.x + r.w && box.x + box.width - t > r.x
          && box.y + t < r.y + r.h && box.y + box.height - t > r.y;
        if (over) hits.add(f.name);
        return over;
      });
      node.stroke(bad ? "#ef4444" : null);
      node.strokeWidth(bad ? 0.8 : 0);
      node.dash(bad ? [2, 1.2] : null);
    }
    $("warn").textContent = hits.size
      ? `Стикеры заходят на: ${[...hits].join(", ").toLowerCase()}. Так их не наклеить.`
      : "";
  }

  // ==========================================
  // ДЕЙСТВИЯ ПАНЕЛИ ИНСТРУМЕНТОВ
  // ==========================================

  function undo() {
    if (!state.history.length) return;
    state.future.push(state._last);
    state._last = state.history.pop();
    state.layouts = JSON.parse(state._last);
    saveDraft();
    renderItems();
    updateButtons();
  }

  function redo() {
    if (!state.future.length) return;
    state.history.push(state._last);
    state._last = state.future.pop();
    state.layouts = JSON.parse(state._last);
    saveDraft();
    renderItems();
    updateButtons();
  }

  function moveZ(dir) {
    const list = items();
    const i = list.findIndex((it) => it.uid === state.selectedUid);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    const node = nodeFor(state.selectedUid);
    dir > 0 ? node.moveUp() : node.moveDown();
    commit();
    layer.batchDraw();
  }

  function flip() {
    const it = selectedItem();
    if (!it) return;
    it.flip = !it.flip;
    nodeFor(it.uid).scaleX(it.flip ? -1 : 1);
    commit();
    layer.batchDraw();
  }

  async function duplicate() {
    const it = selectedItem();
    if (!it) return;
    const copy = { ...it, uid: uidCounter++, x: it.x + 8, y: it.y + 8 };
    items().push(copy);
    commit();
    stickersGroup.add(makeNode(copy, await loadImage(copy.url)));
    selectUid(copy.uid);
    checkWarnings();
  }

  function removeSelected() {
    const list = items();
    const i = list.findIndex((it) => it.uid === state.selectedUid);
    if (i < 0) return;
    list.splice(i, 1);
    nodeFor(state.selectedUid).destroy();
    selectUid(null);
    commit();
    checkWarnings();
  }

  function clearZone() {
    if (!items().length) return;
    state.layouts[layoutKey()] = [];
    commit();
    renderItems();
  }

  // «Стикер-бомбинг»: раскидываем стикеры сеткой с разбросом, поворотами и наложениями
  async function bomb() {
    const z = zone();
    const pool = [];
    const seen = new Set();
    for (const it of items()) {
      if (!seen.has(it.sid) && stickerMeta.has(it.sid)) { seen.add(it.sid); pool.push(stickerMeta.get(it.sid)); }
    }
    for (const m of state.catalog.items) {
      if (pool.length >= 24) break;
      if (!seen.has(m.id)) { seen.add(m.id); pool.push(m); }
    }
    if (!pool.length) { toast("Сначала открой каталог стикеров"); return; }

    const cell = forbiddenZones().length ? 40 : 52;
    const cols = Math.ceil(z.width / cell);
    const rows = Math.ceil(z.height / cell);
    const zones = forbiddenZones();
    const inForbidden = (x, y) => zones.some((f) => f.isLogo
      ? ((x - f.cx) / f.rx) ** 2 + ((y - f.cy) / f.ry) ** 2 < 1.6
      : x > f.x - 4 && x < f.x + f.w + 4 && y > f.y - 4 && y < f.y + f.h + 4);

    const spots = [];
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = (c + 0.5) * (z.width / cols) + (Math.random() - 0.5) * cell * 0.5;
        const y = (r + 0.5) * (z.height / rows) + (Math.random() - 0.5) * cell * 0.5;
        if (!inForbidden(x, y)) spots.push([x, y]);
      }
    }
    // Шафлим, чтобы порядок наложения был случайным
    for (let i = spots.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [spots[i], spots[j]] = [spots[j], spots[i]];
    }
    const placed = [];
    spots.forEach(([x, y], i) => {
      const meta = pool[i % pool.length];
      const aspect = meta.height_px && meta.width_px ? meta.height_px / meta.width_px : meta.height_mm / meta.width_mm;
      const rot = (Math.random() - 0.5) * 50;
      let w = 42 + Math.random() * 30;
      if (w * aspect > 75) w = 75 / aspect;
      // Уменьшаем стикер, пока он не перестанет залезать на клавиатуру/тачпад; не влез — пропускаем
      for (let k = 0; k < 8 && hitsForbidden(x, y, w, w * aspect, rot, zones); k++) w *= 0.85;
      if (w < 16 || hitsForbidden(x, y, w, w * aspect, rot, zones)) return;
      placed.push({ uid: uidCounter++, sid: meta.id, url: meta.image_url, x, y, w, h: w * aspect, rot, flip: false });
    });
    state.layouts[layoutKey()] = placed;
    commit();
    await renderItems();
  }

  // Габариты повёрнутого стикера (AABB) против запретных зон
  function hitsForbidden(x, y, w, h, rot, zones) {
    const a = (rot * Math.PI) / 180;
    const hw = (Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a))) / 2;
    const hh = (Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a))) / 2;
    return zones.some((f) => {
      const r = f.isLogo ? { x: f.cx - f.rx, y: f.cy - f.ry, w: f.rx * 2, h: f.ry * 2 } : f;
      return x - hw < r.x + r.w && x + hw > r.x && y - hh < r.y + r.h && y + hh > r.y;
    });
  }

  async function saveLayout() {
    const btn = $("btn-save");
    btn.disabled = true;
    try {
      const body = { surface_id: state.deviceId, title: device().name, data: serialize() };
      const qs = state.layoutId ? `?layout_id=${encodeURIComponent(state.layoutId)}` : "";
      const res = await api(`/api/layouts${qs}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      state.layoutId = res.layout_id;
      const url = new URL(location.href);
      url.searchParams.set("layout", res.layout_id);
      history.replaceState(null, "", url);
      try { await navigator.clipboard.writeText(url.toString()); toast("Сохранено, ссылка скопирована"); }
      catch (e) { toast("Сохранено. Ссылка в адресной строке"); }
    } catch (e) {
      toast("Не удалось сохранить: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  function exportPng() {
    transformer.nodes([]);
    const z = zone();
    const s = world.scaleX();
    const pad = 12;
    const url = stage.toDataURL({
      x: world.x() - pad, y: world.y() - pad,
      width: z.width * s + pad * 2, height: z.height * s + pad * 2,
      pixelRatio: Math.max(2, 1800 / (z.width * s)),
    });
    selectUid(state.selectedUid);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sticker-studio-${state.deviceId}-${state.zoneId}.png`;
    a.click();
  }

  // ==========================================
  // ВЕРХНЯЯ ПАНЕЛЬ: УСТРОЙСТВО / ЗОНА / ЦВЕТ
  // ==========================================

  function renderTopbar() {
    const sel = $("device-select");
    sel.innerHTML = "";
    for (const d of state.surfaces.devices) sel.add(new Option(d.name, d.id, false, d.id === state.deviceId));

    const seg = $("zone-seg");
    seg.innerHTML = "";
    for (const z of device().zones) {
      const b = document.createElement("button");
      b.textContent = z.name;
      b.classList.toggle("active", z.id === state.zoneId);
      b.onclick = () => switchZone(z.id);
      seg.append(b);
    }

    const sw = $("color-swatches");
    sw.innerHTML = "";
    for (const cid of device().colors) {
      const c = state.surfaces.colors[cid];
      const b = document.createElement("button");
      b.className = "swatch";
      b.title = c.name;
      b.style.background = c.fill;
      b.classList.toggle("active", cid === state.colorId);
      b.onclick = () => { state.colorId = cid; saveDraft(); renderTopbar(); drawSurface(); checkWarnings(); };
      sw.append(b);
    }
    $("chk-logo").checked = state.keepLogo;
    $("chk-logo").parentElement.style.visibility = zone().logo ? "visible" : "hidden";
  }

  function switchZone(zoneId) {
    state.zoneId = zoneId;
    state.selectedUid = null;
    saveDraft();
    renderTopbar();
    drawSurface();
    renderItems();
  }

  // ==========================================
  // КАТАЛОГ
  // ==========================================

  async function loadCategories() {
    const cats = await api("/api/categories");
    const box = $("cats");
    box.innerHTML = "";
    const mk = (label, c1, c2) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.classList.toggle("active", state.catalog.c1 === c1 && state.catalog.c2 === c2);
      b.onclick = () => {
        state.catalog.c1 = c1;
        state.catalog.c2 = c2;
        box.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        loadStickers(true);
      };
      return b;
    };
    box.append(mk("Все", null, null));
    for (const c1 of cats) {
      for (const c2 of c1.children) box.append(mk(c2.name.replace(/^\d+_/, "").replace(/_/g, " "), c1.name, c2.name));
    }
  }

  async function loadStickers(reset) {
    const c = state.catalog;
    if (reset) { c.offset = 0; c.items = []; $("grid").innerHTML = ""; }
    const params = new URLSearchParams({ offset: c.offset, limit: PAGE_SIZE });
    if (c.c1) params.set("c1", c.c1);
    if (c.c2) params.set("c2", c.c2);
    if (c.q) params.set("q", c.q);
    let res;
    try {
      res = await api(`/api/stickers?${params}`);
    } catch (e) {
      $("empty").hidden = false;
      $("empty").textContent = "Каталог недоступен: " + e.message;
      return;
    }
    c.offset += res.items.length;
    c.items.push(...res.items);
    const grid = $("grid");
    for (const m of res.items) {
      stickerMeta.set(m.id, m);
      const tile = document.createElement("div");
      tile.className = "tile";
      tile.title = m.title;
      tile.draggable = true;
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = m.image_url;
      img.alt = m.title;
      tile.append(img);
      tile.onclick = () => addSticker(m);
      tile.ondragstart = (e) => e.dataTransfer.setData("text/sticker-id", m.id);
      grid.append(tile);
    }
    $("btn-more").hidden = !res.has_more;
    $("empty").hidden = c.items.length > 0;
    $("empty").textContent = "Ничего не нашлось";
  }

  // Перетаскивание плитки из каталога на холст (десктоп)
  container.addEventListener("dragover", (e) => e.preventDefault());
  container.addEventListener("drop", (e) => {
    e.preventDefault();
    const meta = stickerMeta.get(e.dataTransfer.getData("text/sticker-id"));
    if (!meta) return;
    stage.setPointersPositions(e);
    const p = world.getRelativeTransform().copy().invert().point(stage.getPointerPosition());
    addSticker(meta, p.x, p.y);
  });

  // ==========================================
  // СОБЫТИЯ
  // ==========================================

  stage.on("mousedown touchstart", (e) => {
    if (e.target === stage || e.target.getParent() === bgGroup) selectUid(null);
  });
  transformer.on("transform", () => { checkWarnings(); });

  $("device-select").onchange = (e) => {
    state.deviceId = e.target.value;
    if (!device().colors.includes(state.colorId)) state.colorId = device().colors[0];
    if (!device().zones.some((z) => z.id === state.zoneId)) state.zoneId = device().zones[0].id;
    switchZone(state.zoneId);
  };
  $("chk-logo").onchange = (e) => { state.keepLogo = e.target.checked; saveDraft(); drawSurface(); checkWarnings(); };
  $("btn-undo").onclick = undo;
  $("btn-redo").onclick = redo;
  $("btn-front").onclick = () => moveZ(1);
  $("btn-back").onclick = () => moveZ(-1);
  $("btn-flip").onclick = flip;
  $("btn-dup").onclick = duplicate;
  $("btn-del").onclick = removeSelected;
  $("btn-bomb").onclick = bomb;
  $("btn-clear").onclick = clearZone;
  $("btn-save").onclick = saveLayout;
  $("btn-png").onclick = exportPng;
  $("btn-more").onclick = () => loadStickers(false);
  $("catalog-toggle").onclick = () => { $("catalog").classList.toggle("collapsed"); setTimeout(fitWorld, 0); };

  let searchTimer;
  $("search").oninput = (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.catalog.q = e.target.value.trim(); loadStickers(true); }, 300);
  };

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); }
    else if (e.key === "Delete" || e.key === "Backspace") { if (selectedItem()) { e.preventDefault(); removeSelected(); } }
  });

  new ResizeObserver(fitWorld).observe(container);

  // ==========================================
  // СТАРТ
  // ==========================================

  async function init() {
    state.surfaces = await api("surfaces.json");
    const first = state.surfaces.devices[0];
    state.deviceId = first.id;
    state.colorId = first.colors[0];

    const sharedId = new URLSearchParams(location.search).get("layout");
    let restored = false;
    if (sharedId) {
      try {
        const saved = await api(`/api/layouts/${encodeURIComponent(sharedId)}`);
        restored = restore(saved.data);
        if (restored) state.layoutId = saved.layout_id;
      } catch (e) { toast("Раскладка по ссылке не найдена"); }
    }
    if (!restored) {
      try { restore(JSON.parse(localStorage.getItem(DRAFT_KEY))); } catch (e) { /* нет черновика */ }
    }
    state._last = snapshot();

    renderTopbar();
    drawSurface();
    await renderItems();
    updateButtons();

    try {
      await loadCategories();
    } catch (e) { /* без категорий каталог всё равно работает */ }
    await loadStickers(true);
    // Подтягиваем метаданные стикеров из восстановленной раскладки для «Заполнить»
    const missing = new Set();
    for (const list of Object.values(state.layouts)) for (const it of list) if (!stickerMeta.has(it.sid)) missing.add(it.sid);
    await Promise.all([...missing].map((sid) => api(`/api/stickers/${encodeURIComponent(sid)}`)
      .then((m) => stickerMeta.set(m.id, m)).catch(() => {})));
  }

  init().catch((e) => toast("Ошибка запуска: " + e.message));
})();
