import React, { useRef, useState, useCallback, useEffect } from "react";
import { Stage } from "react-konva";
import type Konva from "konva";
import { useDesignStore } from "../store/designStore";
import { BackgroundLayer } from "./BackgroundLayer";
import { StickersLayer } from "./StickersLayer";
import { OverlayLayer } from "./OverlayLayer";
import { Ruler } from "./Ruler";
import { mm_to_px, px_to_mm } from "../geometry";
import type { StickerSummary } from "../types";

const STAGE_PADDING = 60; // Отступ в пикселях для линеек и тени

export const KonvaCanvas: React.FC = () => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<Konva.Stage | null>(null);
  const nodeRefs = useRef<Map<string, Konva.Group>>(new Map());

  const {
    object,
    placements,
    selectedId,
    scale,
    isShiftPressed,
    selectPlacement,
    updatePlacement,
    addPlacement,
  } = useDesignStore();

  const [containerSize, setContainerSize] = useState({ width: 800, height: 600 });

  // Регистрация Konva узлов для Transformer
  const registerNodeRef = useCallback((id: string, node: Konva.Group | null) => {
    if (node) {
      nodeRefs.current.set(id, node);
    } else {
      nodeRefs.current.delete(id);
    }
  }, []);

  // Отслеживание размера родительского контейнера
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setContainerSize({
          width: Math.max(400, Math.floor(entry.contentRect.width)),
          height: Math.max(300, Math.floor(entry.contentRect.height)),
        });
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  if (!object) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        Выберите модель ноутбука для начала работы
      </div>
    );
  }

  const lidWidthPx = mm_to_px(object.width_mm, scale);
  const lidHeightPx = mm_to_px(object.height_mm, scale);

  // Центрирование макета внутри доступного контейнера холста
  const totalLidWidth = lidWidthPx + STAGE_PADDING * 2;
  const totalLidHeight = lidHeightPx + STAGE_PADDING * 2;
  const stageWidth = Math.max(containerSize.width, totalLidWidth);
  const stageHeight = Math.max(containerSize.height, totalLidHeight);

  const originX = Math.max(STAGE_PADDING, Math.floor((stageWidth - lidWidthPx) / 2));
  const originY = Math.max(STAGE_PADDING, Math.floor((stageHeight - lidHeightPx) / 2));

  const selectedPlacement = placements.find((p) => p.id === selectedId) ?? null;
  const selectedNode = selectedId ? nodeRefs.current.get(selectedId) ?? null : null;

  // Обработка броска Drag & Drop из каталога
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const rawData = e.dataTransfer.getData("application/json");
    if (!rawData) return;

    try {
      const sticker: StickerSummary = JSON.parse(rawData);
      if (!sticker.asset_id) return;

      const stage = stageRef.current;
      if (!stage) return;

      // Получаем координаты курсора относительно контейнера
      const stageRect = containerRef.current?.getBoundingClientRect();
      if (!stageRect) return;

      const clientX = e.clientX - stageRect.left;
      const clientY = e.clientY - stageRect.top;

      // Пересчёт в систему координат объекта (мм)
      const xPx = clientX - originX;
      const yPx = clientY - originY;
      const x_mm = px_to_mm(xPx, scale);
      const y_mm = px_to_mm(yPx, scale);

      addPlacement({
        asset_id: sticker.asset_id,
        title: sticker.title,
        aspect: sticker.aspect ?? 1.0,
        x_mm: Number(x_mm.toFixed(2)),
        y_mm: Number(y_mm.toFixed(2)),
        width_mm: 55.0, // Стандартная ширина при перетаскивании
      });
    } catch {
      // Игнорируем некорректный payload
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-auto bg-[#13161c] flex items-center justify-center select-none"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      <Stage
        ref={stageRef}
        width={stageWidth}
        height={stageHeight}
        onMouseDown={(e) => {
          // Клик в пустое пространство сбрасывает выделение
          if (e.target === e.target.getStage()) {
            selectPlacement(null);
          }
        }}
        onTouchStart={(e) => {
          if (e.target === e.target.getStage()) {
            selectPlacement(null);
          }
        }}
      >
        {/* Линейки */}
        <Ruler
          width_mm={object.width_mm}
          height_mm={object.height_mm}
          scale={scale}
          rulerThickness={24}
          originX={originX}
          originY={originY}
        />

        {/* Слой 1: Контур и фон крышки ноутбука */}
        <BackgroundLayer
          object={object}
          scale={scale}
          originX={originX}
          originY={originY}
        />

        {/* Слой 2: Стикеры с маской обрезки (clipFunc) */}
        <StickersLayer
          placements={placements}
          object={object}
          scale={scale}
          originX={originX}
          originY={originY}
          selectedId={selectedId}
          onSelectPlacement={selectPlacement}
          onUpdatePlacement={updatePlacement}
          registerNodeRef={registerNodeRef}
        />

        {/* Слой 3: Transformer и оверлей выделения */}
        <OverlayLayer
          selectedNode={selectedNode}
          selectedPlacement={selectedPlacement}
          object={object}
          scale={scale}
          isShiftPressed={isShiftPressed}
          onUpdatePlacement={updatePlacement}
        />
      </Stage>
    </div>
  );
};
