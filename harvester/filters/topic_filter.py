#!/usr/bin/env python3
"""
Строгий фильтр тематической релевантности (Topic Keyword & Anti-Spam Filter):
Гарантирует, что в тематическую папку (например, «Слово Пацана», «Брат 2», «Наруто», «Toyota Mark II»)
попадают ТОЛЬКО стикеры и стикерпаки, реально относящиеся к этой теме, и блокирует:
1. Вирусный спам / стримеров / казино / чужие сериалы (Меллстрой, Пищеблок, 1win, Литвин, Хасбик и т.д.).
2. Безымянные случайные пины из поисковой выдачи, не содержащие ключевых слов темы.
3. Кондитерские распечатки («верх торта», «картинка на торт», «банка распечатка») и мокапы.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# Глобальный чёрный список вирусного мусора, стримеров, казино и чужих форсов,
# которые часто примешиваются в поисковую выдачу по русскоязычным стикерпакам.
UNRELATED_VIRAL_BLACKLIST: Tuple[str, ...] = (
    "меллстрой",
    "мелстрой",
    "mellstroy",
    "melstroy",
    "пищеблок",
    "pischeblok",
    "pishcheblok",
    "1win",
    "1вин",
    "betboom",
    "бетбум",
    "fonbet",
    "фонбет",
    "winline",
    "винлайн",
    "melbet",
    "мелбет",
    "parimatch",
    "париматч",
    "казино",
    "casino",
    "букмекер",
    "литвин",
    "litvin",
    "хасбик",
    "hasbulla",
    "инстасамка",
    "instasamka",
    "моргенштерн",
    "morgenshtern",
    "милохин",
    "некоглай",
    "nekoglai",
    "папич",
    "arthas",
    "мазеллов",
    "mazellov",
    "братишкин",
    "bratishkin",
    "эдвард бил",
    "влад а4",
    "бумага а4",
    "скибиди",
    "skibidi",
    "сигма бой",
    "верх торта",
    "картинка на торт",
    "печать на торт",
    "вафельная картинка",
    "сахарная картинка",
    "банка распечатка",
    "дизайн сторис",
)

# Служебные слова поисковых запросов, которые НЕ являются темой сами по себе
GENERIC_QUERY_STOPWORDS: Set[str] = {
    "sticker", "stickers", "pack", "packs", "sheet", "sheets", "printable",
    "vector", "die", "cut", "diecut", "white", "background", "single",
    "car", "cars", "decal", "decals", "auto", "bumper", "window",
    "logo", "logos", "design", "designs", "illustration", "art", "clipart",
    "funny", "cute", "cool", "meme", "memes", "retro", "vintage", "aesthetic",
    "sign", "signs", "poster", "posters", "print", "prints", "banner",
    "high", "resolution", "hd", "png", "jpg", "jpeg", "svg", "eps", "pdf",
    "flash", "slap", "holo", "vinyl", "collection", "set", "bundle",
    "стикер", "стикеры", "стикеров", "стикерпак", "стикерпаки",
    "наклейка", "наклейки", "наклеек", "лист", "листе", "листы",
    "авто", "машина", "машину", "машины", "стекло", "заднее", "бампер",
    "белый", "белом", "белая", "фон", "фоне", "прозрачный", "прозрачном",
    "печать", "печати", "макет", "макеты", "вектор", "векторный",
    "вывеска", "вывески", "баннер", "баннеры", "плакат", "постер", "табличка",
    "сериал", "фильм", "мультфильм", "аниме", "манга", "игра", "игры",
    "прикол", "приколы", "прикольные", "смешные", "крутые", "милые",
    "знак", "знаки", "надпись", "надписи", "логотип", "эмблема",
    "одиночный", "одиночные", "цельный", "цельные", "картинка", "картинки",
    "рисунок", "рисунки", "эскиз", "тату", "шаблон", "шаблоны",
    "для", "или", "под", "над", "про", "без", "как", "это", "все", "всё",
    "the", "and", "for", "with", "from", "into", "out", "off", "over",
}

# Общие/слабые слова, которые часто встречаются в случайных пинах (например, тег "Слова" в Pinterest)
# и не должны в одиночку подтверждать сложную тему, если у темы есть более конкретные слова.
WEAK_COMMON_TOKENS: Set[str] = {
    "слово", "слова", "слов",
    "игра", "игры", "игр", "game", "games",
    "дом", "house", "home",
    "мир", "world",
    "человек", "люди", "man", "men", "boy", "boys", "girl", "girls",
    "очень", "дела", "дело", "things",
    "во", "все", "bad", "better", "call", "good",
    "брат",  # проверяется через фразы "брат 2", "фильм брат", "бодров", "багров"
    "пацаны",
    "one", "two", "red", "blue", "black", "white", "new", "old",
    "big", "little", "hot", "top", "pro", "max", "full",
    "my", "high", "school", "line", "box", "need", "five",
}

# Дополнительные синонимы, имена персонажей и сленг для популярных тем,
# чтобы фильтр безошибочно пропускал релевантные пины с именами героев
TOPIC_EXTRA_SYNONYMS: Dict[str, List[str]] = {
    "слово_пацана": [
        "слово пацана", "слова пацана", "слово поцана", "словопацана", "спкна",
        "slovo patsana", "slovo pacana", "patsan", "вова адидас", "марат", "маратик",
        "андрей пальто", "кощей", "айгуль", "ералаш", "чушпан", "универсам",
    ],
    "брат_и_брат_2": [
        "фильм брат", "брат 2", "бодров", "сергей бодров", "данила багров",
        "багров", "в чем сила брат", "сила в правде", "bodrov", "danila bagrov",
    ],
    "король_и_шут_сериал": [
        "король и шут", "горшок", "горшенев", "михаил горшенев", "князь",
        "князев", "киш", "панк сказка", "korol i shut", "gorshok",
    ],
    "кухня_и_отель_элеон": [
        "сериал кухня", "отель элеон", "виктор баринов", "шеф баринов",
        "огузок", "максим лавров", "клод моне", "claude monet", "нагиев кухня",
    ],
    "вояк_и_гигачад_memes": [
        "wojak", "wojack", "gigachad", "chad", "doomer", "boomer", "zoomer",
        "вояк", "гигачад", "думер", "пепе",
    ],
    "лягушонок_пепе_pepe": [
        "pepe", "peepo", "pepega", "sadge", "monkas", "kekw", "apustaja",
        "пепе", "лягушонок пепе",
    ],
    "star_wars_звездные_войны": [
        "star wars", "darth vader", "r2d2", "c3po", "yoda", "grogu", "mandalorian",
        "skywalker", "obi wan", "ahsoka", "stormtrooper", "boba fett", "jedi", "sith",
        "звездные войны", "дарт вейдер", "йода", "мандалорец", "грогу",
    ],
    "the_boys_пацаны": [
        "the boys", "homelander", "billy butcher", "starlight", "soldier boy",
        "хоумлендер", "бутчер", "пацаны сериал", "vought",
    ],
    "breaking_bad_во_все_тяжкие": [
        "breaking bad", "heisenberg", "walter white", "jesse pinkman", "los pollos",
        "во все тяжкие", "хайзенберг", "уолтер уайт", "джесси пинкман",
    ],
    "stranger_things_очень_странные_дела": [
        "stranger things", "hellfire club", "demogorgon", "vecna", "eleven",
        "очень странные дела", "демогоргон", "хэллфайр",
    ],
    "squid_game_игра_в_кальмара": [
        "squid game", "игра в кальмара", "кальмар",
    ],
    "game_of_thrones_игра_престолов": [
        "game of thrones", "house of the dragon", "targaryen", "stark", "lannister",
        "игра престолов", "дом дракона", "таргариен", "джон сноу", "дейенерис",
    ],
    "s_t_a_l_k_e_r_сталкер": [
        "stalker", "s.t.a.l.k.e.r", "сталкер", "чернобыль", "монолит", "свобода", "долг", "сидорович",
    ],
    "боевая_классика_жигули": [
        "боевая классика", "жигули", "жига", "ваз 210", "2101", "2105", "2106", "2107",
        "lada", "zhiguli", "корч", "бк",
    ],
    "lada_priora_vesta_granta": [
        "приора", "priora", "веста", "vesta", "гранта", "granta", "калина", "kalina",
        "2114", "2112", "2110", "2109", "лада", "lada", "ваз", "опер стайл",
    ],
}

_RU_TO_EN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit_ru_to_en(text: str) -> str:
    out = []
    for ch in text.lower():
        out.append(_RU_TO_EN.get(ch, ch))
    return "".join(out)


def is_cyrillic_text(text: str) -> bool:
    return bool(re.search(r"[а-яёА-ЯЁ]", text))


def _normalize_text(text: str) -> str:
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"[_\-/.,:;!?()\[\]{}\"'«»„“”+#*@|]+", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    return low


def _russian_stem(word: str) -> str:
    """Отсекает типичные русские падежные/множественные окончания для надёжного поиска корня."""
    if len(word) <= 4 or not is_cyrillic_text(word):
        return word
    for suffix in (
        "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
        "ов", "ев", "ей", "ам", "ям", "ах", "ях", "ом", "ем", "ую", "юю",
        "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой",
        "а", "я", "ы", "и", "у", "ю", "е", "о",
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def build_topic_match_profile(topic: Dict[str, Any]) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Строит для темы профиль фильтрации:
    - strong_stems: уникальные корни/слова темы (достаточно 1 совпадения)
    - phrases: многословные фразы темы (достаточно 1 совпадения фразы)
    - weak_stems: общие слова темы (требуется >= 2 разных слабых слов, если нет сильных и фраз)
    """
    tname = str(topic.get("topic_name") or "")
    slug = str(topic.get("folder_slug") or tname).lower()
    queries: List[str] = list(topic.get("search_queries") or [])

    strong_stems: Set[str] = set()
    phrases: Set[str] = set()
    weak_stems: Set[str] = set()

    # 1. Проверяем словарь синонимов для известных тем
    for key_slug, syns in TOPIC_EXTRA_SYNONYMS.items():
        if key_slug in slug or key_slug in tname.lower():
            for s in syns:
                ns = _normalize_text(s)
                if " " in ns:
                    phrases.add(ns)
                    # Также добавляем укороченную по корням фразу
                    stemmed_phrase = " ".join(_russian_stem(w) for w in ns.split())
                    if len(stemmed_phrase) >= 5:
                        phrases.add(stemmed_phrase)
                elif len(ns) >= 3:
                    strong_stems.add(_russian_stem(ns))
                    if is_cyrillic_text(ns):
                        tr = translit_ru_to_en(ns)
                        if len(tr) >= 4:
                            strong_stems.add(tr)

    # 2. Извлекаем слова и фразы из topic_name и search_queries
    raw_sources = [tname.replace("_", " ")] + queries
    for src in raw_sources:
        norm = _normalize_text(src)
        tokens = [
            t for t in norm.split()
            if t not in GENERIC_QUERY_STOPWORDS and (len(t) >= 3 or t in ("cs", "mk", "dc", "vw", "it", "f1", "v8"))
        ]
        if len(tokens) >= 2:
            # Формируем осмысленные биграммы из соседних тематических слов
            for i in range(len(tokens) - 1):
                w1, w2 = tokens[i], tokens[i + 1]
                # Не строим двуязычную склейку из латиницы и кириллицы (как "boys пацаны")
                if is_cyrillic_text(w1) == is_cyrillic_text(w2):
                    phrases.add(f"{w1} {w2}")
                    phrases.add(f"{_russian_stem(w1)} {_russian_stem(w2)}")

        for tok in tokens:
            stem = _russian_stem(tok)
            if tok in WEAK_COMMON_TOKENS or stem in WEAK_COMMON_TOKENS:
                weak_stems.add(stem)
            else:
                strong_stems.add(stem)
                if is_cyrillic_text(tok):
                    tr = translit_ru_to_en(stem)
                    if len(tr) >= 4:
                        strong_stems.add(tr)

    return strong_stems, phrases, weak_stems


def is_candidate_relevant_to_topic(
    topic: Dict[str, Any],
    cand_title: str,
    cand_url: str = "",
) -> Tuple[bool, str]:
    """
    Проверяет, относится ли найденный макет (по его заголовку, описанию, доске, тегам и URL)
    к целевой теме `topic`.
    Возвращает (is_relevant, reason).
    """
    tname_norm = _normalize_text(str(topic.get("topic_name") or ""))
    queries_norm = " ".join(_normalize_text(q) for q in (topic.get("search_queries") or []))
    topic_full_context = f"{tname_norm} {queries_norm}"

    meta_norm = _normalize_text(f"{cand_title or ''} {cand_url or ''}")
    title_only_norm = _normalize_text(cand_title or "")

    # 1. Мгновенная блокировка вирусного спама, казино, стримеров и чужих сериалов (Пищеблок, Меллстрой и т.д.)
    for bad_viral in UNRELATED_VIRAL_BLACKLIST:
        if bad_viral in meta_norm and bad_viral not in topic_full_context:
            return False, f"blacklisted_offtopic:{bad_viral}"

    # 2. Если у кандидата вообще нет никакого осмысленного описания/названия (или только артикул/цифры)
    alpha_chars = re.sub(r"[^a-zа-яё]", "", title_only_norm)
    if len(alpha_chars) < 2:
        return False, "empty_or_numeric_pin_title"

    if re.match(r"^(арт|art|id|pin|img|image|photo|pic|sticker|stickers|наклейки|стикеры)\s*\d*$", title_only_norm):
        return False, f"generic_unlabeled_pin:{title_only_norm[:30]}"

    strong_stems, phrases, weak_stems = build_topic_match_profile(topic)

    # 3. Проверяем совпадение по точным фразам темы (например, "слово пацан", "брат 2", "король и шут", "one piece")
    for phr in phrases:
        if phr and phr in meta_norm:
            return True, f"matched_phrase:{phr}"

    # 4. Проверяем совпадение по сильным ключевым корням темы (например, "пацан", "бодров", "горшок", "naruto")
    meta_words = meta_norm.split()
    for stem in strong_stems:
        if len(stem) <= 3:
            # Короткие корни (cs, mk, бмв, ваз, киш) ищем только как целое слово или префикс слова <= 5 букв
            if any(w == stem or (w.startswith(stem) and len(w) <= len(stem) + 2) for w in meta_words):
                return True, f"matched_short_stem:{stem}"
        else:
            if stem in meta_norm:
                return True, f"matched_strong_stem:{stem}"

    # 5. Если сильных корней не нашлось, проверяем комбинацию слабых слов (>= 2 разных слабых корня)
    matched_weak = [w for w in weak_stems if any(mw.startswith(w) for mw in meta_words)]
    if len(matched_weak) >= 2:
        return True, f"matched_weak_combo:{'+'.join(matched_weak[:2])}"

    # Если у темы вообще не было сильных корней и фраз, а только 1 слабое слово
    if not strong_stems and not phrases and len(matched_weak) == 1:
        return True, f"matched_single_weak:{matched_weak[0]}"

    return False, f"offtopic_no_keyword_match(title={title_only_norm[:45]})"
