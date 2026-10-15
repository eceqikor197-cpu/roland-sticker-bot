import React from "react";
import { useDesignStore } from "../../store/designStore";
import { AlertTriangle, CheckCircle, Info } from "lucide-react";
import { get_sticker_polygon, is_sticker_inside_object } from "../../geometry";

export const StatsBar: React.FC = () => {
  const { object, placements, selectedId } = useDesignStore();

  if (!object) return null;

  const lidAreaMm2 = object.width_mm * object.height_mm;

  // Расчёт примерной площади стикеров и процента покрытия
  let totalStickersAreaMm2 = 0;
  let outOfBoundsCount = 0;

  for (const p of placements) {
    const aspect = p.aspect > 0 ? p.aspect : 1.0;
    const h_mm = p.width_mm / aspect;
    totalStickersAreaMm2 += p.width_mm * h_mm;

    const poly = get_sticker_polygon(p.x_mm, p.y_mm, p.width_mm, h_mm, p.rotation_deg);
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
    if (!isInside) {
      outOfBoundsCount++;
    }
  }

  const coveragePercent = Math.min(100, Math.round((totalStickersAreaMm2 / lidAreaMm2) * 100));
  const selectedPlacement = placements.find((p) => p.id === selectedId);

  return (
    <footer className="h-8 bg-[#161a22] border-t border-[#2d3748] px-4 flex items-center justify-between text-[11px] text-slate-400 select-none">
      {/* Левая сторона: статус макета и количество стикеров */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <Info className="w-3.5 h-3.5 text-blue-400" />
          <span>Стикеров:</span>
          <span className="font-semibold text-slate-200">
            {placements.length} / 150
          </span>
        </div>

        <div className="h-3 w-px bg-[#2d3748]" />

        <div>
          <span>Покрытие крышки: </span>
          <span className="font-semibold text-slate-200">{coveragePercent}%</span>
        </div>

        <div className="h-3 w-px bg-[#2d3748]" />

        {outOfBoundsCount > 0 ? (
          <div className="flex items-center gap-1 text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>{outOfBoundsCount} стикер(а) за безопасной зоной</span>
          </div>
        ) : placements.length > 0 ? (
          <div className="flex items-center gap-1 text-emerald-400">
            <CheckCircle className="w-3.5 h-3.5" />
            <span>Все стикеры в безопасной зоне</span>
          </div>
        ) : null}
      </div>

      {/* Правая сторона: параметры выделенного объекта и подсказка по горячим клавишам */}
      <div className="flex items-center gap-4 text-slate-400">
        {selectedPlacement ? (
          <div className="flex items-center gap-2 text-slate-300">
            <span>X: {selectedPlacement.x_mm} мм</span>
            <span>Y: {selectedPlacement.y_mm} мм</span>
            <span>Ш: {selectedPlacement.width_mm} мм</span>
            <span>Угол: {selectedPlacement.rotation_deg}°</span>
            {selectedPlacement.locked && (
              <span className="text-amber-400 font-semibold">[Заблокирован]</span>
            )}
          </div>
        ) : (
          <div className="text-[10px] text-slate-500">
            Стрелки: 1 мм • Shift + стрелки: 10 мм • Cmd+D: дубликат • Del: удалить
          </div>
        )}
      </div>
    </footer>
  );
};
