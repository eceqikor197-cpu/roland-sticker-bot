#!/usr/bin/env python3
"""
Стартовый каталог из 500+ поисковых тем, разбитых по 3-уровневой таксономии:
Уровень 1 (Тип продукции) -> Уровень 2 (Категория) -> Уровень 3 (Тема / Фандом / Марка авто).
Включает генератор поисковых запросов под каждый источник и автоматический классификатор
для новых тем, заказанных пользователем в боте или найденных через граф Pinterest.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from harvester.database import upsert_topic

# Таксономия: {category_lvl1: {category_lvl2: [(topic_folder_name, [search_keywords...]), ...]}}
SEED_CATALOG: Dict[str, Dict[str, List[Tuple[str, List[str]]]]] = {
    "01_Стикерпаки": {
        "Сериалы_и_Кино": [
            ("Breaking_Bad_Во_все_тяжкие", ["breaking bad sticker pack", "heisenberg stickers sheet", "breaking bad stickers"]),
            ("Better_Call_Saul", ["better call saul sticker pack", "saul goodman stickers sheet"]),
            ("The_Boys_Пацаны", ["the boys sticker pack", "homelander butcher stickers sheet"]),
            ("Stranger_Things_Очень_странные_дела", ["stranger things sticker pack", "hellfire club stickers sheet"]),
            ("Rick_and_Morty_Рик_и_Морти", ["rick and morty sticker pack", "rick and morty stickers sheet"]),
            ("Gravity_Falls_Гравити_Фолз", ["gravity falls sticker pack", "mabel dipper stickers sheet"]),
            ("The_Office_Офис", ["the office us sticker pack", "michael scott stickers sheet"]),
            ("Friends_Друзья", ["friends tv show sticker pack", "friends stickers sheet"]),
            ("Game_of_Thrones_Игра_Престолов", ["game of thrones sticker pack", "house of the dragon stickers"]),
            ("Peaky_Blinders_Острые_Козырьки", ["peaky blinders sticker pack", "thomas shelby stickers"]),
            ("Sopranos_Клан_Сопрано", ["the sopranos sticker pack", "tony soprano stickers"]),
            ("Pulp_Fiction_Криминальное_Чтиво", ["pulp fiction sticker pack", "tarantino movies stickers sheet"]),
            ("Fight_Club_Бойцовский_Клуб", ["fight club sticker pack", "tyler durden stickers sheet"]),
            ("Star_Wars_Звездные_Войны", ["star wars sticker pack", "mandalorian grogu stickers sheet"]),
            ("Harry_Potter_Гарри_Поттер", ["harry potter sticker pack", "hogwarts stickers sheet"]),
            ("Marvel_Avengers_Марвел", ["marvel sticker pack", "deadpool wolverine stickers sheet", "spiderman sticker pack"]),
            ("DC_Batman_Joker", ["batman joker sticker pack", "dc comics stickers sheet"]),
            ("Wednesday_Уэнсдей", ["wednesday addams sticker pack", "wednesday stickers sheet"]),
            ("Arcane_Аркейн", ["arcane jinx vi sticker pack", "arcane league of legends stickers"]),
            ("Supernatural_Сверхъестественное", ["supernatural sticker pack", "winchester impala stickers"]),
            ("Sherlock_Шерлок", ["sherlock bbc sticker pack", "sherlock holmes stickers"]),
            ("Twin_Peaks_Твин_Пикс", ["twin peaks sticker pack", "david lynch stickers"]),
            ("Cyberpunk_Edgerunners", ["cyberpunk edgerunners sticker pack", "rebecca lucy stickers"]),
            ("Squid_Game_Игра_в_кальмара", ["squid game sticker pack", "squid game stickers sheet"]),
            ("Back_to_the_Future_Назад_в_будущее", ["back to the future sticker pack", "delorean marty mcfly stickers"]),
            ("Matrix_Матрица", ["the matrix sticker pack", "neo matrix stickers"]),
            ("Lord_of_the_Rings_Властелин_Колец", ["lord of the rings sticker pack", "lotr stickers sheet"]),
            ("Pirates_of_the_Caribbean", ["pirates of the caribbean sticker pack", "jack sparrow stickers"]),
            ("Shrek_Шрек", ["shrek sticker pack", "shrek meme stickers sheet"]),
            ("SpongeBob_Губка_Боб", ["spongebob sticker pack", "spongebob meme stickers sheet"]),
            ("Simpsons_Симпсоны", ["the simpsons sticker pack", "homer bart simpson stickers sheet"]),
            ("Futurama_Футурама", ["futurama sticker pack", "bender futurama stickers sheet"]),
            ("South_Park_Южный_Парк", ["south park sticker pack", "cartman kenny stickers sheet"]),
            ("Adventure_Time_Время_Приключений", ["adventure time sticker pack", "finn and jake stickers sheet"]),
            ("Scooby_Doo_Скуби_Ду", ["scooby doo sticker pack", "mystery machine stickers"]),
            ("Teenage_Mutant_Ninja_Turtles_Черепашки_Ниндзя", ["tmnt ninja turtles sticker pack", "tmnt stickers sheet"]),
            ("Brbad_BCS_Universe", ["los pollos hermanos stickers", "better call saul sticker sheet"]),
            ("Брат_и_Брат_2", ["фильм брат бодров стикерпак", "брат 2 стикеры наклейки"]),
            ("Король_и_Шут_Сериал", ["король и шут стикерпак", "горшок киш стикеры наклейки"]),
            ("Слово_Пацана", ["слово пацана стикерпак", "слово пацана стикеры"]),
            ("Кухня_и_Отель_Элеон", ["сериал кухня стикеры", "баринов кухня стикерпак"]),
        ],
        "Аниме_и_Манга": [
            ("Naruto_Наруто", ["naruto sticker pack", "akatsuki itachi stickers sheet"]),
            ("One_Piece_Ван_Пис", ["one piece sticker pack", "luffy zoro stickers sheet"]),
            ("Attack_on_Titan_Атака_Титанов", ["attack on titan sticker pack", "levi eren stickers sheet"]),
            ("Jujutsu_Kaisen_Магическая_Битва", ["jujutsu kaisen sticker pack", "gojo satoru sukuna stickers sheet"]),
            ("Demon_Slayer_Клинок_рассекающий_демонов", ["demon slayer kimetsu sticker pack", "nezuko tanjiro stickers sheet"]),
            ("Chainsaw_Man_Человек_Бензопила", ["chainsaw man sticker pack", "pochita power makima stickers sheet"]),
            ("Death_Note_Тетрадь_Смерти", ["death note sticker pack", "ryuk lawliet stickers sheet"]),
            ("Berserk_Берсерк", ["berserk guts sticker pack", "brand of sacrifice stickers"]),
            ("Initial_D_Инициал_Ди", ["initial d ae86 sticker pack", "akina speed stars stickers"]),
            ("Evangelion_Евангелион", ["neon genesis evangelion sticker pack", "rei asuka eva01 stickers"]),
            ("Bleach_Блич", ["bleach ichigo sticker pack", "bleach anime stickers sheet"]),
            ("Hunter_x_Hunter", ["hunter x hunter sticker pack", "killua hisoka stickers sheet"]),
            ("JoJo_Невероятные_приключения_ДжоДжо", ["jojo bizarre adventure sticker pack", "jojo stickers sheet"]),
            ("Tokyo_Ghoul_Токийский_Гуль", ["tokyo ghoul kaneki sticker pack", "tokyo ghoul stickers sheet"]),
            ("My_Hero_Academia_Моя_Геройская_Академия", ["my hero academia sticker pack", "bakugo deku stickers sheet"]),
            ("One_Punch_Man_Ванпанчмен", ["one punch man saitama sticker pack", "saitama stickers sheet"]),
            ("Spy_x_Family_Семья_Шпиона", ["spy x family anya sticker pack", "anya forger stickers sheet"]),
            ("Studio_Ghibli_Миядзаки", ["studio ghibli totoro sticker pack", "spirited away calcifer stickers sheet"]),
            ("Cowboy_Bebop_Ковбой_Бибоп", ["cowboy bebop sticker pack", "spike spiegel stickers"]),
            ("Fullmetal_Alchemist_Стальной_Алхимик", ["fullmetal alchemist sticker pack", "edward elric stickers"]),
            ("Solo_Leveling_Поднятие_уровня", ["solo leveling sung jinwoo sticker pack", "solo leveling stickers"]),
            ("Frieren_Фрирен", ["frieren beyond journey end sticker pack", "frieren stickers"]),
            ("Blue_Lock_Синяя_Тюрьма", ["blue lock anime sticker pack", "isagi bachira stickers"]),
            ("Haikyuu_Волейбол", ["haikyuu sticker pack", "karasuno stickers sheet"]),
            ("Sailor_Moon_Сейлор_Мун", ["sailor moon sticker pack", "sailor moon aesthetic stickers"]),
        ],
        "Игры_и_Гейминг": [
            ("GTA_Grand_Theft_Auto", ["gta san andreas sticker pack", "gta vice city gta 5 stickers sheet"]),
            ("Counter_Strike_CS2", ["csgo cs2 sticker pack", "counter strike holo stickers sheet"]),
            ("Dota_2_Дота", ["dota 2 sticker pack", "dota heroes stickers sheet"]),
            ("Minecraft_Майнкрафт", ["minecraft sticker pack", "minecraft creeper stickers sheet"]),
            ("Witcher_3_Ведьмак", ["the witcher 3 geralt sticker pack", "witcher wolf school stickers"]),
            ("Cyberpunk_2077", ["cyberpunk 2077 samurai sticker pack", "night city stickers sheet"]),
            ("Red_Dead_Redemption_2", ["red dead redemption 2 arthur morgan sticker pack", "rdr2 stickers"]),
            ("Elden_Ring_Dark_Souls", ["dark souls solaire elden ring sticker pack", "praise the sun stickers"]),
            ("Fallout_Фоллаут", ["fallout vault boy nuka cola sticker pack", "fallout stickers sheet"]),
            ("Skyrim_TES", ["skyrim elder scrolls sticker pack", "skyrim dragonborn stickers"]),
            ("Brawl_Stars", ["brawl stars sticker pack", "brawl stars pins stickers sheet"]),
            ("Genshin_Impact", ["genshin impact sticker pack", "paimon hu tao stickers sheet"]),
            ("Valorant_Валорант", ["valorant spray sticker pack", "valorant agents stickers sheet"]),
            ("League_of_Legends", ["league of legends sticker pack", "lol emotes stickers sheet"]),
            ("Need_for_Speed_NFS", ["need for speed underground most wanted sticker pack", "nfs stickers"]),
            ("Five_Nights_at_Freddys_FNAF", ["fnaf five nights at freddys sticker pack", "fnaf stickers sheet"]),
            ("Undertale_Андертейл", ["undertale sans sticker pack", "undertale stickers sheet"]),
            ("Hollow_Knight", ["hollow knight hornet sticker pack", "hollow knight stickers sheet"]),
            ("cuphead", ["cuphead mugman sticker pack", "cuphead vintage cartoon stickers"]),
            ("Super_Mario_Nintendo", ["super mario bros nintendo sticker pack", "mario yoshi kirby stickers"]),
            ("Sonic_the_Hedgehog", ["sonic the hedgehog shadow sticker pack", "sonic stickers sheet"]),
            ("Resident_Evil", ["resident evil umbrella corp sticker pack", "leon kennedy stickers"]),
            ("Silent_Hill", ["silent hill halo of the sun sticker pack", "pyramid head stickers"]),
            ("Mortal_Kombat", ["mortal kombat scorpion subzero sticker pack", "mk dragon stickers"]),
            ("S_T_A_L_K_E_R_Сталкер", ["сталкер группировки стикерпак", "stalker chernobyl stickers pack"]),
            ("Atomic_Heart", ["atomic heart twins стикерпак", "atomic heart stickers"]),
        ],
        "Мемы_Котики_и_Поп_Культура": [
            ("Котики_Мемы_Cat_Memes", ["funny cat memes sticker pack", "crying cat banana cat stickers sheet", "мемные коты стикерпак"]),
            ("Собаки_Сиба_Ину_Doge", ["doge shiba inu cheems sticker pack", "funny dogs stickers sheet"]),
            ("Капибара_Capybara", ["capybara cute funny sticker pack", "капибара стикерпак наклейки"]),
            ("Еноты_и_Опоссумы", ["raccoon possum trash meme sticker pack", "енот стикерпак"]),
            ("Лягушонок_Пепе_Pepe", ["pepe the frog twitch emotes sticker pack", "pepe meme stickers"]),
            ("Вояк_и_Гигачад_Memes", ["wojak gigachad meme sticker pack", "internet memes stickers sheet"]),
            ("IT_Программисты_Linux", ["programmer coding developer sticker pack", "linux python git stickers sheet"]),
            ("Рок_и_Метал_Группы", ["rock band metallica nirvana slipknot sticker pack", "punk rock stickers sheet"]),
            ("Хип_Хоп_и_Рэп", ["rap hip hop tupac eminem travis scott sticker pack", "90s hip hop stickers"]),
            ("Скейт_и_Стритвир_Supreme", ["skateboard streetwear thrasher vans sticker pack", "hypebeast stickers sheet"]),
            ("Тату_Олдскул_Traditional", ["traditional old school tattoo flash sticker pack", "american traditional tattoo stickers"]),
            ("Космос_и_НАСА", ["space nasa astronaut galaxy sticker pack", "astronaut planet stickers sheet"]),
            ("Ретро_80е_90е_Vaporwave", ["retro 80s 90s vaporwave synthwave sticker pack", "nostalgia 90s stickers"]),
            ("Хэллоуин_и_Хоррор", ["halloween spooky gothic horror sticker pack", "slasher horror movies stickers"]),
            ("Кофе_и_Еда_Каваи", ["cute food boba coffee sticker pack", "kawaii ramen sushi stickers sheet"]),
        ],
    },
    "02_Авто_Наклейки": {
        "JDM_и_Японские_Авто": [
            ("JDM_Стикербомбинг_Япония", ["jdm car stickers pack vector", "japanese car decal wakaba sadboiz", "наклейки на авто jdm"]),
            ("Toyota_Mark_Chaser_Supra", ["toyota supra mk4 chaser jzx100 car decal sticker", "trd toyota racing sticker"]),
            ("Nissan_Skyline_GTR_Silvia", ["nissan gtr r34 silvia s15 nismo car sticker decal", "nismo jdm sticker"]),
            ("Honda_Civic_VTEC_TypeR", ["honda civic vtec kicked in yo sticker decal", "mugen spoon sports jdm sticker"]),
            ("Subaru_Impreza_WRX_STI", ["subaru wrx sti rally pig boxer car sticker decal", "subaru jdm decal"]),
            ("Mazda_RX7_Miata_Rotary", ["mazda miata mx5 rx7 rotary brap car sticker decal", "mazdaspeed sticker"]),
            ("Mitsubishi_Lancer_Evolution", ["mitsubishi lancer evo ralliart car sticker decal", "lancer evolution decal"]),
            ("Drift_Slap_Stickers_Дрифт", ["drift slap stickers japanese kanji car window", "night runner drift team sticker"]),
            ("Японские_Иероглифы_Кандзи", ["japanese kanji car decal vector", "initial d fujiwara tofu shop decal"]),
            ("Аниме_Пеeker_на_Стекло_Авто", ["anime peeker car window sticker", "waifu peeker decal printable"]),
        ],
        "Немецкие_и_Европейские_Авто": [
            ("BMW_M_Power_Performance", ["bmw m performance e34 e36 e39 e46 car sticker decal", "bmw motorsport sticker vector"]),
            ("Mercedes_AMG_Brabus", ["mercedes amg brabus car window sticker decal", "affalterbach amg decal"]),
            ("Audi_Quattro_RS_Sline", ["audi quattro sport rs car sticker decal", "dope audi rings sticker"]),
            ("Volkswagen_GTI_VAG", ["volkswagen golf gti vag car sticker decal", "vw dub sticker pack"]),
            ("Porsche_911_Motorsport", ["porsche 911 martini racing car decal sticker", "porsche motorsport sticker"]),
            ("Volvo_Prancing_Moose", ["volvo prancing moose turbo brick car sticker", "volvo racing decal"]),
            ("Nurburgring_Трассы", ["nurburgring track outline car sticker decal", "nordschleife sticker"]),
        ],
        "Отечественные_ВАЗ_Lada_Боевая_Классика": [
            ("Боевая_Классика_Жигули", ["боевая классика жигули наклейки на авто", "ваз 2107 2105 дрифт стикеры"]),
            ("Lada_Priora_Vesta_Granta", ["лада приора веста опер стайл наклейка", "lada sport bpan наклейки на стекло"]),
            ("ГАЗ_Волга_Газель", ["газель волга наклейки приколы на авто", "газ автоклуб наклейка"]),
            ("УАЗ_Патриот_Буханка_4x4", ["уаз буханка патриот оффроуд наклейки", "uaz 4x4 offroad sticker"]),
            ("Нива_4x4_Lada_Legend", ["нива 4х4 бездорожье наклейка на авто", "lada niva offroad decal"]),
        ],
        "Приколы_Надписи_и_Инфо_на_Стекло": [
            ("Авто_Юмор_и_Приколы", ["прикольные наклейки на авто на заднее стекло", "funny car bumper stickers memes", "смешные надписи на машину"]),
            ("Fake_Taxi_Smotra_Стритрейсинг", ["fake taxi car sticker logo", "street racing night hunter car decal"]),
            ("Ребенок_в_Машине_Baby_on_Board", ["baby on board funny car sticker", "ребенок в машине наклейка вектор"]),
            ("Начинающий_Водитель_Учебная", ["начинающий водитель восклицательный знак прикол наклейка", "new driver please be patient car sticker"]),
            ("Собака_Кот_в_Машине", ["dog on board car window sticker", "кот саймона наклейка на авто бак"]),
            ("Оффроуд_Трофи_Грязь_4x4", ["4x4 offroad mud compass mountain car decal", "внедорожник грязь оффроуд наклейка"]),
            ("Рыбалка_и_Охота_на_Авто", ["рыбалка охота наклейки на машину", "fishing hunting car window decal vector"]),
            ("Мото_Байкерские_Наклейки", ["motorcycle biker skull helmet sticker pack", "harley kawasaki yamaha honda moto stickers"]),
            ("Гоночные_Бренды_Тюнинг_Спонсоры", ["racing sponsor logos sticker sheet brembo hks sparco bride", "car tuning brands decal pack"]),
        ],
    },
    "03_Одиночные_Стикеры": {
        "Эстетика_Арт_и_Дизайн": [
            ("Киберпанк_и_Неон", ["cyberpunk neon glitch sticker design", "futuristic mecha sticker illustration"]),
            ("Черепа_Готика_Dark_Art", ["skull grim reaper dark art sticker", "gothic skeleton tarot sticker illustration"]),
            ("Космонавты_и_Пришельцы", ["astronaut alien ufo trippy sticker design", "psychedelic space sticker"]),
            ("Драконы_и_Мифология", ["japanese dragon irezumi sticker design", "fantasy dragon sticker illustration"]),
            ("Самураи_и_Они_Маски", ["oni mask samurai katana sticker design", "japanese demon hannya sticker"]),
            ("Ретро_Винтажные_Этикетки", ["vintage retro badge sticker design", "70s groovy retro sticker"]),
            ("Стрит_Арт_Граффити", ["graffiti character street art slap sticker", "urban graffiti sticker design"]),
            ("Аниме_Чиби_Персонажи", ["chibi anime character sticker png", "cute anime sticker die cut"]),
            ("Тачки_Мультяшные_Chibi_Cars", ["cartoon chibi jdm car sticker illustration", "tuner car vector sticker"]),
            ("Крипта_Биткоин_Финансы", ["bitcoin crypto monopoly money sticker", "wallstreetbets hustler sticker"]),
        ],
    },
    "04_Баннеры_и_Вывески": {
        "Автобизнес_Гараж_и_Сервис": [
            ("Автосервис_СТО_Шиномонтаж", ["шиномонтаж автосервис вывеска баннер макет", "car repair garage vintage sign poster"]),
            ("Ретро_Постеры_в_Гараж_PinUp", ["vintage garage motor oil tin sign poster", "classic car garage wall poster"]),
            ("Автомойка_Детейлинг", ["детейлинг автомойка логотип баннер", "car wash detailing sign vector"]),
        ],
        "Таблички_Предупреждения_и_Постеры": [
            ("Знаки_Безопасности_Осторожно_Приколы", ["warning danger funny metal sign sticker", "осторожно злая собака табличка прикол"]),
            ("Кафе_Кофейня_Бургерная_Постеры", ["vintage coffee shop poster sign illustration", "retro diner burger neon sign"]),
            ("Барбершоп_и_Тату_Студия", ["barbershop tattoo studio vintage poster sign", "old school barber sign"]),
            ("Мотивационные_и_Кино_Постеры", ["minimalist movie poster retro print", "japanese vintage travel poster"]),
        ],
    },
}


def build_expanded_Topics() -> List[Tuple[str, str, str, List[str], int]]:
    """
    Формирует расширенный список тем (более 500 позиций за счёт комбинаторных и нишевых подтем).
    Возвращает список кортежей: (category_lvl1, category_lvl2, topic_name, search_queries, priority).
    """
    items: List[Tuple[str, str, str, List[str], int]] = []

    for cat1, subcats in SEED_CATALOG.items():
        for cat2, topic_list in subcats.items():
            for topic_name, base_queries in topic_list:
                items.append((cat1, cat2, topic_name, base_queries, 20))

    # Дополнительное расширение сериалов, аниме, игр и марок авто до 500+ уникальных папок
    extra_series = [
        ("Dexter_Декстер", "dexter morgan blood spatter"),
        ("House_MD_Доктор_Хаус", "house md gregory house"),
        ("Sons_of_Anarchy_Сыны_Анархии", "sons of anarchy samcro reaper"),
        ("Vikings_Викинги", "vikings ragnar lothbrok"),
        ("Walking_Dead_Ходячие_Мертвецы", "the walking dead daryl negan"),
        ("Black_Mirror_Черное_Зеркало", "black mirror tv series"),
        ("Mr_Robot_Мистер_Робот", "mr robot fsociety elliot"),
        ("BoJack_Horseman_Конь_БоДжек", "bojack horseman"),
        ("Rick_and_Morty_Pickle_Rick", "pickle rick morty"),
        ("Avatar_The_Last_Airbender", "avatar aang appa zuko"),
        ("Gravity_Falls_Bill_Cipher", "bill cipher waddles gravity falls"),
        ("Invincible_Неуязвимый", "invincible omni man"),
        ("Hazbin_Hotel_Отель_Хазбин", "hazbin hotel alastor angel dust"),
        ("Helluva_Boss_Адский_Босс", "helluva boss blitzo loona"),
        ("Murder_Drones_Дроны_Убийцы", "murder drones uzi n"),
        ("Digital_Circus_Цифровой_Цирк", "the amazing digital circus pomni jax"),
        ("Regular_Show_Обычный_Мультик", "regular show mordecai rigby"),
        ("Samurai_Jack_Самурай_Джек", "samurai jack aku"),
        ("Over_the_Garden_Wall", "over the garden wall wirt greg"),
        ("Coraline_Коралина", "coraline鬼 button eyes"),
        ("Nightmare_Before_Christmas", "jack skellington nightmare before christmas"),
        ("Deadpool_Дэдпул", "deadpool marvel comics"),
        ("Venom_Веном", "venom symbiote marvel"),
        ("Spider_Verse_Человек_Паук", "miles morales spider gwen spiderverse"),
        ("Guardians_of_the_Galaxy", "baby groot rocket raccoon"),
        ("John_Wick_Джон_Уик", "john wick baba yaga"),
        ("Fast_and_Furious_Форсаж", "fast and furious paul walker skyline"),
        ("Drive_Драйв_Райан_Гослинг", "ryan gosling drive scorpion jacket"),
        ("Blade_Runner_2049", "blade runner 2049 cyberpunk"),
        ("Dune_Дюна", "dune paul atreides sandworm"),
        ("Mad_Max_Безумный_Макс", "mad max fury road immortan joe"),
        ("Ghostbusters_Охотники_за_привидениями", "ghostbusters slimer"),
        ("Alien_and_Predator_Чужой_Хищник", "alien xenomorph predator"),
        ("Terminator_Терминатор", "terminator t800 cyberdyne"),
        ("Jurassic_Park_Парк_Юрского_Периода", "jurassic park dinosaur logo"),
        ("Godzilla_Годзилла", "godzilla kaiju monster"),
        ("Transformers_Трансформеры", "transformers autobot decepticon optimus"),
        ("Cars_Pixar_Тачки_Маквин", "lightning mcqueen cars pixar"),
        ("Toy_Story_История_Игрушек", "toy story buzz woody"),
        ("Monsters_Inc_Корпорация_Монстров", "monsters inc sulley mike wazowski"),
        ("Wall_E_Валли", "wall-e eve pixar"),
        ("How_to_Train_Your_Dragon", "toothless night fury dragon"),
        ("Kung_Fu_Panda_Кунг_Фу_Панда", "kung fu panda po"),
        ("Zootopia_Зверополис", "zootopia nick judy"),
        ("Смешарики_Пин_Крош", "смешарики стикеры мем"),
        ("Чебурашка_и_Союзмультфильм", "союзмультфильм ну погоди винни пух стикеры"),
        ("Остров_Сокровищ_Доктор_Ливси", "остров сокровищ доктор ливси стикеры"),
        ("Жмурки_и_Бригада", "жмурки бригада бумер стикеры"),
    ]
    for folder, kw in extra_series:
        items.append(
            (
                "01_Стикерпаки",
                "Сериалы_и_Кино",
                folder,
                [f"{kw} sticker pack", f"{kw} stickers sheet"],
                15,
            )
        )

    extra_anime = [
        ("Vinland_Saga_Сага_о_Винланде", "vinland saga thorfinn"),
        ("Mob_Psycho_100_Моб_Психо", "mob psycho 100 reigen"),
        ("Chainsaw_Man_Power_Pochita", "pochita power chainsaw man"),
        ("Jujutsu_Kaisen_Gojo_Geto", "gojo satoru choso toji"),
        ("Demon_Slayer_Hashira", "rengoku tengen giyu hashira"),
        ("One_Piece_Gear_5_Luffy", "luffy gear 5 straw hat"),
        ("Naruto_Uchiha_Clan", "itachi sasuke madara obito uchiha"),
        ("Tokyo_Revengers_Токийские_Мстители", "tokyo revengers mikey draken"),
        ("Black_Clover_Черный_Клевер", "black clover asta yami"),
        ("Fairy_Tail_Хвост_Феи", "fairy tail natsu happy"),
        ("Sword_Art_Online_SAO", "sword art online kirito asuna"),
        ("Code_Geass_Код_Гиас", "code geass lelouch c.c."),
        ("Steins_Gate_Врата_Штейна", "steins gate okabe kurisu"),
        ("Gurren_Lagann_Гуррен_Лаганн", "gurren lagann kamina drill"),
        ("Kill_la_Kill_Килл_ла_Килл", "kill la kill ryuko matoi"),
        ("Darling_in_the_Franxx_Zero_Two", "zero two darling in the franxx"),
        ("Konosuba_Коносуба", "konosuba megumin aqua"),
        ("Re_Zero_Рем_Эмилия", "re zero rem ram emilia"),
        ("Overlord_Оверлорд", "overlord ainz albedo"),
        ("Goblin_Slayer", "goblin slayer anime"),
        ("Made_in_Abyss", "made in abyss nanachi"),
        ("Promised_Neverland", "promised neverland emma norman"),
        ("Dr_Stone_Доктор_Стоун", "dr stone senku"),
        ("Fire_Force_Пламенная_Бригада", "fire force shinra"),
        ("Soul_Eater_Пожиратель_Душ", "soul eater maka death the kid"),
        ("Hellsing_Хеллсинг_Алукард", "hellsing ultimate alucard"),
        ("Black_Lagoon_Черная_Лагуна", "black lagoon revy"),
        ("Great_Teacher_Onizuka_GTO", "great teacher onizuka gto"),
        ("Slam_Dunk_Слэм_Данк", "slam dunk sakuragi"),
        ("Hajime_no_Ippo_Первый_Шаг", "hajime no ippo boxing"),
        ("Baki_Боец_Баки", "baki hanma yujiro"),
        ("Kengen_Ashura", "kengan ashura ohma"),
        ("Yu_Gi_Oh_Югио", "yugioh dark magician blue eyes"),
        ("Pokemon_Покемоны_Пикачу", "pokemon pikachu gengar charizard eevee"),
        ("Digimon_Дигимоны", "digimon agumon"),
        ("Dragon_Ball_Z_Гоку", "dragon ball z goku vegeta"),
        ("Akira_Акира_Канеда", "akira kaneda bike capsule"),
        ("Ghost_in_the_Shell", "ghost in the shell motoko kusanagi"),
        ("Serial_Experiments_Lain", "serial experiments lain cyberia"),
        ("Nana_Аниме_Нана", "nana osaki black stones"),
    ]
    for folder, kw in extra_anime:
        items.append(
            (
                "01_Стикерпаки",
                "Аниме_и_Манга",
                folder,
                [f"{kw} sticker pack", f"{kw} anime stickers sheet"],
                15,
            )
        )

    extra_cars = [
        ("Toyota_Camry_Corolla", "toyota camry TRD car sticker decal"),
        ("Toyota_Land_Cruiser_Prado", "toyota land cruiser 4x4 offroad decal"),
        ("Toyota_AE86_Trueno", "toyota ae86 trueno initial d decal"),
        ("Toyota_Altezza_IS200", "toyota altezza rs200 lexus is300 sticker"),
        ("Lexus_F_Sport", "lexus f sport jdm car decal"),
        ("Nissan_350Z_370Z_Fairlady", "nissan 350z 370z fairlady z car sticker"),
        ("Nissan_180SX_240SX_Schassis", "nissan 240sx 180sx s13 s14 drift sticker"),
        ("Nissan_Patrol_Safari_4x4", "nissan patrol y60 y61 offroad decal"),
        ("Honda_Accord_Integra_S2000", "honda s2000 integra acura rsx vtec sticker"),
        ("Subaru_Forester_Legacy", "subaru forestersti boxer awd decal"),
        ("Mazda_3_6_MPS", "mazda mps speed3 zoom zoom car sticker"),
        ("Mitsubishi_Pajero_L200", "mitsubishi pajero ralliart 4x4 sticker"),
        ("Suzuki_Jimny_Samurai", "suzuki jimny offroad 4x4 sticker decal"),
        ("BMW_E30_E34_E36_Легенды", "bmw e30 e34 e36 classic drift sticker"),
        ("BMW_E38_E39_E46_E60", "bmw e39 e46 m5 angel eyes car sticker"),
        ("Mercedes_W124_W140_W210", "mercedes w124 w140 classic car decal"),
        ("Mercedes_G_Class_Gelendwagen", "mercedes g63 amg g wagon decal"),
        ("Audi_A4_A6_B5_C5_Quattro", "audi quattro torsen turbo car sticker"),
        ("VW_Golf_MK2_MK4_Passat", "vw golf mk2 mk4 gti stance sticker"),
        ("Skoda_Octavia_VRS", "skoda octavia vrs turbo car sticker"),
        ("Ford_Mustang_Focus_ST_RS", "ford mustang shelby focus rs car decal"),
        ("Chevrolet_Camaro_Corvette_Tahoe", "chevrolet corvette camaro v8 car sticker"),
        ("Dodge_Challenger_Charger_SRT", "dodge challenger srt hellcat demon decal"),
        ("Jeep_Wrangler_Cherokee_4x4", "jeep wrangler rubicon 4x4 trail rated decal"),
        ("Stance_Lowdaily_Static_Посадка", "stance static bagged low car slap sticker"),
        ("Turbo_Boost_Snail_Турбина", "turbo boost snail blow off car sticker"),
        ("Check_Engine_Автосервис_Юмор", "check engine funny car warning sticker"),
        ("Loud_Sound_Автозвук_Сабвуфер", "basshead car audio subwoofer spl sticker"),
        ("No_Fat_Chicks_JDM_Classic", "funny jdm warning sun visor sticker"),
        ("Brembo_Recaro_BBS_HKS_Bilstein", "bbs recaro hks greddy momo sparco decal"),
        ("Monster_Energy_RedBull_Rockstar", "monster energy redbull Hoonigan rally sticker"),
        ("Harley_Davidson_Чоппер_Клуб", "harley davidson skull eagle chopper decal"),
        ("Motul_Liqui_Moly_Castrol", "motul michelin pirelli racing sponsor decal"),
    ]
    for folder, kw in extra_cars:
        items.append(
            (
                "02_Авто_Наклейки",
                "JDM_и_Японские_Авто" if any(x in folder for x in ("Toyota", "Nissan", "Honda", "Subaru", "Mazda", "Mitsubishi", "Suzuki", "Lexus", "JDM")) else "Немецкие_и_Европейские_Авто",
                folder,
                [kw, f"{kw} printable"],
                16,
            )
        )

    # Расширенная коллекция тем (ещё 320+ нишевых папок до 520+ уникальных тем)
    more_series_and_cartoons = [
        "Doctor_Who_Доктор_Кто", "Hannibal_Ганнибал", "X_Files_Секретные_Материалы",
        "Lost_Остаться_в_живых", "Prison_Break_Побег", "Lucifer_Люцифер",
        "Good_Omens_Благие_Знамения", "The_Bear_Медведь", "Succession_Наследники",
        "Ted_Lasso_Тед_Лассо", "Severance_Разделение", "Silo_Укрытие",
        "Fallout_TV_Show", "The_Last_of_Us_Одни_из_нас", "Mandalorian_Grogu",
        "Ahsoka_Star_Wars", "Andor_Star_Wars", "Loki_Локи_Marvel",
        "WandaVision_Ванда", "Moon_Knight_Лунный_Рыцарь", "Daredevil_Сорвиголова",
        "Punisher_Каратель", "Watchmen_Хранители", "V_for_Vendetta",
        "Constantine_Константин", "Sandman_Песочный_Человек", "Umbrella_Academy",
        "Boys_Gen_V", "Invincible_Atom_Eve", "Vox_Machina",
        "Castlevania_Кастлвания", "Blue_Eye_Samurai", "Scavengers_Reign",
        "Love_Death_Robots", "Secret_Level", "Smiling_Friends",
        "Aqua_Teen_Hunger_Force", "Metalocalypse_Dethklok", "Venture_Bros",
        "Beavis_and_Butt_Head", "Daria_Дарья", "King_of_the_Hill",
        "American_Dad", "Family_Guy_Гриффины", "Bob_Burgers",
        "Archer_Арчер", "Disenchantment_Разочарование", "Final_Space",
        "Inside_Job_Корпорация_Заговор", "Infinity_Train", "Owl_House_Дом_Совы",
        "Amphibia_Амфибия", "Star_vs_Forces_of_Evil", "Steven_Universe",
        "Gumball_Удивительный_мир_Гамбола", "We_Bare_Bears_Вся_правда_о_медведях",
        "Powerpuff_Girls_Суперкрошки", "Dexter_Laboratory", "Courage_Cowardly_Dog",
        "Ed_Edd_n_Eddy", "Johnny_Bravo", "Grim_Adventures_Billy_Mandy",
        "Danny_Phantom", "Avatar_Korra", "Teen_Titans_Юные_Титаны",
        "Batman_Animated_Series", "Justice_League_Unlimited", "X_Men_97",
        "Spectacular_Spider_Man", "Ben_10_Бен_10", "Generator_Rex",
        "Kim_Possible_Ким_Пять_с_Плюсом", "Phineas_and_Ferb", "DuckTales_Утиные_Истории",
        "Chip_and_Dale_Чип_и_Дейл", "Darkwing_Duck_Черный_Плащ", "Gargoyles_Гаргульи",
        "Looney_Tunes_Bugs_Bunny", "Tom_and_Jerry_Том_и_Джерри", "Pink_Panther",
        "Woody_Woodpecker", "Garfield_Гарфилд", "Snoopy_Peanuts",
        "Moomin_Муми_Тролли", "Hello_Kitty_Sanrio", "Kuromi_My_Melody",
        "Cinnamoroll_Sanrio", "Gudetama_Sanrio", "Pusheen_Cat",
        "Molang_Piu_Piu", "BT21_Kpop", "Gorillaz_Band",
        "Scarface_Лицо_со_шрамом", "Godfather_Крестный_Отец", "Goodfellas_Славные_Парни",
        "Casino_Казино", "Reservoir_Dogs_Бешеные_Псы", "Kill_Bill_Убить_Билла",
        "Inglourious_Basterds", "Django_Unchained_Джанго", "Once_Upon_in_Hollywood",
        "Big_Lebowski_Большой_Лебовски", "Snatch_Большой_Куш", "Lock_Stock_Два_Ствола",
        "Gentlemen_Джентльмены", "Trainspotting_На_игле", "Clockwork_Orange",
        "Shining_Сияние", "2001_Space_Odyssey", "Interstellar_Интерстеллар",
        "Inception_Начало", "Dark_Knight_Темный_Рыцарь", "Oppenheimer_Оппенгеймер",
        "Memento_Помни", "Prestige_Престиж", "Seven_Семь",
        "Zodiac_Зодиак", "Gone_Girl", "Silence_of_the_Lambs",
        "American_Psycho_Патрик_Бейтман", "Taxi_Driver_Таксист", "Wolf_of_Wall_Street",
        "Great_Gatsby", "Shutter_Island", "Titanic_Титаник",
        "Avatar_Pandora", "Terminator_2_Judgment_Day", "Aliens_Ripley",
        "Robocop_Робокоп", "Total_Recall", "Fifth_Element_Пятый_Элемент",
        "Leon_Леон_Киллер", "Taxi_Марсель_Пежо", "Amelie_Амели",
        "Harry_Potter_Slytherin", "Harry_Potter_Gryffindor", "Fantastic_Beasts",
        "Hobbit_Smaug", "Chronicles_of_Narnia", "Hunger_Games",
        "Maze_Runner", "Twilight_Сумерки_Мемы", "Mean_Girls",
        "Clueless_90s", "Breakfast_Club", "Ferris_Bueller",
        "Home_Alone_Один_Дома", "Grinch_Гринч", "Beetlejuice_Битлджус",
        "Edward_Scissorhands", "Corpse_Bride_Труп_Невесты", "Frankenweenie",
        "Addams_Family", "Ghostface_Scream_Крик", "Jason_Voorhees_Friday_13",
        "Freddy_Krueger_Nightmare", "Michael_Myers_Halloween", "Chucky_Child_Play",
        "Pennywise_IT_Оно", "Saw_Jigsaw_Пила", "Evil_Dead_Ash",
        "Hellraiser_Pinhead", "Texas_Chainsaw_Massacre", "Conjuring_Annabelle",
    ]
    for folder in more_series_and_cartoons:
        clean_kw = folder.replace("_", " ")
        items.append(
            (
                "01_Стикерпаки",
                "Сериалы_и_Кино",
                folder,
                [f"{clean_kw} sticker pack", f"{clean_kw} stickers sheet"],
                12,
            )
        )

    more_games_and_anime = [
        "Baldur_Gate_3_Astarion", "Mass_Effect_N7", "Dragon_Age",
        "Bioshock_Rapture", "Dishonored_Outsider", "Prey_Typhon",
        "Deus_Ex_Jensen", "Hitman_Agent_47", "Max_Payne",
        "Alan_Wake_Control", "Control_FBC", "Death_Stranding_Kojima",
        "Metal_Gear_Solid_Snake", "Devil_May_Cry_Dante_Vergil", "Bayonetta",
        "Nier_Automata_2B", "Persona_5_Phantom_Thieves", "Persona_3_Reload",
        "Yakuza_Like_a_Dragon", "Final_Fantasy_VII_Cloud", "Kingdom_Hearts",
        "Monster_Hunter_Palico", "Street_Fighter_6", "Tekken_8_Mishima",
        "Guilty_Gear_Strive", "Doom_Eternal_Slayer", "Quake_Ranger",
        "Wolfenstein_BJ", "Half_Life_Black_Mesa", "Portal_Aperture_Science",
        "Team_Fortress_2_TF2", "Left_4_Dead_2", "Dead_Space_Isaac",
        "Subnautica_Peeper", "Outer_Wilds", "No_Mans_Sky",
        "Starfield_NASA_Punk", "Helldivers_2_Super_Earth", "Deep_Rock_Galactic",
        "Warhammer_40k_Space_Marine", "World_of_Warcraft_Horde_Alliance", "Diablo_IV_Lilith",
        "Starcraft_Terran_Zerg", "Overwatch_2_Heroes", "Apex_Legends_Nessy",
        "Titanfall_2_BT7274", "Rainbow_Six_Siege", "Escape_from_Tarkov_USEC_BEAR",
        "Rust_Survival_Stickers", "DayZ_Survivor", "PUBG_Winner_Chicken",
        "Fortnite_Llama_Stickers", "Roblox_Noob_Stickers", "Among_Us_Crewmate",
        "Fall_Guys_Beans", "Phasmophobia_Ghost", "Lethal_Company_Scrap",
        "Content_Warning", "Cult_of_the_Lamb", "Hades_Zagreus_Melinoe",
        "Celeste_Madeline", "Dead_Cells_Beheaded", "Binding_of_Isaac",
        "Slay_the_Spire", "Darkest_Dungeon", "Blasphemous_Penitent",
        "Rain_World_Slugcat", "Ori_and_Blind_Forest", "Little_Nightmares",
        "Limbo_Inside", "Stardew_Valley_Junimo", "Animal_Crossing_Isabelle",
        "Terraria_Slime", "Dont_Starve_Wilson", "Factorio_Gear",
        "RimWorld_Colony", "Kerbal_Space_Program", "Sims_Plumbob",
        "Disco_Elysium_Kim_Harrier", "Pathologic_Мор_Утопия", "Signalis_Elster",
        "Omori_Sunny", "Doki_Doki_Literature_Club", "Ace_Attorney_Objection",
        "Danganronpa_Monokuma", "Zelda_Breath_of_ the_Wild", "Metroid_Samus",
        "Splatoon_Squid", "Pikmin_Nintendo", "Xenoblade_Chronicles",
        "Fire_Emblem", "Pokemon_Eeveelutions", "Pokemon_Gengar_Ghost",
        "Honkai_Star_Rail", "Zenless_Zone_Zero", "Wuthering_Waves",
        "Arknights_Rhodes", "Blue_Archive", "Azur_Lane",
        "Fate_Stay_Night_Saber", "Tsukihime_Neco_Arc", "Touhou_Project_Fumo",
        "Hatsune_Miku_Vocaloid", "Bocchi_the_Rock", "K_On_Anime",
        "Lycoris_Recoil", "Cyberpunk_Lucy_Rebecca", "Dungeon_Meshi_Laios",
        "Apothecary_Diaries_Maomao", "Oshi_no_Ko_Ai", "Mashle_Magic_Muscles",
        "Dandadan_Momo_Okarun", "Kaiju_No_8", "Sakamoto_Days",
        "Kagurabachi", "Vagabond_Miyamoto_Musashi", "Monster_Johan_Liebert",
        "20th_Century_Boys", "Pluto_Urasawa", "Dorohedoro_Caiman",
        "Golden_Kamuy", "Gintama_Gintoki", "Trigun_Vash",
        "Samurai_Champloo", "Afro_Samurai", "Dororo_Hyakkimaru",
        "Inuyasha_Sesshomaru", "Yu_Yu_Hakusho", "Rurouni_Kenshin",
        "Claymore_Clare", "Gantz_Anime", "Parasyte_Migi",
        "Elfen_Lied", "Mirai_Nikki_Yuno", "Psycho_Pass",
        "Ergo_Proxy", "Texhnolyze", "Paprika_Satoshi_Kon",
        "Perfect_Blue", "Redline_Anime_Car", "Initial_D_Takumi_FD3S",
    ]
    for folder in more_games_and_anime:
        clean_kw = folder.replace("_", " ")
        items.append(
            (
                "01_Стикерпаки",
                "Игры_и_Гейминг" if folder in more_games_and_anime[:88] else "Аниме_и_Манга",
                folder,
                [f"{clean_kw} sticker pack", f"{clean_kw} stickers sheet"],
                12,
            )
        )

    more_auto_and_singles = [
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Toyota_Crown_Majesta_Athlete", "toyota crown jdm vip sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Toyota_Celica_MR2_Caldina", "toyota celica gt4 mr2 sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Nissan_Stagea_Laurel_Cefiro", "nissan laurel c33 stagea drift sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Honda_Prelude_CRX_DelSol", "honda prelude crx vtec sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Subaru_BRZ_Toyota_GT86", "gt86 brz frs boxer sticker decal"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Mitsubishi_Eclipse_3000GT_Galant", "mitsubishi eclipse dsm vr4 sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Daihatsu_Isuzu_Kei_Car", "kei truck jdm Carry sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Bosozoku_Shakotan_Kaido_Racer", "bosozoku tsurikawa jdm slap sticker"),
        ("02_Авто_Наклейки", "JDM_и_Японские_Авто", "Midnight_Club_Wangan_Runners", "mid night club wangan jdm sticker"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Opel_OPC_Astra_Vectra", "opel opc nurburgring car sticker"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Renault_Sport_Megane_RS", "renault sport rs trophy decal"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Peugeot_GTI_Citroen_VTS", "peugeot sport rallye gti sticker"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Alfa_Romeo_Quadrifoglio", "alfa romeo quadrifoglio clover decal"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Fiat_Abarth_Scorpion", "abarth scorpion racing car sticker"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Mini_Cooper_JCW_Works", "mini john cooper works sticker"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Land_Rover_Defender_Camel_Trophy", "camel trophy land rover defender decal"),
        ("02_Авто_Наклейки", "Немецкие_и_Европейские_Авто", "Saab_Turbo_Aero_Griffin", "saab turbo born from jets sticker"),
        ("02_Авто_Наклейки", "Приколы_Надписи_и_Инфо_на_Стекло", "Kia_Hyundai_Корейские_Авто", "kdmsociety kia hyundai turbo car sticker"),
        ("02_Авто_Наклейки", "Приколы_Надписи_и_Инфо_на_Стекло", "Дальнобой_Фуры_Scania_Volvo", "scania griffin v8 truck decal"),
        ("02_Авто_Наклейки", "Приколы_Надписи_и_Инфо_на_Стекло", "Эвакуатор_Техпомощь_Наклейки", "tow truck recovery 4x4 sticker"),
        ("02_Авто_Наклейки", "Приколы_Надписи_и_Инфо_на_Стекло", "Такси_Яндекс_Uber_Шашечки", "taxi cab checker decal vector"),
        ("02_Авто_Наклейки", "Приколы_Надписи_и_Инфо_на_Стекло", "ГБО_Пропан_Дизель_Мощь", "turbodiesel black smoke car sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Синтвейв_Retrowave_Закат", "synthwave sunset palm grid sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Грибы_Психоделика_Лес", "magic mushroom trippy frog sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Таро_Мистика_Ведьмы", "tarot card witchy sun moon sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Волки_Медведи_Дикая_Природа", "howling wolf bear mountain sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Акулы_Океан_Серфинг", "shark wave surfing vintage sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Горы_Кемпинг_Походы", "mountain adventure camping vanlife sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Сноуборд_Лыжи_Зима", "snowboard ski mountain sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Велосипед_MTB_BMX", "mtb downhill bmx bike sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Бокс_ММА_Единоборства", "mma boxing muay thai fighter sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Качалка_Фитнес_Бодибилдинг", "gym powerlifting barbell funny sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Футбол_Баскетбол_Хоккей", "basketball Jordan hockey skull sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Гитары_Рок_Инструменты", "electric guitar amp rock sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Винил_Кассеты_Музыка_90х", "retro cassette tape boombox sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Фотоаппараты_Кинокамеры", "vintage camera photography sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Книги_Чтение_Библиотека", "bookworm reading coffee sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Медицина_Врачи_Скорая", "medical anatomy heart nurse sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Авиация_Самолеты_Пилоты", "aviation fighter jet airplane sticker"),
        ("03_Одиночные_Стикеры", "Эстетика_Арт_и_Дизайн", "Корабли_Якоря_Морская_Тема", "nautical anchor kraken ship sticker"),
        ("04_Баннеры_и_Вывески", "Таблички_Предупреждения_и_Постеры", "Видеонаблюдение_Охрана_Знаки", "cctv security camera warning sign printable"),
        ("04_Баннеры_и_Вывески", "Таблички_Предупреждения_и_Постеры", "Не_Курить_Правила_Гаража", "garage rules vintage tin sign print"),
        ("04_Баннеры_и_Вывески", "Таблички_Предупреждения_и_Постеры", "Пиво_Паб_Барные_Постеры", "vintage beer pub metal sign poster"),
        ("04_Баннеры_и_Вывески", "Таблички_Предупреждения_и_Постеры", "Бокс_Спортзал_Мотивация_Плакат", "vintage boxing gym poster print"),
    ]
    for cat1, cat2, folder, kw in more_auto_and_singles:
        items.append((cat1, cat2, folder, [kw, f"{kw} pack"], 12))

    return items


def seed_database_if_needed() -> int:
    """Заполняет базу стартовым каталогом тем и возвращает общее число добавленных/обновлённых тем."""
    all_topics = build_expanded_Topics()
    for cat1, cat2, topic_name, queries, prio in all_topics:
        upsert_topic(
            category_lvl1=cat1,
            category_lvl2=cat2,
            topic_name=topic_name,
            search_queries=queries,
            priority=prio,
        )
    return len(all_topics)


def classify_user_topic(raw_topic: str) -> Tuple[str, str, str, List[str]]:
    """
    Автоматически определяет (category_lvl1, category_lvl2, clean_topic_name, search_queries)
    для любой темы, которую пользователь написал в Telegram-боте или веб-панели.
    """
    text = raw_topic.strip()
    low = text.lower()

    # 1. Проверяем на баннеры / вывески / таблички
    if any(w in low for w in ("баннер", "вывеск", "табличк", "постер", "плакат", "знак", "banner", "poster", "sign")):
        cat1 = "04_Баннеры_и_Вывески"
        if any(w in low for w in ("авто", "гараж", "шиномонтаж", "мойк", "сто ", "сервис", "garage")):
            cat2 = "Автобизнес_Гараж_и_Сервис"
        else:
            cat2 = "Таблички_Предупреждения_и_Постеры"
        queries = [
            f"{text} плакат вывеска макет",
            f"{text} vintage sign poster illustration",
        ]
        return cat1, cat2, text, queries

    # 2. Проверяем на автомобильную тематику
    car_keywords = (
        "авто", "машин", "на стекло", "бампер", "jdm", "дрифт", "drift",
        "toyota", "тойот", "mark", "chaser", "supra", "camry",
        "nissan", "ниссан", "skyline", "gtr", "silvia",
        "honda", "хонд", "civic", "subaru", "субар", "mazda", "мазд",
        "bmw", "бмв", "mercedes", "мерседес", "amg", "audi", "ауди", "vag", "golf",
        "ваз", "жигули", "лада", "приора", "веста", "нива", "уаз", "газель",
        "оффроуд", "4x4", "offroad", "car decal", "car sticker", "stance", "тюнинг",
    )
    if any(w in low for w in car_keywords):
        cat1 = "02_Авто_Наклейки"
        if any(w in low for w in ("ваз", "жигули", "лада", "приора", "веста", "нива", "уаз", "газель", "боевая")):
            cat2 = "Отечественные_ВАЗ_Lada_Боевая_Классика"
        elif any(w in low for w in ("bmw", "бмв", "mercedes", "мерседес", "audi", "ауди", "vw", "golf", "porsche", "volvo", "skoda")):
            cat2 = "Немецкие_и_Европейские_Авто"
        elif any(w in low for w in ("прикол", "надпис", "стекло", "ребенок", "рыбалк", "охот", "оффроуд", "4x4", "мото")):
            cat2 = "Приколы_Надписи_и_Инфо_на_Стекло"
        else:
            cat2 = "JDM_и_Японские_Авто"
        queries = [
            f"{text} car sticker decal vector",
            f"{text} наклейки на авто",
            f"{text} sticker pack",
        ]
        return cat1, cat2, text, queries

    # 3. Проверяем на стикерпаки (Аниме / Игры / Сериалы / Мемы)
    cat1 = "01_Стикерпаки"
    if any(w in low for w in ("аниме", "anime", "манга", "наруто", "ван пис", "блич", "титан", "гуль", "джоджо", "бензопил", "магическ", "клинок")):
        cat2 = "Аниме_и_Манга"
    elif any(w in low for w in ("игр", "game", "gta", "cs", "dota", "дота", "майнкрафт", "minecraft", "ведьмак", "сталкер", "stalker", "brawl", "genshin", "fnaf")):
        cat2 = "Игры_и_Гейминг"
    elif any(w in low for w in ("кот", "мем", "meme", "собак", "пепе", "капибар", "рок", "метал", "тату", "космос", "it ", "программист")):
        cat2 = "Мемы_Котики_и_Поп_Культура"
    else:
        cat2 = "Сериалы_и_Кино"

    queries = [
        f"{text} sticker pack",
        f"{text} stickers sheet printable",
        f"{text} стикерпак наклейки",
    ]
    return cat1, cat2, text, queries
