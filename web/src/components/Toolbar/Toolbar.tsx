import React from "react";
import {
  Undo2,
  Redo2,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Trash2,
  CheckCircle2,
  Download,
  Laptop,
} from "lucide-react";
import { useDesignStore } from "../../store/designStore";

interface ToolbarProps {
  onValidate: () => void;
  onExport: () => void;
}

export const Toolbar: React.FC<ToolbarProps> = ({ onValidate, onExport }) => {
  const {
    object,
    availableObjects,
    setObjectById,
    scale,
    setScale,
    undo,
    redo,
    canUndo,
    canRedo,
    clearCanvas,
    placements,
  } = useDesignStore();

  const handleZoomIn = () => setScale(scale * 1.2);
  const handleZoomOut = () => setScale(scale / 1.2);
  const handleZoomReset = () => setScale(2.5);

  const handleClear = () => {
    if (placements.length === 0) return;
    if (window.confirm("Очистить все стикеры с макета?")) {
      clearCanvas();
    }
  };

  return (
    <header className="h-14 bg-[#1a1e27] border-b border-[#2d3748] px-4 flex items-center justify-between text-slate-200 select-none">
      {/* Левая секция: Логотип и выбор модели */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-lg">✨</span>
          <span className="font-bold text-sm tracking-wide text-white">
            Roland Stickerbomb
          </span>
        </div>

        <div className="h-4 w-px bg-[#2d3748]" />

        {/* Выбор объекта оклейки */}
        <div className="flex items-center gap-2">
          <Laptop className="w-4 h-4 text-blue-400" />
          <select
            value={object?.id ?? ""}
            onChange={(e) => setObjectById(e.target.value)}
            className="text-xs bg-[#242b38] border border-[#384357] rounded-md px-2.5 py-1.5 text-slate-100 focus:outline-none focus:border-blue-500 cursor-pointer"
          >
            {availableObjects.map((obj) => (
              <option key={obj.id} value={obj.id}>
                {obj.title} ({obj.width_mm} × {obj.height_mm} мм)
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Центральная секция: Undo / Redo и Zoom */}
      <div className="flex items-center gap-1.5">
        {/* Undo / Redo */}
        <div className="flex items-center bg-[#242b38] rounded-md p-0.5 border border-[#384357]">
          <button
            onClick={undo}
            disabled={!canUndo()}
            title="Отменить действие (Cmd/Ctrl+Z)"
            className="p-1.5 rounded hover:bg-[#344055] disabled:opacity-40 disabled:hover:bg-transparent text-slate-300"
          >
            <Undo2 className="w-4 h-4" />
          </button>
          <button
            onClick={redo}
            disabled={!canRedo()}
            title="Повторить действие (Cmd/Ctrl+Shift+Z)"
            className="p-1.5 rounded hover:bg-[#344055] disabled:opacity-40 disabled:hover:bg-transparent text-slate-300"
          >
            <Redo2 className="w-4 h-4" />
          </button>
        </div>

        <div className="h-4 w-px bg-[#2d3748] mx-1" />

        {/* Zoom */}
        <div className="flex items-center bg-[#242b38] rounded-md p-0.5 border border-[#384357]">
          <button
            onClick={handleZoomOut}
            title="Уменьшить масштаб"
            className="p-1.5 rounded hover:bg-[#344055] text-slate-300"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <button
            onClick={handleZoomReset}
            title="Сбросить масштаб (100%)"
            className="px-2 py-1 text-xs text-slate-300 hover:bg-[#344055] rounded"
          >
            {Math.round((scale / 2.5) * 100)}%
          </button>
          <button
            onClick={handleZoomIn}
            title="Увеличить масштаб"
            className="p-1.5 rounded hover:bg-[#344055] text-slate-300"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={handleZoomReset}
            title="Вписать в экран"
            className="p-1.5 rounded hover:bg-[#344055] text-slate-300"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="h-4 w-px bg-[#2d3748] mx-1" />

        {/* Очистить */}
        <button
          onClick={handleClear}
          disabled={placements.length === 0}
          title="Очистить холст"
          className="p-1.5 rounded hover:bg-red-500/20 text-slate-400 hover:text-red-400 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      {/* Правая секция: Валидация и Экспорт */}
      <div className="flex items-center gap-2">
        <button
          onClick={onValidate}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[#242b38] hover:bg-[#2f394a] border border-[#384357] text-slate-200 transition-colors"
        >
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Проверить макет</span>
        </button>

        <button
          onClick={onExport}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-sm transition-colors"
        >
          <Download className="w-4 h-4" />
          <span>Экспорт JSON</span>
        </button>
      </div>
    </header>
  );
};
