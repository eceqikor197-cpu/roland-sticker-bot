import { describe, it, expect, beforeEach } from "vitest";
import { useDesignStore, serializeDesign } from "./designStore";
import type { ObjectItem } from "../types";

const mockObject: ObjectItem = {
  id: "macbook_air_13",
  name: "MacBook Air 13\" (M2/M3)",
  category: "laptop",
  width_mm: 304.1,
  height_mm: 214.9,
  corner_radius_mm: 8.0,
  safe_margin_mm: 4.0,
  mask_svg_path_mm: null,
  verified: false,
};

describe("DesignStore Zustand Store", () => {
  beforeEach(() => {
    // Сброс хранилища перед каждым тестом
    useDesignStore.setState({
      object: mockObject,
      availableObjects: [mockObject],
      placements: [],
      selectedId: null,
      undoStack: [],
      redoStack: [],
      scale: 2.5,
      isShiftPressed: false,
    });
  });

  it("initializes with default object and empty placements", () => {
    const state = useDesignStore.getState();
    expect(state.object?.id).toBe("macbook_air_13");
    expect(state.placements).toEqual([]);
    expect(state.canUndo()).toBe(false);
    expect(state.canRedo()).toBe(false);
  });

  it("adds placements with proper mm coordinates and clamps width (15..150 mm)", () => {
    const store = useDesignStore.getState();

    // 1. Обычный стикер
    const id1 = store.addPlacement({
      asset_id: "sticker_01",
      title: "Neon Skull",
      aspect: 1.5,
      x_mm: 100,
      y_mm: 80,
      width_mm: 50,
    });

    let state = useDesignStore.getState();
    expect(state.placements.length).toBe(1);
    expect(state.placements[0].id).toBe(id1);
    expect(state.placements[0].width_mm).toBe(50);
    expect(state.placements[0].x_mm).toBe(100);
    expect(state.placements[0].y_mm).toBe(80);
    expect(state.selectedId).toBe(id1);
    expect(state.canUndo()).toBe(true);

    // 2. Слишком маленький (< 15 мм) -> кламп до 15.0
    const id2 = store.addPlacement({
      asset_id: "sticker_02",
      title: "Mini",
      width_mm: 5,
    });
    state = useDesignStore.getState();
    expect(state.placements.find((p) => p.id === id2)?.width_mm).toBe(15.0);

    // 3. Слишком большой (> 150 мм) -> кламп до 150.0
    const id3 = store.addPlacement({
      asset_id: "sticker_03",
      title: "Giant",
      width_mm: 200,
    });
    state = useDesignStore.getState();
    expect(state.placements.find((p) => p.id === id3)?.width_mm).toBe(150.0);
  });

  it("supports full undo and redo up to 100 steps", () => {
    const store = useDesignStore.getState();

    store.addPlacement({ asset_id: "st_1", title: "First", width_mm: 40 });
    expect(useDesignStore.getState().placements.length).toBe(1);

    store.addPlacement({ asset_id: "st_2", title: "Second", width_mm: 50 });
    expect(useDesignStore.getState().placements.length).toBe(2);

    // Undo 1: возвращаемся к 1 стикеру
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().placements.length).toBe(1);
    expect(useDesignStore.getState().placements[0].asset_id).toBe("st_1");
    expect(useDesignStore.getState().canRedo()).toBe(true);

    // Redo 1: снова 2 стикера
    useDesignStore.getState().redo();
    expect(useDesignStore.getState().placements.length).toBe(2);
    expect(useDesignStore.getState().placements[1].asset_id).toBe("st_2");

    // Undo 2 раза -> 0 стикеров
    useDesignStore.getState().undo();
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().placements.length).toBe(0);
    expect(useDesignStore.getState().canUndo()).toBe(false);
  });

  it("duplicates placements with +5mm offset and higher z-index", () => {
    const store = useDesignStore.getState();
    const id = store.addPlacement({
      asset_id: "st_dup",
      title: "Original",
      x_mm: 100,
      y_mm: 100,
      width_mm: 40,
    });

    const dupId = store.duplicatePlacement(id);
    expect(dupId).toBeTruthy();

    const state = useDesignStore.getState();
    expect(state.placements.length).toBe(2);

    const dup = state.placements.find((p) => p.id === dupId);
    expect(dup?.x_mm).toBe(105);
    expect(dup?.y_mm).toBe(105);
    expect(dup?.z).toBeGreaterThan(state.placements[0].z);
    expect(state.selectedId).toBe(dupId);
  });

  it("respects locked stickers (cannot move or delete)", () => {
    const store = useDesignStore.getState();
    const id = store.addPlacement({
      asset_id: "st_lock",
      title: "Locked",
      x_mm: 50,
      y_mm: 50,
    });

    store.toggleLock(id);
    expect(useDesignStore.getState().placements[0].locked).toBe(true);

    // Попытка сдвига не должна ничего менять
    store.updatePlacement(id, { x_mm: 80, y_mm: 80 });
    expect(useDesignStore.getState().placements[0].x_mm).toBe(50);

    // Попытка удаления заблокированного стикера игнорируется
    store.removePlacement(id);
    expect(useDesignStore.getState().placements.length).toBe(1);
  });

  it("manages z-order correctly (bringToFront, sendToBack)", () => {
    const store = useDesignStore.getState();
    const id1 = store.addPlacement({ asset_id: "st_1", title: "Bottom" });
    const id2 = store.addPlacement({ asset_id: "st_2", title: "Middle" });
    const id3 = store.addPlacement({ asset_id: "st_3", title: "Top" });

    // sendToBack для st_3
    store.sendToBack(id3);
    let state = useDesignStore.getState();
    let p3 = state.placements.find((p) => p.id === id3);
    let p1 = state.placements.find((p) => p.id === id1);
    expect(p3!.z).toBeLessThan(p1!.z);

    // bringToFront для st_3
    store.bringToFront(id3);
    state = useDesignStore.getState();
    p3 = state.placements.find((p) => p.id === id3);
    const p2 = state.placements.find((p) => p.id === id2);
    expect(p3!.z).toBeGreaterThan(p2!.z);
  });

  it("nudges selected sticker by specified millimeters", () => {
    const store = useDesignStore.getState();
    const id = store.addPlacement({
      asset_id: "st_nudge",
      title: "Nudge",
      x_mm: 100,
      y_mm: 100,
    });
    store.selectPlacement(id);

    // Шаг 1 мм вправо
    store.nudgeSelected(1.0, 0.0);
    expect(useDesignStore.getState().placements[0].x_mm).toBe(101.0);

    // Шаг с Shift: 10 мм вниз
    store.nudgeSelected(0.0, 10.0);
    expect(useDesignStore.getState().placements[0].y_mm).toBe(110.0);
  });

  it("serializes design strictly matching design.schema.json", () => {
    const store = useDesignStore.getState();
    store.addPlacement({
      asset_id: "st_serial",
      title: "Serial",
      x_mm: 123.456,
      y_mm: 78.912,
      width_mm: 45.0,
    });

    const serialized = store.getSerializedDesign();
    expect(serialized).not.toBeNull();
    expect(serialized?.schema_version).toBe("1.0");
    expect(serialized?.object_id).toBe("macbook_air_13");
    expect(serialized?.placements.length).toBe(1);

    const p = serialized!.placements[0];
    expect(p.asset_id).toBe("st_serial");
    expect(p.x_mm).toBe(123.46);
    expect(p.y_mm).toBe(78.91);
    expect(p.width_mm).toBe(45.0);
    expect(p.rotation_deg).toBe(0.0);
    expect(typeof p.z).toBe("number");
    expect(typeof p.locked).toBe("boolean");
  });
});
