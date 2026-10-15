import React, { useState, useEffect } from "react";
import { Search, Tag, Image as ImageIcon, Loader2 } from "lucide-react";
import { api, type PopularTagItem } from "../../api/client";
import type { StickerSummary } from "../../types";
import { useDesignStore } from "../../store/designStore";

export const CatalogPanel: React.FC = () => {
  const [stickers, setStickers] = useState<StickerSummary[]>([]);
  const [popularTags, setPopularTags] = useState<PopularTagItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const { addPlacement, object } = useDesignStore();

  // Загрузка популярных тегов
  useEffect(() => {
    api
      .getPopularTags(20)
      .then((res) => setPopularTags(res.tags))
      .catch((err) => console.error("Ошибка загрузки тегов:", err));
  }, []);

  // Загрузка стикеров при смене фильтров
  useEffect(() => {
    let isCurrent = true;
    setIsLoading(true);

    api
      .getStickers({
        q: searchQuery.trim() || undefined,
        tag: selectedTag || undefined,
        page,
        per_page: 30,
      })
      .then((res) => {
        if (!isCurrent) return;
        setStickers(res.items ?? []);
        setTotal(res.meta.total_items);
      })
      .catch((err) => console.error("Ошибка загрузки каталога:", err))
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });

    return () => {
      isCurrent = false;
    };
  }, [searchQuery, selectedTag, page]);

  const handleCardClick = (sticker: StickerSummary) => {
    addPlacement({
      asset_id: sticker.asset_id,
      title: sticker.title,
      aspect: sticker.aspect ?? 1.0,
      width_mm: 55.0,
      x_mm: object ? object.width_mm / 2 : undefined,
      y_mm: object ? object.height_mm / 2 : undefined,
    });
  };

  const handleDragStart = (e: React.DragEvent<HTMLDivElement>, sticker: StickerSummary) => {
    e.dataTransfer.setData("application/json", JSON.stringify(sticker));
    e.dataTransfer.effectAllowed = "copy";
  };

  return (
    <aside className="w-80 h-full flex flex-col bg-[#1a1e27] border-r border-[#2d3748] text-slate-200 select-none">
      {/* Шапка каталога */}
      <div className="p-4 border-b border-[#2d3748] space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wide uppercase text-slate-400">
            Каталог стикеров
          </h2>
          <span className="text-xs text-slate-500">{total} шт.</span>
        </div>

        {/* Поиск */}
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Поиск по стикерам..."
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setPage(1);
            }}
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-[#242b38] border border-[#384357] rounded-md text-slate-100 placeholder-slate-400 focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Чипы популярных тегов */}
        {popularTags.length > 0 && (
          <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none text-[11px]">
            <button
              onClick={() => {
                setSelectedTag(null);
                setPage(1);
              }}
              className={`px-2 py-0.5 rounded whitespace-nowrap transition-colors ${
                selectedTag === null
                  ? "bg-blue-600 text-white font-medium"
                  : "bg-[#283142] text-slate-300 hover:bg-[#344055]"
              }`}
            >
              Все
            </button>
            {popularTags.slice(0, 10).map((t) => (
              <button
                key={t.tag}
                onClick={() => {
                  setSelectedTag(selectedTag === t.tag ? null : t.tag);
                  setPage(1);
                }}
                className={`px-2 py-0.5 rounded whitespace-nowrap transition-colors flex items-center gap-1 ${
                  selectedTag === t.tag
                    ? "bg-blue-600 text-white font-medium"
                    : "bg-[#283142] text-slate-300 hover:bg-[#344055]"
                }`}
              >
                <Tag className="w-2.5 h-2.5 opacity-60" />
                {t.tag}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Сетка стикеров */}
      <div className="flex-1 overflow-y-auto p-3">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center h-48 text-slate-400 gap-2">
            <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
            <span className="text-xs">Загрузка стикеров...</span>
          </div>
        ) : stickers.length === 0 ? (
          <div className="text-center py-12 text-slate-500 text-xs">
            Ничего не найдено
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2.5">
            {stickers.map((sticker) => {
              const thumbUrl = api.getStickerImageUrl(sticker.asset_id, "thumb");
              return (
                <div
                  key={sticker.asset_id}
                  draggable
                  onDragStart={(e) => handleDragStart(e, sticker)}
                  onClick={() => handleCardClick(sticker)}
                  className="group relative flex flex-col items-center p-2 rounded-lg bg-[#242b38] border border-[#344055] hover:border-blue-500 hover:bg-[#2c3547] cursor-pointer transition-all shadow-sm"
                  title="Кликните для добавления или перетащите на холст"
                >
                  <div className="w-full aspect-square flex items-center justify-center p-1 bg-[#1a1e27] rounded-md overflow-hidden">
                    <img
                      src={thumbUrl}
                      alt={sticker.title}
                      loading="lazy"
                      className="max-w-full max-h-full object-contain filter drop-shadow group-hover:scale-105 transition-transform"
                      onError={(e) => {
                        (e.target as HTMLElement).style.display = "none";
                      }}
                    />
                  </div>
                  <span className="mt-1.5 text-[11px] font-medium text-slate-200 truncate w-full text-center">
                    {sticker.title}
                  </span>
                  {sticker.topic_name && (
                    <span className="text-[9px] text-slate-500 truncate w-full text-center">
                      {sticker.topic_name}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Нижняя подсказка */}
      <div className="p-2.5 border-t border-[#2d3748] bg-[#161a22] text-[11px] text-slate-400 flex items-center gap-1.5 justify-center">
        <ImageIcon className="w-3.5 h-3.5 text-blue-400" />
        <span>Клик или перетаскивание на крышку</span>
      </div>
    </aside>
  );
};
