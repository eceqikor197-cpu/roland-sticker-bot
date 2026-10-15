import type { components } from "../api/schema";

export type ObjectItem = components["schemas"]["ObjectItem"];
export type ObjectsListResponse = components["schemas"]["ObjectsListResponse"];
export type StickerSummary = components["schemas"]["StickerSummary"];
export type StickerDetail = components["schemas"]["StickerDetail"];
export type StickerListResponse = components["schemas"]["StickerListResponse"];
export type CategoryTree = components["schemas"]["CategoryTree"];
export type DesignData = components["schemas"]["DesignData"];
export type DesignPlacement = components["schemas"]["DesignPlacement"];
export type DesignValidationResult = components["schemas"]["DesignValidationResult"];

/**
 * Расширенная структура стикера на холсте.
 * Содержит локальный id для однозначной идентификации копий на клиенте
 * и метаданные макета (aspect, title) для корректного масштабирования и отображения в UI.
 */
export interface CanvasPlacement extends DesignPlacement {
  /** Локальный уникальный ID размещения (для React/Konva ключей и дублирования) */
  id: string;
  /** Пропорция сторон (width / height) */
  aspect: number;
  /** Название стикера для списка слоёв */
  title: string;
}

/**
 * Сериализованный макет для сохранения в localStorage или отправки на бэкенд.
 */
export interface SerializedDesign {
  schema_version: "1.0";
  object_id: string;
  created_at: number;
  placements: DesignPlacement[];
}

export type StickerImageSize = "thumb" | "preview" | "full";
