import React from "react";
import { Layer, Rect, Text, Group } from "react-konva";
import type { ObjectItem } from "../types";
import { mm_to_px } from "../geometry";

interface BackgroundLayerProps {
  object: ObjectItem;
  scale: number;
  originX?: number;
  originY?: number;
}

export const BackgroundLayer: React.FC<BackgroundLayerProps> = ({
  object,
  scale,
  originX = 0,
  originY = 0,
}) => {
  const widthPx = mm_to_px(object.width_mm, scale);
  const heightPx = mm_to_px(object.height_mm, scale);
  const cornerRadiusPx = mm_to_px(object.corner_radius_mm ?? 8.0, scale);
  const safeMarginPx = mm_to_px(object.safe_margin_mm ?? 4.0, scale);

  const safeWidthPx = Math.max(0, widthPx - safeMarginPx * 2);
  const safeHeightPx = Math.max(0, heightPx - safeMarginPx * 2);
  const safeCornerRadiusPx = Math.max(0, cornerRadiusPx - safeMarginPx);

  return (
    <Layer listening={false}>
      {/* Тень и корпус ноутбука */}
      <Group x={originX} y={originY}>
        {/* Внешняя мягкая тень корпуса */}
        <Rect
          x={0}
          y={0}
          width={widthPx}
          height={heightPx}
          cornerRadius={cornerRadiusPx}
          fill="#1e222b"
          stroke="#384152"
          strokeWidth={2}
          shadowColor="rgba(0, 0, 0, 0.45)"
          shadowBlur={24}
          shadowOffset={{ x: 0, y: 12 }}
          shadowOpacity={0.8}
        />

        {/* Внутренняя поверхность крышки (Space Gray / Silver градиент) */}
        <Rect
          x={2}
          y={2}
          width={widthPx - 4}
          height={heightPx - 4}
          cornerRadius={Math.max(0, cornerRadiusPx - 2)}
          fill="#252a36"
        />

        {/* Пунктирный контур безопасной зоны (Safe Margin) */}
        {object.safe_margin_mm > 0 && (
          <Rect
            x={safeMarginPx}
            y={safeMarginPx}
            width={safeWidthPx}
            height={safeHeightPx}
            cornerRadius={safeCornerRadiusPx}
            stroke="#4f596d"
            strokeWidth={1}
            dash={[6, 4]}
            opacity={0.6}
          />
        )}

        {/* Текстовая метка модели и физических размеров */}
        <Text
          x={14}
          y={heightPx - 22}
          text={`${object.title} • ${object.width_mm} × ${object.height_mm} мм`}
          fontSize={11}
          fontFamily="system-ui, -apple-system, sans-serif"
          fill="#6b778c"
          opacity={0.8}
        />
      </Group>
    </Layer>
  );
};
