import React from "react";
import { Layer, Line, Text, Group } from "react-konva";
import { mm_to_px } from "../geometry";

interface RulerProps {
  width_mm: number;
  height_mm: number;
  scale: number;
  rulerThickness?: number;
}

export const Ruler: React.FC<RulerProps> = ({
  width_mm,
  height_mm,
  scale,
  rulerThickness = 24,
}) => {
  const widthPx = mm_to_px(width_mm, scale);
  const heightPx = mm_to_px(height_mm, scale);

  // Горизонтальные метки каждые 10 мм, цифры каждые 50 мм
  const xTicks: { mm: number; px: number; isMajor: boolean }[] = [];
  for (let mm = 0; mm <= width_mm; mm += 10) {
    xTicks.push({
      mm,
      px: mm_to_px(mm, scale),
      isMajor: mm % 50 === 0,
    });
  }

  // Вертикальные метки каждые 10 мм, цифры каждые 50 мм
  const yTicks: { mm: number; px: number; isMajor: boolean }[] = [];
  for (let mm = 0; mm <= height_mm; mm += 10) {
    yTicks.push({
      mm,
      px: mm_to_px(mm, scale),
      isMajor: mm % 50 === 0,
    });
  }

  return (
    <Layer listening={false}>
      {/* Верхняя горизонтальная линейка */}
      <Group y={-rulerThickness}>
        <Line
          points={[0, rulerThickness, widthPx, rulerThickness]}
          stroke="#475569"
          strokeWidth={1}
        />
        {xTicks.map(({ mm, px, isMajor }) => (
          <Group key={`xtick_${mm}`} x={px}>
            <Line
              points={[0, rulerThickness, 0, rulerThickness - (isMajor ? 10 : 5)]}
              stroke="#64748b"
              strokeWidth={1}
            />
            {isMajor && (
              <Text
                x={2}
                y={2}
                text={`${mm}`}
                fontSize={9}
                fontFamily="system-ui, sans-serif"
                fill="#94a3b8"
              />
            )}
          </Group>
        ))}
      </Group>

      {/* Левая вертикальная линейка */}
      <Group x={-rulerThickness}>
        <Line
          points={[rulerThickness, 0, rulerThickness, heightPx]}
          stroke="#475569"
          strokeWidth={1}
        />
        {yTicks.map(({ mm, px, isMajor }) => (
          <Group key={`ytick_${mm}`} y={px}>
            <Line
              points={[rulerThickness, 0, rulerThickness - (isMajor ? 10 : 5), 0]}
              stroke="#64748b"
              strokeWidth={1}
            />
            {isMajor && (
              <Text
                x={2}
                y={2}
                text={`${mm}`}
                fontSize={9}
                fontFamily="system-ui, sans-serif"
                fill="#94a3b8"
              />
            )}
          </Group>
        ))}
      </Group>
    </Layer>
  );
};
