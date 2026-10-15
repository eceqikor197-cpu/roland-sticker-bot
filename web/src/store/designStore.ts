import { create } from "zustand";
import type { ObjectItem, CanvasPlacement, SerializedDesign, DesignPlacement } from "../types";

const MAX_HISTORY_STEPS = 100;
const STORAGE_KEY = "roland_stickerbomb_draft";

export interface DesignState {
  object: ObjectItem | null;
  availableObjects: ObjectItem[];
  placements: CanvasPlacement[];
  selectedId: string | null;
  undoStack: CanvasPlacement[][];
  redoStack: CanvasPlacement[][];
  scale: number; // пикселей на мм
  isShiftPressed: boolean;

  // Actions
  setAvailableObjects: (objects: ObjectItem[]) => void;
  setObject: (object: ObjectItem) => void;
  setObjectById: (id: string) => void;
  setScale: (scale: number) => void;
  setShiftPressed: (pressed: boolean) => void;

  selectPlacement: (id: string | null) => void;
  addPlacement: (params: {
    asset_id: string;
    title: string;
    aspect?: number;
    x_mm?: number;
    y_mm?: number;
    width_mm?: number;
  }) => string;
  updatePlacement: (id: string, updates: Partial<CanvasPlacement>, recordHistory?: boolean) => void;
  removePlacement: (id: string) => void;
  duplicatePlacement: (id: string) => string | null;
  toggleLock: (id: string) => void;

  bringToFront: (id: string) => void;
  sendToBack: (id: string) => void;
  bringForward: (id: string) => void;
  sendBackward: (id: string) => void;

  nudgeSelected: (dx_mm: number, dy_mm: number) => void;

  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  clearCanvas: () => void;
  getSerializedDesign: () => SerializedDesign | null;
  loadFromLocalStorage: () => boolean;
  saveToLocalStorage: () => void;
  loadDesign: (design: SerializedDesign, assetMetaMap?: Record<string, { title: string; aspect: number }>) => void;
}

let nextId = 1;
export function generatePlacementId(): string {
  return `p_${Date.now()}_${nextId++}`;
}

export function serializeDesign(object: ObjectItem | null, placements: CanvasPlacement[]): SerializedDesign | null {
  if (!object) return null;
  return {
    schema_version: "1.0",
    object_id: object.id,
    created_at: Date.now(),
    placements: placements.map((p) => ({
      asset_id: p.asset_id,
      x_mm: Number(p.x_mm.toFixed(2)),
      y_mm: Number(p.y_mm.toFixed(2)),
      width_mm: Number(p.width_mm.toFixed(2)),
      rotation_deg: Number((((p.rotation_deg % 360) + 360) % 360).toFixed(1)),
      z: Math.round(p.z),
      locked: Boolean(p.locked),
    })),
  };
}

export const useDesignStore = create<DesignState>((set, get) => {
  const pushToHistory = (currentPlacements: CanvasPlacement[]) => {
    const { undoStack } = get();
    const cloned = currentPlacements.map((p) => ({ ...p }));
    const newUndo = [...undoStack, cloned];
    if (newUndo.length > MAX_HISTORY_STEPS) {
      newUndo.shift();
    }
    return {
      undoStack: newUndo,
      redoStack: [],
    };
  };

  const autoSave = (stateToSave?: { object: ObjectItem | null; placements: CanvasPlacement[] }) => {
    try {
      const obj = stateToSave ? stateToSave.object : get().object;
      const plc = stateToSave ? stateToSave.placements : get().placements;
      const serialized = serializeDesign(obj, plc);
      if (serialized && typeof window !== "undefined" && window.localStorage) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(serialized));
      }
    } catch {
      // Игнорируем ошибки квот localStorage
    }
  };

  return {
    object: null,
    availableObjects: [],
    placements: [],
    selectedId: null,
    undoStack: [],
    redoStack: [],
    scale: 2.5, // По умолчанию 2.5 px на 1 mm
    isShiftPressed: false,

    setAvailableObjects: (objects) => {
      set({ availableObjects: objects });
      if (!get().object && objects.length > 0) {
        get().setObject(objects[0]);
      }
    },

    setObject: (object) => {
      set({ object });
      autoSave();
    },

    setObjectById: (id) => {
      const found = get().availableObjects.find((o) => o.id === id);
      if (found) {
        set({ object: found });
        autoSave();
      }
    },

    setScale: (scale) => set({ scale: Math.max(0.5, Math.min(10, scale)) }),
    setShiftPressed: (isShiftPressed) => set({ isShiftPressed }),

    selectPlacement: (id) => set({ selectedId: id }),

    addPlacement: ({ asset_id, title, aspect = 1.0, x_mm, y_mm, width_mm = 60.0 }) => {
      const { object, placements } = get();
      const id = generatePlacementId();

      const cx = x_mm ?? (object ? object.width_mm / 2 : 150);
      const cy = y_mm ?? (object ? object.height_mm / 2 : 100);
      const clampedWidth = Math.max(15.0, Math.min(150.0, width_mm));

      // Новый стикер поверх существующих
      const maxZ = placements.reduce((acc, p) => Math.max(acc, p.z), 0);

      const newPlacement: CanvasPlacement = {
        id,
        asset_id,
        title,
        aspect: aspect > 0 ? aspect : 1.0,
        x_mm: Number(cx.toFixed(2)),
        y_mm: Number(cy.toFixed(2)),
        width_mm: Number(clampedWidth.toFixed(2)),
        rotation_deg: 0,
        z: maxZ + 1,
        locked: false,
      };

      const historyUpdate = pushToHistory(placements);
      const updatedPlacements = [...placements, newPlacement];

      set({
        ...historyUpdate,
        placements: updatedPlacements,
        selectedId: id,
      });

      autoSave({ object, placements: updatedPlacements });
      return id;
    },

    updatePlacement: (id, updates, recordHistory = false) => {
      const { placements, object } = get();
      const index = placements.findIndex((p) => p.id === id);
      if (index === -1) return;

      const target = placements[index];
      if (target.locked && updates.locked === undefined) {
        // Заблокированный стикер нельзя двигать или менять размер
        return;
      }

      let historyUpdate = {};
      if (recordHistory) {
        historyUpdate = pushToHistory(placements);
      }

      // Клампинг ширины если она меняется
      const finalUpdates = { ...updates };
      if (updates.width_mm !== undefined) {
        finalUpdates.width_mm = Math.max(15.0, Math.min(150.0, updates.width_mm));
      }

      const updatedList = placements.map((p) => (p.id === id ? { ...p, ...finalUpdates } : p));

      set({
        ...historyUpdate,
        placements: updatedList,
      });

      autoSave({ object, placements: updatedList });
    },

    removePlacement: (id) => {
      const { placements, selectedId, object } = get();
      const target = placements.find((p) => p.id === id);
      if (!target || target.locked) return;

      const historyUpdate = pushToHistory(placements);
      const updatedList = placements.filter((p) => p.id !== id);

      set({
        ...historyUpdate,
        placements: updatedList,
        selectedId: selectedId === id ? null : selectedId,
      });

      autoSave({ object, placements: updatedList });
    },

    duplicatePlacement: (id) => {
      const { placements, object } = get();
      const target = placements.find((p) => p.id === id);
      if (!target) return null;

      const newId = generatePlacementId();
      const maxZ = placements.reduce((acc, p) => Math.max(acc, p.z), 0);

      // Смещение копии на +5 мм по диагонали
      const newPlacement: CanvasPlacement = {
        ...target,
        id: newId,
        x_mm: Number((target.x_mm + 5.0).toFixed(2)),
        y_mm: Number((target.y_mm + 5.0).toFixed(2)),
        z: maxZ + 1,
        locked: false,
      };

      const historyUpdate = pushToHistory(placements);
      const updatedList = [...placements, newPlacement];

      set({
        ...historyUpdate,
        placements: updatedList,
        selectedId: newId,
      });

      autoSave({ object, placements: updatedList });
      return newId;
    },

    toggleLock: (id) => {
      const { placements, object } = get();
      const updatedList = placements.map((p) => (p.id === id ? { ...p, locked: !p.locked } : p));
      set({ placements: updatedList });
      autoSave({ object, placements: updatedList });
    },

    bringToFront: (id) => {
      const { placements, object } = get();
      const target = placements.find((p) => p.id === id);
      if (!target) return;
      const maxZ = placements.reduce((acc, p) => Math.max(acc, p.z), 0);
      if (target.z === maxZ) return;

      const historyUpdate = pushToHistory(placements);
      const updatedList = placements.map((p) => (p.id === id ? { ...p, z: maxZ + 1 } : p));

      set({ ...historyUpdate, placements: updatedList });
      autoSave({ object, placements: updatedList });
    },

    sendToBack: (id) => {
      const { placements, object } = get();
      const target = placements.find((p) => p.id === id);
      if (!target) return;
      const minZ = placements.reduce((acc, p) => Math.min(acc, p.z), 0);
      if (target.z === minZ && minZ === 0) return;

      const historyUpdate = pushToHistory(placements);
      // Уменьшаем z для целевого
      const updatedList = placements.map((p) => (p.id === id ? { ...p, z: Math.max(0, minZ - 1) } : p));
      // Нормализуем z-индексы
      const sorted = [...updatedList].sort((a, b) => a.z - b.z);
      sorted.forEach((p, idx) => {
        p.z = idx;
      });

      set({ ...historyUpdate, placements: [...sorted] });
      autoSave({ object, placements: sorted });
    },

    bringForward: (id) => {
      const { placements, object } = get();
      const sorted = [...placements].sort((a, b) => a.z - b.z);
      const idx = sorted.findIndex((p) => p.id === id);
      if (idx === -1 || idx === sorted.length - 1) return;

      const historyUpdate = pushToHistory(placements);
      const current = sorted[idx];
      const next = sorted[idx + 1];

      const tempZ = current.z;
      current.z = next.z;
      next.z = tempZ;

      if (current.z === next.z) {
        current.z += 1;
      }

      set({ ...historyUpdate, placements: [...sorted] });
      autoSave({ object, placements: sorted });
    },

    sendBackward: (id) => {
      const { placements, object } = get();
      const sorted = [...placements].sort((a, b) => a.z - b.z);
      const idx = sorted.findIndex((p) => p.id === id);
      if (idx <= 0) return;

      const historyUpdate = pushToHistory(placements);
      const current = sorted[idx];
      const prev = sorted[idx - 1];

      const tempZ = current.z;
      current.z = prev.z;
      prev.z = tempZ;

      if (current.z === prev.z) {
        prev.z += 1;
      }

      set({ ...historyUpdate, placements: [...sorted] });
      autoSave({ object, placements: sorted });
    },

    nudgeSelected: (dx_mm, dy_mm) => {
      const { selectedId, placements, object } = get();
      if (!selectedId) return;

      const target = placements.find((p) => p.id === selectedId);
      if (!target || target.locked) return;

      const historyUpdate = pushToHistory(placements);
      const updatedList = placements.map((p) =>
        p.id === selectedId
          ? {
              ...p,
              x_mm: Number((p.x_mm + dx_mm).toFixed(2)),
              y_mm: Number((p.y_mm + dy_mm).toFixed(2)),
            }
          : p
      );

      set({ ...historyUpdate, placements: updatedList });
      autoSave({ object, placements: updatedList });
    },

    undo: () => {
      const { undoStack, redoStack, placements, object } = get();
      if (undoStack.length === 0) return;

      const prevPlacements = undoStack[undoStack.length - 1];
      const newUndo = undoStack.slice(0, undoStack.length - 1);
      const newRedo = [...redoStack, placements.map((p) => ({ ...p }))];

      set({
        placements: prevPlacements,
        undoStack: newUndo,
        redoStack: newRedo,
      });

      autoSave({ object, placements: prevPlacements });
    },

    redo: () => {
      const { undoStack, redoStack, placements, object } = get();
      if (redoStack.length === 0) return;

      const nextPlacements = redoStack[redoStack.length - 1];
      const newRedo = redoStack.slice(0, redoStack.length - 1);
      const newUndo = [...undoStack, placements.map((p) => ({ ...p }))];

      set({
        placements: nextPlacements,
        undoStack: newUndo,
        redoStack: newRedo,
      });

      autoSave({ object, placements: nextPlacements });
    },

    canUndo: () => get().undoStack.length > 0,
    canRedo: () => get().redoStack.length > 0,

    clearCanvas: () => {
      const { placements, object } = get();
      if (placements.length === 0) return;

      const historyUpdate = pushToHistory(placements);
      set({
        ...historyUpdate,
        placements: [],
        selectedId: null,
      });
      autoSave({ object, placements: [] });
    },

    getSerializedDesign: () => {
      const { object, placements } = get();
      return serializeDesign(object, placements);
    },

    saveToLocalStorage: () => {
      autoSave();
    },

    loadFromLocalStorage: () => {
      try {
        if (typeof window === "undefined" || !window.localStorage) return false;
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return false;
        const parsed = JSON.parse(raw);
        if (parsed.schema_version !== "1.0" || !parsed.object_id || !Array.isArray(parsed.placements)) {
          return false;
        }

        const { availableObjects } = get();
        const obj = availableObjects.find((o) => o.id === parsed.object_id);
        if (obj) {
          set({ object: obj });
        }

        const reconstructed: CanvasPlacement[] = parsed.placements.map((p: DesignPlacement, index: number) => ({
          id: `p_restored_${index}_${p.asset_id}`,
          asset_id: p.asset_id,
          title: `Стикер ${p.asset_id.slice(0, 8)}`,
          aspect: 1.0,
          x_mm: p.x_mm,
          y_mm: p.y_mm,
          width_mm: p.width_mm,
          rotation_deg: p.rotation_deg ?? 0,
          z: p.z ?? index,
          locked: Boolean(p.locked),
        }));

        set({
          placements: reconstructed,
          selectedId: null,
          undoStack: [],
          redoStack: [],
        });
        return true;
      } catch {
        return false;
      }
    },

    loadDesign: (design, assetMetaMap = {}) => {
      const { availableObjects } = get();
      const obj = availableObjects.find((o) => o.id === design.object_id);
      if (obj) {
        set({ object: obj });
      }

      const reconstructed: CanvasPlacement[] = design.placements.map((p: DesignPlacement, index: number) => {
        const meta = assetMetaMap[p.asset_id];
        return {
          id: `p_loaded_${index}_${p.asset_id}`,
          asset_id: p.asset_id,
          title: meta?.title ?? `Стикер ${p.asset_id.slice(0, 8)}`,
          aspect: meta?.aspect ?? 1.0,
          x_mm: p.x_mm,
          y_mm: p.y_mm,
          width_mm: p.width_mm,
          rotation_deg: p.rotation_deg ?? 0,
          z: p.z ?? index,
          locked: Boolean(p.locked),
        };
      });

      set({
        placements: reconstructed,
        selectedId: null,
        undoStack: [],
        redoStack: [],
      });
      autoSave({ object: obj ?? get().object, placements: reconstructed });
    },
  };
});
