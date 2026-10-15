/**
 * Геометрические преобразования и вычисления системы координат стикербомбинга.
 * Единый контракт с бэкендом (sticker_app/geometry.py).
 * Единственный источник истины — миллиметры (mm).
 */

export type Point2D = [number, number];
export type Polygon2D = Point2D[];
export type BBox2D = [number, number, number, number]; // [min_x, min_y, max_x, max_y]

export interface ObjectMask {
  width_mm: number;
  height_mm: number;
  corner_radius_mm?: number;
  safe_margin_mm?: number;
}

export type PointLike = Point2D | { x: number; y: number };

/**
 * Переводит физический размер в миллиметрах в экранные пиксели.
 */
export function mm_to_px(val_mm: number, scale: number): number {
  return val_mm * scale;
}
export const mmToPx = mm_to_px;

/**
 * Переводит экранные пиксели в физические миллиметры.
 */
export function px_to_mm(val_px: number, scale: number): number {
  if (scale === 0) {
    throw new Error("Масштаб scale не может быть равен 0");
  }
  return val_px / scale;
}
export const pxToMm = px_to_mm;

/**
 * Поворачивает точку (x, y) вокруг центра (cx, cy) на угол angle_deg по часовой стрелке.
 * В системе координат экрана ось X направлена вправо, ось Y направлена вниз.
 */
export function rotate_point(
  x: number,
  y: number,
  cx: number,
  cy: number,
  angle_deg: number
): Point2D {
  let norm_angle = angle_deg % 360.0;
  if (norm_angle < 0) {
    norm_angle += 360.0;
  }

  if (Math.abs(norm_angle) < 1e-9) {
    return [x, y];
  } else if (Math.abs(norm_angle - 90.0) < 1e-9) {
    const dx = x - cx;
    const dy = y - cy;
    return [cx - dy, cy + dx];
  } else if (Math.abs(norm_angle - 180.0) < 1e-9) {
    const dx = x - cx;
    const dy = y - cy;
    return [cx - dx, cy - dy];
  } else if (Math.abs(norm_angle - 270.0) < 1e-9) {
    const dx = x - cx;
    const dy = y - cy;
    return [cx + dy, cy - dx];
  }

  const rad = (norm_angle * Math.PI) / 180.0;
  const cos_a = Math.cos(rad);
  const sin_a = Math.sin(rad);

  const dx = x - cx;
  const dy = y - cy;

  const rx = cx + dx * cos_a - dy * sin_a;
  const ry = cy + dx * sin_a + dy * cos_a;

  return [rx, ry];
}
export const rotatePoint = rotate_point;

/**
 * Вычисляет 4 вершины повернутого прямоугольника стикера (в мм).
 * Порядок вершин: Top-Left, Top-Right, Bottom-Right, Bottom-Left.
 */
export function get_sticker_polygon(
  cx_mm: number,
  cy_mm: number,
  w_mm: number,
  h_mm: number,
  angle_deg: number = 0.0
): Polygon2D {
  const half_w = w_mm / 2.0;
  const half_h = h_mm / 2.0;

  const unrotated_corners: Point2D[] = [
    [cx_mm - half_w, cy_mm - half_h], // TL
    [cx_mm + half_w, cy_mm - half_h], // TR
    [cx_mm + half_w, cy_mm + half_h], // BR
    [cx_mm - half_w, cy_mm + half_h], // BL
  ];

  if (Math.abs(angle_deg % 360.0) < 1e-9) {
    return unrotated_corners;
  }

  return unrotated_corners.map(([x, y]) =>
    rotate_point(x, y, cx_mm, cy_mm, angle_deg)
  );
}
export const getStickerPolygon = get_sticker_polygon;

/**
 * Вычисляет габаритный ограничивающий прямоугольник (bounding box) повернутого стикера.
 * Возвращает кортеж [min_x, min_y, max_x, max_y] в миллиметрах.
 */
export function get_rotated_bbox(
  cx_mm: number,
  cy_mm: number,
  w_mm: number,
  h_mm: number,
  angle_deg: number = 0.0
): BBox2D {
  const poly = get_sticker_polygon(cx_mm, cy_mm, w_mm, h_mm, angle_deg);
  let min_x = Infinity;
  let min_y = Infinity;
  let max_x = -Infinity;
  let max_y = -Infinity;

  for (const [x, y] of poly) {
    if (x < min_x) min_x = x;
    if (x > max_x) max_x = x;
    if (y < min_y) min_y = y;
    if (y > max_y) max_y = y;
  }

  return [min_x, min_y, max_x, max_y];
}
export const getRotatedBBox = get_rotated_bbox;

/**
 * Проверяет аналитически, находится ли точка внутри скруглённого прямоугольника.
 */
export function is_point_inside_rounded_rect(
  x: number,
  y: number,
  w_mm: number,
  h_mm: number,
  corner_radius_mm: number = 0.0,
  margin_mm: number = 0.0,
  eps: number = 1e-7
): boolean {
  const x_min = margin_mm;
  const x_max = w_mm - margin_mm;
  const y_min = margin_mm;
  const y_max = h_mm - margin_mm;
  const r = Math.max(0.0, corner_radius_mm - margin_mm);

  if (x < x_min - eps || x > x_max + eps || y < y_min - eps || y > y_max + eps) {
    return false;
  }

  if (r <= 0) {
    return true;
  }

  // 4 угла со скруглением
  // Верхний левый угол
  if (x < x_min + r && y < y_min + r) {
    const dx = x - (x_min + r);
    const dy = y - (y_min + r);
    return dx * dx + dy * dy <= r * r + eps;
  }

  // Верхний правый угол
  if (x > x_max - r && y < y_min + r) {
    const dx = x - (x_max - r);
    const dy = y - (y_min + r);
    return dx * dx + dy * dy <= r * r + eps;
  }

  // Нижний левый угол
  if (x < x_min + r && y > y_max - r) {
    const dx = x - (x_min + r);
    const dy = y - (y_max - r);
    return dx * dx + dy * dy <= r * r + eps;
  }

  // Нижний правый угол
  if (x > x_max - r && y > y_max - r) {
    const dx = x - (x_max - r);
    const dy = y - (y_max - r);
    return dx * dx + dy * dy <= r * r + eps;
  }

  return true;
}
export const isPointInsideRoundedRect = is_point_inside_rounded_rect;

/**
 * Проверяет, целиком ли полигон стикера помещается внутрь маски объекта.
 */
export function is_sticker_inside_object(
  sticker_poly: PointLike[],
  object_mask: ObjectMask,
  safe_margin_mm?: number
): boolean {
  const w = object_mask.width_mm;
  const h = object_mask.height_mm;
  const r = object_mask.corner_radius_mm ?? 0.0;
  const margin = safe_margin_mm ?? object_mask.safe_margin_mm ?? 0.0;

  for (const pt of sticker_poly) {
    let px: number;
    let py: number;
    if (Array.isArray(pt)) {
      px = pt[0];
      py = pt[1];
    } else {
      px = pt.x;
      py = pt.y;
    }

    if (!is_point_inside_rounded_rect(px, py, w, h, r, margin)) {
      return false;
    }
  }

  return true;
}
export const isStickerInsideObject = is_sticker_inside_object;
