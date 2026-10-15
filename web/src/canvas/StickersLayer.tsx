import React from "react";
import { Layer, Group } from "react-konva";
import type Konva from "konva";
import type { CanvasPlacement, ObjectItem } from "../types";
import { mm_to_px } from "../geometry";
import { StickerNode } from "./StickerNode";

interface StickersLayerProps {
  placements: CanvasPlacement[];
  object: ObjectItem;
  scale: number;
  selectedId: string | null;
  onSelectPlacement: (id: string | null) => void;
  onUpdatePlacement: (id: string, updates: Partial<CanvasPlacement>, recordHistory?: boolean) => void;
  registerNodeRef: (id: string, node: Konva.Group | null) => void;
}

export const StickersLayer: React.FC<StickersLayerProps> = ({
  placements,
  object,
  scale,
  selectedId,
  onSelectPlacement,
  onUpdatePlacement,
  registerNodeRef,
}) => {
  // Сортировка по z-index для правильного порядка наложения
  const sortedPlacements = [...placements].sort((a, b) => a.z - b.z);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const clipFunc = (ctx: any) => {
    const w = mm_to_px(object.width_mm, scale);
    const h = mm_to_px(object.height_mm, scale);
    const r = mm_to_px(object.corner_radius_mm ?? 8.0, scale);

    ctx.beginPath();
    if (typeof ctx.roundRect === "function") {
      ctx.roundRect(0, 0, w, h, r);
    } else {
      ctx.moveTo(r, 0);
      ctx.lineTo(w - r, 0);
      ctx.quadraticCurveTo(w, 0, w, r);
      ctx.lineTo(w, h - r);
      ctx.quadraticCurveTo(w, h, w - r, h);
      ctx.lineTo(r, h);
      ctx.quadraticCurveTo(0, h, 0, h - r);
      ctx.lineTo(0, r);
      ctx.quadraticCurveTo(0, 0, r, 0);
    }
    ctx.closePath();
  };

  return (
    <Layer>
      {/* Группа с маской обрезки (clipFunc) по форме крышки ноутбука */}
      <Group clipFunc={clipFunc}>
        {sortedPlacements.map((placement) => (
          <StickerNode
            key={placement.id}
            placement={placement}
            object={object}
            scale={scale}
            isSelected={selectedId === placement.id}
            onSelect={() => onSelectPlacement(placement.id)}
            onUpdate={(updates, recordHistory) => onUpdatePlacement(placement.id, updates, recordHistory)}
            nodeRefCallback={(node) => registerNodeRef(placement.id, node)}
          />
        ))}
      </Group>
    </Layer>
  );
};
