import React, { useEffect, useState, useCallback } from "react";
import { useDesignStore } from "./store/designStore";
import { api } from "./api/client";
import { Toolbar } from "./components/Toolbar/Toolbar";
import { CatalogPanel } from "./components/Catalog/CatalogPanel";
import { LayersPanel } from "./components/Layers/LayersPanel";
import { KonvaCanvas } from "./canvas/KonvaCanvas";
import { StatsBar } from "./components/Indicators/StatsBar";
import { ValidationModal } from "./components/ValidationModal";
import type { DesignValidationResult } from "./types";

export const App: React.FC = () => {
  const {
    setAvailableObjects,
    loadFromLocalStorage,
    selectedId,
    undo,
    redo,
    duplicatePlacement,
    removePlacement,
    nudgeSelected,
    setShiftPressed,
    getSerializedDesign,
  } = useDesignStore();

  const [validationResult, setValidationResult] = useState<DesignValidationResult | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Инициализация: загрузка объектов оклейки и восстановление из localStorage
  useEffect(() => {
    api
      .getObjects()
      .then((objects) => {
        setAvailableObjects(objects);
        loadFromLocalStorage();
      })
      .catch((err) => console.error("Ошибка загрузки объектов оклейки:", err));
  }, [setAvailableObjects, loadFromLocalStorage]);

  // Глобальные горячие клавиши
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Игнорируем горячие клавиши, если фокус в поле ввода
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      if (e.key === "Shift") {
        setShiftPressed(true);
      }

      const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const cmdOrCtrl = isMac ? e.metaKey : e.ctrlKey;

      // Undo: Cmd+Z (без Shift)
      if (cmdOrCtrl && (e.key === "z" || e.key === "Z" || e.key === "я" || e.key === "Я") && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
      }

      // Redo: Cmd+Shift+Z или Cmd+Y
      if (
        (cmdOrCtrl && e.shiftKey && (e.key === "z" || e.key === "Z" || e.key === "я" || e.key === "Я")) ||
        (cmdOrCtrl && (e.key === "y" || e.key === "Y"))
      ) {
        e.preventDefault();
        redo();
        return;
      }

      // Duplicate: Cmd+D
      if (cmdOrCtrl && (e.key === "d" || e.key === "D" || e.key === "в" || e.key === "В")) {
        e.preventDefault();
        if (selectedId) {
          duplicatePlacement(selectedId);
        }
        return;
      }

      // Delete / Backspace: удалить выделенный стикер
      if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedId) {
          e.preventDefault();
          removePlacement(selectedId);
        }
        return;
      }

      // Стрелки: микросдвиг (1 мм, с Shift: 10 мм)
      if (selectedId && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
        e.preventDefault();
        const step = e.shiftKey ? 10.0 : 1.0;
        let dx = 0;
        let dy = 0;
        if (e.key === "ArrowLeft") dx = -step;
        if (e.key === "ArrowRight") dx = step;
        if (e.key === "ArrowUp") dy = -step;
        if (e.key === "ArrowDown") dy = step;
        nudgeSelected(dx, dy);
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Shift") {
        setShiftPressed(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [undo, redo, duplicatePlacement, removePlacement, nudgeSelected, selectedId, setShiftPressed]);

  // Валидация макета через API
  const handleValidate = useCallback(async () => {
    const design = getSerializedDesign();
    if (!design) return;

    setIsValidating(true);
    setIsModalOpen(true);
    try {
      const res = await api.validateDesign(design);
      setValidationResult(res);
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : "Неизвестная ошибка валидации";
      setValidationResult({
        valid: false,
        errors: [errorMsg],
        warnings: [],
        placements_count: design.placements.length,
      });
    } finally {
      setIsValidating(false);
    }
  }, [getSerializedDesign]);

  // Экспорт JSON файла
  const handleExport = useCallback(() => {
    const design = getSerializedDesign();
    if (!design) {
      alert("Нет данных для экспорта");
      return;
    }

    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(design, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute(
      "download",
      `roland_design_${design.object_id}_${new Date().toISOString().slice(0, 10)}.json`
    );
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  }, [getSerializedDesign]);

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-[#13161c] text-slate-100 font-sans">
      {/* Верхний тулбар */}
      <Toolbar onValidate={handleValidate} onExport={handleExport} />

      {/* Основная рабочая область */}
      <div className="flex-1 flex overflow-hidden">
        {/* Левая колонка: Каталог */}
        <CatalogPanel />

        {/* Центр: Холст Konva */}
        <main className="flex-1 h-full overflow-hidden relative">
          <KonvaCanvas />
        </main>

        {/* Правая колонка: Слои */}
        <LayersPanel />
      </div>

      {/* Нижняя панель статистики и подсказок */}
      <StatsBar />

      {/* Модальное окно валидации */}
      <ValidationModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        result={validationResult}
        isLoading={isValidating}
        isObjectVerified={Boolean(useDesignStore.getState().object?.verified)}
      />
    </div>
  );
};

export default App;
