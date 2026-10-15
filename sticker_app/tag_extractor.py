"""
Модуль извлечения, очистки и нормализации тегов для каталога макетов.
Удаляет технические префиксы, стоп-слова (RU/EN), нормализует регистр
и формирует до 12 качественных тегов на макет.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Sequence, Set

# Стоп-слова для исключения служебных терминов и служебных частей речи
STOP_WORDS: Set[str] = {
    # Термины форматов и стикеров (RU)
    "стикер", "стикеры", "стикеров", "стикерам", "стикерами", "стикерах",
    "стикерпак", "стикерпаки", "стикерпаков",
    "наклейка", "наклейки", "наклеек", "наклейкам", "наклейками", "наклейках",
    "набор", "наборы", "набора", "наборов", "пак", "паки", "паков",
    "одиночные", "одиночный", "одиночная", "одиночное",
    "коллекция", "печать", "контур", "рез", "резка",
    "вектор", "векторный", "векторная", "арт", "клипарт",
    "фото", "рисунок", "изображение", "дизайн", "макет",
    # Термины форматов и стикеров (EN)
    "sticker", "stickers", "stickerpack", "decal", "decals",
    "pack", "packs", "single", "sheet", "vector",
    "cutcontour", "perfcutcontour", "cut", "contour", "transparent",
    "clipart", "art", "free", "download", "hd", "print", "printready",
    "png", "eps", "jpg", "jpeg", "svg", "webp", "pdf",
    # Служебные слова русского языка
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "она", "так", "его", "но", "да", "ты", "к", "ко", "у", "же", "вы", "за", "бы",
    "по", "только", "ее", "мне", "было", "вот", "от", "ото", "меня", "еще", "нет",
    "о", "об", "обо", "из", "изо", "ему", "теперь", "когда", "даже", "ну", "вдруг",
    "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей", "может",
    "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их", "чем",
    "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под",
    "будет", "ж", "тогда", "кто", "этот", "того", "потому", "этого", "какой",
    "совсем", "ним", "здесь", "этом", "один", "почти", "мой", "тем", "чтобы",
    "нее", "сейчас", "были", "куда", "зачем", "всех", "никогда", "можно", "при",
    "наконец", "два", "об", "другой", "хоть", "после", "над", "больше", "тот",
    "через", "эти", "нас", "про", "всего", "них", "какая", "много", "разве",
    "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
    "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда", "конечно",
    "всю", "между", "это", "чья", "чье", "чьи",
    # Служебные слова английского языка
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "up", "about", "into", "over", "after", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "but", "if", "then", "so", "as", "out", "no", "not", "only", "own", "same",
    "too", "very", "can", "will", "just", "should", "now",
}

# Регулярные выражения
_PREFIX_RE = re.compile(r"^\d+_+")
_CLEAN_PUNCT_RE = re.compile(r"[\_\-\/\.,;:!?\(\)\[\]\"\'\`~@#\$%\^&\*+=\<\>\\|]")
_WORDS_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")


def clean_text_field(text: str) -> str:
    """Удаляет числовые префиксы категорий и заменяет пунктуацию пробелами."""
    if not text:
        return ""
    # Удаляем ведущие префиксы вроде '03_', '01_'
    cleaned = _PREFIX_RE.sub("", text)
    # Заменяем знаки пунктуации, дефисы, подчеркивания на пробелы
    cleaned = _CLEAN_PUNCT_RE.sub(" ", cleaned)
    return cleaned


def parse_search_queries(raw_queries: Any) -> List[str]:
    """Безопасно преобразует search_queries_json или список в плоский список строк."""
    if not raw_queries:
        return []
    if isinstance(raw_queries, list):
        return [str(q) for q in raw_queries if q]
    if isinstance(raw_queries, str):
        try:
            parsed = json.loads(raw_queries)
            if isinstance(parsed, list):
                return [str(q) for q in parsed if q]
        except (json.JSONDecodeError, TypeError):
            return [raw_queries]
    return []


def extract_tags(
    category_lvl1: str = "",
    category_lvl2: str = "",
    topic_name: str = "",
    folder_slug: str = "",
    title: str = "",
    search_queries: Optional[Sequence[str] | str] = None,
    max_tags: int = 12,
) -> List[str]:
    """
    Извлекает и ранжирует качественные теги для макета.
    Приоритет:
    1. title (наиболее специфичный)
    2. topic_name
    3. folder_slug
    4. category_lvl2
    5. search_queries
    6. category_lvl1
    """
    queries_list = parse_search_queries(search_queries)

    # Формируем цепочку текстов в порядке убывания специфичности
    sources: List[str] = [title, topic_name, folder_slug, category_lvl2]
    sources.extend(queries_list)
    sources.append(category_lvl1)

    tags: List[str] = []
    seen: Set[str] = set()

    for src in sources:
        cleaned = clean_text_field(src)
        if not cleaned:
            continue

        words = _WORDS_RE.findall(cleaned)
        for w in words:
            token = w.lower().strip()
            # Пропуск слишком коротких слов
            if len(token) < 2:
                continue
            # Пропуск коротких чисел (кроме годов/значимых чисел вроде '2077', '4k')
            if token.isdigit() and len(token) < 3:
                continue
            # Пропуск стоп-слов
            if token in STOP_WORDS:
                continue
            # Дедупликация
            if token not in seen:
                seen.add(token)
                tags.append(token)
                if len(tags) >= max_tags:
                    return tags

    return tags
