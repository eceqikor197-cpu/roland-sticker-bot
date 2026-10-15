import React, { useEffect, useRef } from "react";
import { Layer, Transformer } from "react-konva";
import type Konva from "konva";
import type { CanvasPlacement, ObjectItem } from "../types";
import { mm_to_px, px_to_mm, get_sticker_polygon, is_sticker_inside_object } from "../geometry";

interface OverlayLayerProps {
  selectedNode: Konva.Group | null;
  selectedPlacement: CanvasPlacement | null;
  object: ObjectItem;
  scale: number;
  isShiftPressed: boolean;
  onUpdatePlacement: (id: string, updates: Partial<CanvasPlacement>, recordHistory?: boolean) => void;
}

export const OverlayLayer: React.FC<OverlayLayerProps> = ({
  selectedNode,
  selectedPlacement,
  object,
  scale,
  isShiftPressed,
  onUpdatePlacement,
}) => {
  const trRef = useRef<Konva.Transformer | null>(null);

  useEffect(() => {
    if (!trRef.current) return;
    if (selectedNode && selectedPlacement && !selectedPlacement.locked) {
      trRef.current.nodes([selectedNode]);
      trRef.current.getLayer()?.batchDraw();
    } else {
      trRef.current.nodes([]);
      trRef.current.getLayer()?.batchDraw();
    }
  }, [selectedNode, selectedPlacement]);

  if (!selectedPlacement || selectedPlacement.locked) {
    return <Layer />;
  }

  // Проверка попадания стикера в безопасную зону для стилизации рамки трансформера
  const h_mm = selectedPlacement.width_mm / (selectedPlacement.aspect > 0 ? selectedPlacement.aspect : 1.0);
  const poly = get_sticker_polygon(
    selectedPlacement.x_mm,
    selectedPlacement.y_mm,
    selectedPlacement.width_mm,
    h_mm,
    selectedPlacement.rotation_deg
  );
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

  // Шаг привязки угла поворота: при Shift — 15°, иначе — основные 90°
  const rotationSnaps = isShiftPressed
    ? Array.from({ length: 24 }, (_, i) => i * 15)
    : [0, 90, 180, 270];

  const minWidthPx = mm_to_px(15.0, scale);
  const maxWidthPx = mm_to_px(150.0, scale);

  return (
    <Layer>
      <Transformer
        ref={trRef}
        rotateEnabled={true}
        resizeEnabled={true}
        keepRatio={true}
        enabledAnchors={["top-left", "top-right", "bottom-left", "bottom-right"]}
        rotationSnaps={rotationSnaps}
        rotationSnapTolerance={10}
        anchorSize={9}
        anchorCornerRadius={2}
        anchorFill="#ffffff"
        anchorStroke={isInside ? "#3b82f6" : "#ff4d4f"}
        anchorStrokeWidth={1.5}
        borderStroke={isInside ? "#3b82f6" : "#ff4d4f"}
        borderStrokeWidth={1.5}
        borderDash={isInside ? undefined : [4, 4]}
        boundBoxFunc={(oldBox, newBox) => {
          const w = Math.abs(newBox.width);
          if (w < minWidthPx || w > maxWidthPx) {
            return oldBox;
          }
          return newBox;
        }}
        onTransformEnd={() => {
          if (!selectedNode || !selectedPlacement) return;

          const scaleX = selectedNode.scaleX();
          const newWidthPx = selectedNode.width() * scaleX;
          const newWidthMm = Math.max(15.0, Math.min(150.0, px_to_mm(newWidthPx, scale)));

          let newRotation = selectedNode.rotation() % 360.0;
          if (newRotation < 0) newRotation += 360.0;

          const newX_mm = px_to_mm(selectedNode.x(), scale);
          const newY_mm = px_to_mm(selectedNode.y(), scale);

          // Сбрасываем внутренний масштаб узла Konva
          selectedNode.scaleX(1);
          selectedNode.scaleY(1);

          onUpdatePlacement(
            selectedPlacement.id,
            {
              width_mm: Number(newWidthMm.toFixed(2)),
              rotation_deg: Number(newRotation.toFixed(1)),
              x_mm: Number(newX_mm.toFixed(2)),
              y_mm: Number(newY_mm.toFixed(2)),
            },
            true
          );
        }}
      />
    </Layer>
  );
};
