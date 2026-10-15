/**
 * Тесты модуля геометрии на TypeScript по эталону tests/golden/geometry.json.
 * Совместимо с vitest и native node --test runner.
 * Допустимая погрешность: не более 0.01 мм.
 */

import { describe, it } from "vitest";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  mm_to_px,
  px_to_mm,
  rotate_point,
  get_sticker_polygon,
  get_rotated_bbox,
  is_sticker_inside_object,
} from "./index.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const goldenPath = path.resolve(__dirname, "../../../tests/golden/geometry.json");

const goldenData = JSON.parse(fs.readFileSync(goldenPath, "utf-8"));
const tol = goldenData.tolerance_mm;

function assertClose(actual: number, expected: number, tolerance: number, msg?: string) {
  const diff = Math.abs(actual - expected);
  assert.ok(
    diff <= tolerance,
    `${msg ?? "Values not close"}: actual ${actual}, expected ${expected}, diff ${diff} > ${tolerance}`
  );
}

describe("Golden Geometry Tests (TypeScript)", () => {
  it("validates golden file structure", () => {
    assert.equal(goldenData.version, "1.0");
    assert.ok(goldenData.tolerance_mm <= 0.01);
    assert.ok(goldenData.conversions.length >= 30);
    assert.ok(goldenData.rotations.length >= 10);
    assert.ok(goldenData.cases.length >= 30);
  });

  it("validates unit conversions (mm <-> px)", () => {
    for (const item of goldenData.conversions) {
      const actualPx = mm_to_px(item.val_mm, item.scale);
      assertClose(actualPx, item.expected_px, tol, `mm_to_px(${item.val_mm}, ${item.scale})`);

      if (item.scale > 0) {
        const actualMm = px_to_mm(actualPx, item.scale);
        assertClose(actualMm, item.expected_mm, tol, `px_to_mm(${actualPx}, ${item.scale})`);
      }
    }
  });

  it("throws on px_to_mm with zero scale", () => {
    assert.throws(() => px_to_mm(100, 0), /не может быть равен 0/);
  });

  it("validates point rotations", () => {
    for (const item of goldenData.rotations) {
      const [rx, ry] = rotate_point(
        item.x,
        item.y,
        item.cx,
        item.cy,
        item.angle_deg
      );
      assertClose(rx, item.expected_rx, tol, `rx for angle ${item.angle_deg}`);
      assertClose(ry, item.expected_ry, tol, `ry for angle ${item.angle_deg}`);
    }
  });

  it("validates 36+ complex sticker cases", () => {
    for (const c of goldenData.cases) {
      const { sticker, object: obj, expected, scale } = c;

      // 1. Polygon corners
      const poly = get_sticker_polygon(
        sticker.cx_mm,
        sticker.cy_mm,
        sticker.w_mm,
        sticker.h_mm,
        sticker.angle_deg
      );
      assert.equal(poly.length, 4);
      for (let i = 0; i < 4; i++) {
        assertClose(poly[i][0], expected.polygon_mm[i][0], tol, `${c.id} poly[${i}].x`);
        assertClose(poly[i][1], expected.polygon_mm[i][1], tol, `${c.id} poly[${i}].y`);
      }

      // 2. Bounding Box
      const bbox = get_rotated_bbox(
        sticker.cx_mm,
        sticker.cy_mm,
        sticker.w_mm,
        sticker.h_mm,
        sticker.angle_deg
      );
      for (let i = 0; i < 4; i++) {
        assertClose(bbox[i], expected.bbox_mm[i], tol, `${c.id} bbox[${i}]`);
      }

      // 3. Center conversion to px
      const cxPx = mm_to_px(sticker.cx_mm, scale);
      const cyPx = mm_to_px(sticker.cy_mm, scale);
      assertClose(cxPx, expected.center_px[0], tol, `${c.id} center_px.x`);
      assertClose(cyPx, expected.center_px[1], tol, `${c.id} center_px.y`);

      // 4. Inside check (full object)
      const inside = is_sticker_inside_object(poly, obj, 0.0);
      assert.equal(inside, expected.is_inside, `${c.id} is_inside failed`);

      // 5. Inside check (safe margin)
      const insideSafe = is_sticker_inside_object(poly, obj, obj.safe_margin_mm);
      assert.equal(insideSafe, expected.is_inside_safe, `${c.id} is_inside_safe failed`);
    }
  });
});
