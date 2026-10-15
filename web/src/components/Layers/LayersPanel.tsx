import React from "react";
import {
  Layers,
  Lock,
  Unlock,
  Trash2,
  Copy,
  ChevronUp,
  ChevronDown,
  ChevronsUp,
  ChevronsDown,
} from "lucide-react";
import { useDesignStore } from "../../store/designStore";
import { api } from "../../api/client";

export const LayersPanel: React.FC = () => {
  const {
    placements,
    selectedId,
    selectPlacement,
    toggleLock,
    removePlacement,
    duplicatePlacement,
    bringToFront,
    sendToBack,
    bringForward,
    sendBackward,
  } = useDesignStore();

  // Слои отображаются сверху вниз: самый верхний z вверху списка
  const sortedPlacements = [...placements].sort((a, b) => b.z - a.z);

  return (
    <aside className="w-72 h-full flex flex-col bg-[#1a1e27] border-l border-[#2d3748] text-slate-200 select-none">
      {/* Шапка панели слоёв */}
      <div className="p-4 border-b border-[#2d3748] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-blue-400" />
          <h2 className="text-sm font-semibold tracking-wide uppercase text-slate-400">
            Слои
          </h2>
        </div>
        <span className="text-xs text-slate-500">{placements.length} шт.</span>
      </div>

      {/* Список слоёв */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {sortedPlacements.length === 0 ? (
          <div className="text-center py-12 text-slate-500 text-xs">
            Стикеры не добавлены
          </div>
        ) : (
          sortedPlacements.map((p) => {
            const isSelected = p.id === selectedId;
            const thumbUrl = api.getStickerImageUrl(p.asset_id, "thumb");

            return (
              <div
                key={p.id}
                onClick={() => selectPlacement(p.id)}
                className={`group flex items-center gap-2 px-2.5 py-1.5 rounded-md border text-xs cursor-pointer transition-all ${
                  isSelected
                    ? "bg-blue-600/20 border-blue-500 text-white font-medium shadow-sm"
                    : "bg-[#242b38] border-transparent hover:border-[#384357] text-slate-300"
                }`}
              >
                {/* Миниатюра */}
                <div className="w-7 h-7 flex-shrink-0 bg-[#151922] rounded flex items-center justify-center p-0.5 overflow-hidden">
                  <img
                    src={thumbUrl}
                    alt={p.title}
                    className="max-w-full max-h-full object-contain"
                    onError={(e) => {
                      (e.target as HTMLElement).style.display = "none";
                    }}
                  />
                </div>

                {/* Заголовок и параметры */}
                <div className="flex-1 min-w-0">
                  <div className="truncate text-[11px]">{p.title}</div>
                  <div className="text-[9px] text-slate-500">
                    {p.width_mm} мм • {p.rotation_deg}°
                  </div>
                </div>

                {/* Действия со слоем */}
                <div className="flex items-center gap-1 opacity-80 group-hover:opacity-100">
                  {/* Замочек (блокировка) */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleLock(p.id);
                    }}
                    title={p.locked ? "Разблокировать" : "Заблокировать"}
                    className={`p-1 rounded hover:bg-[#344055] ${
                      p.locked ? "text-amber-400" : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {p.locked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
                  </button>

                  {/* Удаление */}
                  {!p.locked && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removePlacement(p.id);
                      }}
                      title="Удалить"
                      className="p-1 rounded hover:bg-red-500/20 text-slate-500 hover:text-red-400"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Панель управления выбранным слоем */}
      {selectedId && (
        <div className="p-3 border-t border-[#2d3748] bg-[#161a22] space-y-2">
          <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">
            Порядок слоя
          </div>
          <div className="grid grid-cols-4 gap-1">
            <button
              onClick={() => bringToFront(selectedId)}
              title="На самый передний план"
              className="p-1.5 flex items-center justify-center rounded bg-[#242b38] hover:bg-[#344055] text-slate-300"
            >
              <ChevronsUp className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => bringForward(selectedId)}
              title="На один слой выше"
              className="p-1.5 flex items-center justify-center rounded bg-[#242b38] hover:bg-[#344055] text-slate-300"
            >
              <ChevronUp className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => sendBackward(selectedId)}
              title="На один слой ниже"
              className="p-1.5 flex items-center justify-center rounded bg-[#242b38] hover:bg-[#344055] text-slate-300"
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => sendToBack(selectedId)}
              title="На самый задний план"
              className="p-1.5 flex items-center justify-center rounded bg-[#242b38] hover:bg-[#344055] text-slate-300"
            >
              <ChevronsDown className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="pt-1 flex gap-1">
            <button
              onClick={() => duplicatePlacement(selectedId)}
              className="flex-1 py-1.5 px-2 flex items-center justify-center gap-1.5 rounded bg-[#242b38] hover:bg-[#344055] text-xs text-slate-300"
            >
              <Copy className="w-3 h-3" />
              <span>Дубликат</span>
            </button>
            <button
              onClick={() => removePlacement(selectedId)}
              className="py-1.5 px-2.5 flex items-center justify-center rounded bg-red-500/10 hover:bg-red-500/20 text-xs text-red-400"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </aside>
  );
};
