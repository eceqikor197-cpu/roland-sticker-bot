import React, { useRef, useEffect } from "react";
import { Group, Image as KonvaImage, Rect } from "react-konva";
import type Konva from "konva";
import type { CanvasPlacement, ObjectItem } from "../types";
import { mm_to_px, px_to_mm, get_sticker_polygon, is_sticker_inside_object } from "../geometry";
import { useImageLoader } from "./useImageLoader";
import { api } from "../api/client";

interface StickerNodeProps {
  placement: CanvasPlacement;
  object: ObjectItem;
  scale: number;
  isSelected: boolean;
  onSelect: () => void;
  onUpdate: (updates: Partial<CanvasPlacement>, recordHistory?: boolean) => void;
  nodeRefCallback: (node: Konva.Group | null) => void;
}

export const StickerNode: React.FC<StickerNodeProps> = ({
  placement,
  object,
  scale,
  isSelected,
  onSelect,
  onUpdate,
  nodeRefCallback,
}) => {
  const groupRef = useRef<Konva.Group | null>(null);

  useEffect(() => {
    nodeRefCallback(groupRef.current);
  }, [nodeRefCallback]);

  const imageUrl = api.getStickerImageUrl(placement.asset_id, "preview");
  const [image, imageStatus] = useImageLoader(imageUrl);

  // Размеры в пикселях
  const widthPx = mm_to_px(placement.width_mm, scale);
  const heightPx = widthPx / (placement.aspect > 0 ? placement.aspect : 1.0);
  const xPx = mm_to_px(placement.x_mm, scale);
  const yPx = mm_to_px(placement.y_mm, scale);

  // Проверка попадания в безопасную зону объекта
  const h_mm = placement.width_mm / (placement.aspect > 0 ? placement.aspect : 1.0);
  const poly = get_sticker_polygon(placement.x_mm, placement.y_mm, placement.width_mm, h_mm, placement.rotation_deg);
  const isInside = is_sticker_inside_object(
    poly,
    {
      width_mm: object.width_mm,
      height_mm: object.height_mm,
      corner_radius_mm: object.corner_radius_mm,
      safe_margin_mm: object.safe_margin_mm,
    },
    object.safe_margin_mm
  );

  return (
    <Group
      ref={(node) => {
        groupRef.current = node;
        nodeRefCallback(node);
      }}
      id={placement.id}
      x={xPx}
      y={yPx}
      width={widthPx}
      height={heightPx}
      offsetX={widthPx / 2}
      offsetY={heightPx / 2}
      rotation={placement.rotation_deg}
      draggable={!placement.locked}
      onClick={(e) => {
        e.cancelBubble = true;
        onSelect();
      }}
      onTap={(e) => {
        e.cancelBubble = true;
        onSelect();
      }}
      onDragEnd={(e) => {
        const newX_mm = px_to_mm(e.target.x(), scale);
        const newY_mm = px_to_mm(e.target.y(), scale);
        onUpdate(
          {
            x_mm: Number(newX_mm.toFixed(2)),
            y_mm: Number(newY_mm.toFixed(2)),
          },
          true
        );
      }}
    >
      {/* Предупреждающая подсветка контура, если стикер выходит за безопасную зону */}
      {!isInside && (
        <Rect
          x={-2}
          y={-2}
          width={widthPx + 4}
          height={heightPx + 4}
          stroke="#ff4d4f"
          strokeWidth={2}
          dash={[4, 4]}
          cornerRadius={4}
          opacity={0.85}
        />
      )}

      {/* Отрисовка изображения стикера или плейсхолдера при загрузке */}
      {image && imageStatus === "loaded" ? (
        <KonvaImage
          image={image}
          width={widthPx}
          height={heightPx}
          listening={true}
        />
      ) : (
        <Rect
          width={widthPx}
          height={heightPx}
          fill="#333a48"
          stroke="#556075"
          strokeWidth={1}
          cornerRadius={4}
          opacity={0.6}
        />
      )}

      {/* Тонкая рамка выделения, если стикер выбран, но не трансформируется */}
      {isSelected && (
        <Rect
          x={0}
          y={0}
          width={widthPx}
          height={heightPx}
          stroke="#3b82f6"
          strokeWidth={1.5}
          opacity={0.9}
        />
      )}
    </Group>
  );
};
