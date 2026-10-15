import React from "react";
import { CheckCircle2, AlertTriangle, XCircle, X } from "lucide-react";
import type { DesignValidationResult } from "../types";

interface ValidationModalProps {
  isOpen: boolean;
  onClose: () => void;
  result: DesignValidationResult | null;
  isLoading: boolean;
  isObjectVerified?: boolean;
}

export const ValidationModal: React.FC<ValidationModalProps> = ({
  isOpen,
  onClose,
  result,
  isLoading,
  isObjectVerified = false,
}) => {
  if (!isOpen) return null;

  const errors = result?.errors ?? [];
  const warnings = result?.warnings ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-[#1a1e27] border border-[#2d3748] rounded-xl shadow-2xl overflow-hidden flex flex-col text-slate-200">
        {/* Шапка модального окна */}
        <div className="px-5 py-4 border-b border-[#2d3748] flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">
            Результат проверки макета
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-[#283142]"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Контент */}
        <div className="p-5 space-y-4">
          {isLoading ? (
            <div className="py-8 text-center text-sm text-slate-400">
              Выполняется инженерная проверка геометрии макета...
            </div>
          ) : result ? (
            <>
              {/* Статус валидности */}
              <div
                className={`p-3.5 rounded-lg border flex items-center gap-3 ${
                  result.valid
                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                    : "bg-red-500/10 border-red-500/30 text-red-300"
                }`}
              >
                {result.valid ? (
                  <CheckCircle2 className="w-6 h-6 text-emerald-400 flex-shrink-0" />
                ) : (
                  <XCircle className="w-6 h-6 text-red-400 flex-shrink-0" />
                )}
                <div>
                  <div className="font-semibold text-sm">
                    {result.valid ? "Макет валиден" : "Обнаружены ошибки"}
                  </div>
                  <div className="text-xs opacity-90">
                    Стикеров в макете: {result.placements_count} шт.
                  </div>
                </div>
              </div>

              {/* Ошибки */}
              {errors.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold uppercase tracking-wider text-red-400">
                    Критические ошибки:
                  </div>
                  <ul className="space-y-1 text-xs text-red-300 bg-red-950/20 border border-red-900/40 p-2.5 rounded-md">
                    {errors.map((err, i) => (
                      <li key={i} className="flex items-start gap-1.5">
                        <span className="text-red-500">•</span>
                        <span>{err}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Предупреждения */}
              {warnings.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    <span>Инженерные предупреждения:</span>
                  </div>
                  <ul className="space-y-1 text-xs text-amber-200/90 bg-amber-950/20 border border-amber-900/40 p-2.5 rounded-md max-h-48 overflow-y-auto">
                    {warnings.map((warn, i) => (
                      <li key={i} className="flex items-start gap-1.5">
                        <span className="text-amber-500">•</span>
                        <span>{warn}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Статус верификации модели */}
              <div className="text-[11px] text-slate-400 bg-[#242b38] p-2.5 rounded-md">
                Габариты корпуса ноутбука:{" "}
                <span className={isObjectVerified ? "text-emerald-400 font-medium" : "text-amber-400 font-medium"}>
                  {isObjectVerified ? "Верифицированы линейкой" : "Предварительные (официальные спеки)"}
                </span>
              </div>
            </>
          ) : (
            <div className="text-sm text-slate-400">Нет данных проверки</div>
          )}
        </div>

        {/* Подвал */}
        <div className="px-5 py-3 border-t border-[#2d3748] bg-[#161a22] flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs font-medium rounded-md bg-[#242b38] hover:bg-[#344055] text-slate-200 transition-colors"
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
};
