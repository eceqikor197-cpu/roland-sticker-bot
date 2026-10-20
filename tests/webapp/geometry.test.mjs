// JS-геометрия мини-апки совпадает с Python (tests/golden/miniapp_layout.json).
// Запуск: node --test tests/webapp/geometry.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { a4Fit, a4Sheet, itemGeometry, rollLayout } from '../../miniapp/static/geometry.js';

const golden = JSON.parse(readFileSync(new URL('../golden/miniapp_layout.json', import.meta.url), 'utf8'));

for (const [i, c] of golden.cases.entries()) {
  test(`case ${i}: ${c.crop_w_px}x${c.crop_h_px} size ${c.params.size_mm}`, () => {
    const geo = itemGeometry(c.crop_w_px, c.crop_h_px, c.params);
    for (const [k, v] of Object.entries(c.geometry)) {
      assert.ok(Math.abs(geo[k] - v) < 1e-6, `${k}: js ${geo[k]} != py ${v}`);
    }
    const fit = a4Fit(geo.cell_w_mm, geo.cell_h_mm);
    assert.deepEqual(fit, c.a4);
    for (const r of c.roll) {
      const lay = rollLayout(geo.cell_w_mm, geo.cell_h_mm, r.qty, r.roll_width_mm);
      assert.equal(lay.cols, r.cols);
      assert.equal(lay.rows, r.rows);
      assert.ok(Math.abs(lay.length - r.length) < 1e-6);
    }
  });
}

test('лист А4 с поворотом альбомный и не шире формата', () => {
  const s = a4Sheet(120.685, 54.932);
  assert.equal(s.sheetW, 297);
  assert.equal(s.sheetH, 210);
  assert.equal(s.fit, 6);
  for (const cell of s.cells) {
    assert.ok(cell.x >= 0 && cell.x + 120.685 <= 297);
    assert.ok(cell.y >= 0 && cell.y + 54.932 <= 210);
  }
});

test('наклейка крупнее А4 не раскладывается', () => {
  const s = a4Sheet(400, 300);
  assert.equal(s.fits, false);
  assert.equal(s.fit, 0);
});
