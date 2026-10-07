# -*- coding: utf-8 -*-
"""
encar_parser.py
================

Модуль для извлечения ГОСНОМЕРА автомобиля (vehicleNo) и VIN со страницы
объявления сайта Encar (https://fem.encar.com/cars/detail/{carid}).

КАК ЭТО РАБОТАЕТ (простыми словами):

Когда мы открываем страницу объявления в браузере, сервер Encar уже
"зашивает" все данные об автомобиле прямо в HTML-код страницы — в виде
куска JavaScript-кода вида:

    __PRELOADED_STATE__ = { ...много данных в формате JSON... };

Это сделано для того, чтобы страница красиво и быстро отображалась в
браузере ещё до того, как загрузится и выполнится весь JavaScript.
Нам это на руку: можно вообще не запускать браузер, а просто скачать
HTML-страницу как текст и вытащить из неё нужные данные.

У нас есть ДВА способа получить номер машины:

1) СПОСОБ 1 (быстрый, основной): напрямую обратиться к серверу Encar,
   который отдаёт данные (api.encar.com), и получить готовый JSON
   с номером машины. Это быстрее, потому что не нужно скачивать всю
   HTML-страницу целиком.

2) СПОСОБ 2 (резервный, fallback): если способ 1 не сработал (сайт
   заблокировал запрос, изменил структуру ответа и т.п.) — скачиваем
   обычную HTML-страницу объявления и вытаскиваем данные из
   __PRELOADED_STATE__, как описано выше.

Если оба способа не сработали — модуль поднимает понятную ошибку
EncarParsingError с объяснением, что именно пошло не так, вместо того,
чтобы падать с непонятным traceback.

Требуется библиотека `requests` (устанавливается командой:
    pip install requests
если она ещё не установлена).
"""

import json
import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

import requests

# Настраиваем логирование. Пользователь-непрограммист может не понимать
# логи, но они полезны для нас (или для того, кто будет отлаживать код).
logger = logging.getLogger("encar_parser")
if not logger.handlers:
    # Если логирование ещё нигде не настроено — настроим по-простому,
    # чтобы сообщения выводились в консоль.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


class EncarParsingError(Exception):
    """
    Собственное исключение (ошибка) для этого модуля.

    Если что-то пошло не так при получении данных с Encar — мы
    поднимаем именно эту ошибку с понятным человеческим текстом,
    а не даём программе упасть с непонятным traceback.
    """
    pass


# ---------------------------------------------------------------------------
# Заголовки, имитирующие обычный браузер (Chrome).
# Без этого многие сайты (в том числе Encar) могут блокировать запросы,
# определяя, что их делает не браузер, а скрипт.
# ---------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fem.encar.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,ru;q=0.8,en;q=0.7",
}

# Заголовки для скачивания самой HTML-страницы (способ 2) —
# тут Accept немного другой, т.к. мы просим у сервера HTML, а не JSON.
BROWSER_HEADERS_HTML = dict(BROWSER_HEADERS)
BROWSER_HEADERS_HTML["Accept"] = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
)

# Таймаут на запросы (в секундах), чтобы скрипт не "завис" навечно,
# если сайт не отвечает.
REQUEST_TIMEOUT = 15

# Список полей ("include"), который передаётся в запрос к API —
# он был подсмотрен в поле requestUrl внутри __PRELOADED_STATE__ реальной
# страницы объявления.
API_INCLUDE_FIELDS = (
    "ADVERTISEMENT,CATEGORY,CONDITION,CONTACT,MANAGE,OPTIONS,"
    "PHOTOS,SPEC,PARTNERSHIP,CENTER,VIEW"
)


def extract_car_id(url: str) -> Optional[str]:
    """
    Достаёт id объявления (carid) из ссылки на Encar.

    Поддерживает два формата ссылок:

    1) Новый формат:
       https://fem.encar.com/cars/detail/42201853?pageid=...&carid=42201853
       — id стоит прямо в пути ссылки, сразу после /cars/detail/

    2) Старый формат:
       http://www.encar.com/dc/dc_cardetailview.do?carid=41449755
       — id передаётся как параметр carid= в query-строке (после знака ?)

    Возвращает id как строку (например "42201853") или None, если
    в ссылке не удалось найти id.
    """
    if not url:
        return None

    parsed = urlparse(url)

    # --- Пробуем новый формат: id в пути, вида /cars/detail/42201853 ---
    path_match = re.search(r"/cars/detail/(\d+)", parsed.path)
    if path_match:
        return path_match.group(1)

    # --- Пробуем старый формат: id в query-параметре carid=... ---
    query_params = parse_qs(parsed.query)
    if "carid" in query_params and query_params["carid"]:
        candidate = query_params["carid"][0]
        if candidate.isdigit():
            return candidate

    # Если ни один из вариантов не сработал — на всякий случай попробуем
    # найти любое упоминание carid=ЦИФРЫ в самой ссылке целиком
    # (вдруг ссылка пришла в каком-то нестандартном виде).
    fallback_match = re.search(r"carid=(\d+)", url)
    if fallback_match:
        return fallback_match.group(1)

    return None


def _find_vehicle_no_in_json(data: dict) -> Optional[str]:
    """
    Ищет номер машины (vehicleNo) внутри произвольной JSON-структуры.

    Зачем это нужно: структура ответа "сырого" API (способ 1) может
    немного отличаться от структуры Redux-стейта на странице (способ 2).
    Чтобы не зависеть от точной вложенности полей, эта функция рекурсивно
    обходит весь JSON-объект (словари и списки внутри него) и ищет ключ
    "vehicleNo" на любом уровне вложенности.

    Возвращает найденное значение (строку) или None, если ничего не нашли.
    """
    if isinstance(data, dict):
        if "vehicleNo" in data and data["vehicleNo"]:
            return str(data["vehicleNo"])
        for value in data.values():
            result = _find_vehicle_no_in_json(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_vehicle_no_in_json(item)
            if result:
                return result
    return None


def _find_vin_in_json(data: dict) -> Optional[str]:
    """
    Точно так же, как _find_vehicle_no_in_json, но ищет VIN (ключ "vin").
    VIN нам не обязателен для основной задачи, но полезен для проверки —
    и пригодится в других частях проекта (Carmoodo и т.д.).
    """
    if isinstance(data, dict):
        if "vin" in data and data["vin"]:
            return str(data["vin"])
        for value in data.values():
            result = _find_vin_in_json(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_vin_in_json(item)
            if result:
                return result
    return None


def _find_category_dict_in_json(data) -> Optional[dict]:
    """
    Ищет словарь "category" внутри произвольной JSON-структуры (тем же
    рекурсивным способом, что и _find_vehicle_no_in_json/_find_vin_in_json
    выше) — именно в нём лежат модель/трим машины (modelName, gradeName,
    gradeDetailName), см. _extract_grade_text.

    Нужно для шага выбора трима в Carmoodo:
    когда Carmoodo не может однозначно определить машину по одному номеру,
    она показывает экран выбора конкретной сборки — и нам нужно заранее
    знать точный трим машины из объявления Encar, чтобы код мог сам
    выбрать правильный вариант, не полагаясь на человека.
    """
    if isinstance(data, dict):
        if "category" in data and isinstance(data["category"], dict):
            return data["category"]
        for value in data.values():
            result = _find_category_dict_in_json(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_category_dict_in_json(item)
            if result:
                return result
    return None


def _extract_grade_text(data) -> Optional[str]:
    """
    Собирает из словаря "category" человекочитаемую строку вида
    "아반떼 XD 5DR 1.5 DOHC 스포츠 기본형" (modelName + gradeName +
    gradeDetailName через пробел) — ПОДТВЕРЖДЕНО реальным примером: это
    ровно тот же текст, что Encar показывает как заголовок объявления
    (см. реальный JSON по carid=41507203, "아반떼 XD 5DR 1.5 DOHC 스포츠
    기본형" совпадает с заголовком страницы один-в-один).

    Эта строка дальше используется в carmoodo_vin.py, чтобы сопоставить
    её с текстом вариантов на экране выбора трима в Carmoodo (см.
    _pick_matching_trim_option в том файле) — поэтому здесь важно взять
    именно "сырые" поля как есть, ничего не сокращая и не переводя.

    Возвращает None, если словарь "category" вообще не нашёлся или в нём
    нет ни одного из трёх полей (тогда сопоставлять всё равно нечего).
    """
    category = _find_category_dict_in_json(data)
    if not category:
        return None

    parts = [
        category.get("modelName"),
        category.get("gradeName"),
        category.get("gradeDetailName"),
    ]
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return None
    return " ".join(parts)


def _try_api_method(car_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    СПОСОБ 1: прямой запрос к api.encar.com.

    Возвращает кортеж (vehicle_no, vin, grade_text). Если не получилось —
    возвращает (None, None, None), но не поднимает исключение — чтобы
    вызывающий код мог спокойно попробовать способ 2 (fallback).
    """
    api_url = (
        f"https://api.encar.com/v1/readside/vehicle/{car_id}"
        f"?include={API_INCLUDE_FIELDS}"
    )

    logger.info("Способ 1: запрашиваю данные напрямую с API: %s", api_url)

    try:
        response = requests.get(
            api_url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        # Например: нет интернета, сайт не отвечает, обрыв соединения и т.п.
        logger.warning("Способ 1: не удалось выполнить запрос — %s", exc)
        return None, None, None

    if response.status_code != 200:
        logger.warning(
            "Способ 1: сервер ответил кодом %s вместо 200 — API недоступен "
            "или заблокировал запрос.",
            response.status_code,
        )
        return None, None, None

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "Способ 1: ответ сервера не является корректным JSON — "
            "возможно, сайт вернул HTML-страницу с ошибкой вместо данных."
        )
        return None, None, None

    # Сырой JSON нужен только для сверки со структурой ответа при разборе
    # сбоя. Это 3000 символов в КАЖДОМ запросе (около 120 строк лога на
    # одну проверку), которые в консоли заслоняли бы ход работы. Поэтому
    # уровень DEBUG: в файл лога оно попадает целиком (файл пишется от
    # DEBUG), а консоль им не заливается.
    logger.debug(
        "Способ 1: получен сырой JSON-ответ от API (для сверки структуры):\n%s",
        json.dumps(data, ensure_ascii=False, indent=2)[:3000],
        # Обрезаем до 3000 символов, чтобы не засорять лог на очень
        # длинных ответах — этого достаточно, чтобы увидеть структуру.
    )

    vehicle_no = _find_vehicle_no_in_json(data)
    vin = _find_vin_in_json(data)
    grade_text = _extract_grade_text(data)

    if vehicle_no:
        logger.info("Способ 1: номер машины найден — %s", vehicle_no)
    else:
        logger.warning(
            "Способ 1: запрос прошёл успешно, но поле vehicleNo в ответе "
            "не найдено — структура ответа могла измениться."
        )
    if grade_text:
        logger.info("Способ 1: модель/трим найдены — %s", grade_text)

    return vehicle_no, vin, grade_text


def _try_html_page_method(car_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    СПОСОБ 2 (fallback): скачиваем HTML-страницу объявления и достаём
    из неё JSON-объект __PRELOADED_STATE__.

    Возвращает кортеж (vehicle_no, vin, grade_text), либо (None, None, None)
    при неудаче.
    """
    page_url = f"https://fem.encar.com/cars/detail/{car_id}"

    logger.info("Способ 2: скачиваю HTML-страницу объявления: %s", page_url)

    try:
        response = requests.get(
            page_url, headers=BROWSER_HEADERS_HTML, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.warning("Способ 2: не удалось скачать страницу — %s", exc)
        return None, None, None

    if response.status_code != 200:
        logger.warning(
            "Способ 2: сервер ответил кодом %s вместо 200 при загрузке "
            "страницы.",
            response.status_code,
        )
        return None, None, None

    html_text = response.text

    # Ищем начало куска "__PRELOADED_STATE__ = {"
    marker = "__PRELOADED_STATE__"
    marker_pos = html_text.find(marker)
    if marker_pos == -1:
        logger.warning(
            "Способ 2: в HTML-странице не найден блок __PRELOADED_STATE__ — "
            "возможно, сайт изменил структуру страницы."
        )
        return None, None, None

    # Ищем открывающую фигурную скобку "{" после найденного маркера —
    # именно с неё начинается сам JSON-объект.
    brace_pos = html_text.find("{", marker_pos)
    if brace_pos == -1:
        logger.warning(
            "Способ 2: после __PRELOADED_STATE__ не найдена открывающая "
            "скобка '{' — не удалось определить начало JSON."
        )
        return None, None, None

    # ВАЖНО: не обрезаем строку по первому символу ";", т.к. внутри самого
    # JSON (например, в текстовых описаниях опций автомобиля) тоже могут
    # встречаться символы ";", и это сломает разбор.
    #
    # Вместо этого используем json.JSONDecoder().raw_decode() — эта
    # функция сама умеет найти, где именно заканчивается корректный
    # JSON-объект, начиная с указанной позиции, полностью игнорируя любые
    # символы ";" внутри строковых значений.
    json_fragment = html_text[brace_pos:]

    try:
        data, _end_index = json.JSONDecoder().raw_decode(json_fragment)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Способ 2: не удалось разобрать JSON из __PRELOADED_STATE__ — %s",
            exc,
        )
        return None, None, None

    vehicle_no = _find_vehicle_no_in_json(data)
    vin = _find_vin_in_json(data)
    grade_text = _extract_grade_text(data)

    if vehicle_no:
        logger.info("Способ 2: номер машины найден — %s", vehicle_no)
    else:
        logger.warning(
            "Способ 2: страница скачана и JSON разобран, но поле "
            "vehicleNo не найдено внутри __PRELOADED_STATE__."
        )
    if grade_text:
        logger.info("Способ 2: модель/трим найдены — %s", grade_text)

    return vehicle_no, vin, grade_text


def _fetch_all(car_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Общая внутренняя функция: пробует способ 1 (API), а если он не дал
    номер машины — способ 2 (HTML-страница), и возвращает всё, что
    удалось получить, одним кортежем (vehicle_no, vin, grade_text).

    Вынесена отдельно, чтобы не делать ДВА отдельных HTTP-запроса за
    одними и теми же данными (один — за номером, другой — за тримом):
    pipeline.py получает всё разом через get_vehicle_number_vin_and_grade()
    ниже, а она использует именно эту функцию.

    Критерий "способ сработал" — именно наличие vehicle_no: без номера
    машины сам смысл запроса теряется, поэтому если
    способ 1 не дал номер — пробуем способ 2 целиком заново, а не пытаемся
    "добрать" недостающие поля по отдельности из разных способов (это
    усложнило бы логику ради редкого случая, который на практике пока не
    встречался).
    """
    vehicle_no, vin, grade_text = _try_api_method(car_id)
    if vehicle_no:
        return vehicle_no, vin, grade_text

    logger.info("Способ 1 не дал результата, пробую способ 2 (fallback)...")

    return _try_html_page_method(car_id)


def get_vehicle_number(url: str) -> str:
    """
    ГЛАВНАЯ ФУНКЦИЯ МОДУЛЯ.

    Принимает ссылку на объявление Encar целиком (в любом из двух
    поддерживаемых форматов), сама достаёт из неё id объявления,
    пробует получить номер машины сначала способом 1 (быстрый запрос
    к API), а если не получилось — способом 2 (скачивание HTML-страницы
    и разбор __PRELOADED_STATE__).

    Возвращает номер машины строкой, например "123저7671".

    Если номер получить не удалось ни одним из способов — поднимает
    исключение EncarParsingError с понятным описанием причины.
    """
    vehicle_no, _grade_text = get_vehicle_number_and_grade(url)
    return vehicle_no


def get_vehicle_number_and_grade(url: str):
    """
    Совместимость: то же самое, но без VIN.

    Оставлено для кода, которому VIN не нужен; новый код пусть зовёт
    get_vehicle_number_vin_and_grade напрямую.
    """
    vehicle_no, _vin, grade_text = get_vehicle_number_vin_and_grade(url)
    return vehicle_no, grade_text


def get_vehicle_number_vin_and_grade(url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    То же самое, что get_vehicle_number(), но заодно возвращает и текст
    модели/трима машины (см. _extract_grade_text) — нужно для шага
    выбора трима в Carmoodo (см. carmoodo_vin.py, _pick_matching_trim_option):
    когда Carmoodo не может однозначно определить машину по одному номеру,
    ей нужно ЗАРАНЕЕ знать точный трим машины, чтобы выбрать правильный
    вариант самостоятельно, без участия человека.

    Возвращает кортеж (vehicle_no, vin, grade_text). И vin, и grade_text
    могут быть None, если этих данных нет в ответе Encar (сам номер машины
    при этом всё равно возвращается как обычно) — вызывающий код
    (pipeline.py) должен быть готов к этому и просто не использовать
    отсутствующее, а не считать это ошибкой всего запроса.

    Отсутствующий VIN — штатный случай: по сверке он не указан примерно
    у 5% объявлений. Такая машина просто идёт за VIN в Carmoodo.

    Если номер машины получить не удалось ни одним из способов — поднимает
    исключение EncarParsingError, как и get_vehicle_number().
    """
    # Ссылки KB Chachacha разбираются своим модулем (см. его шапку).
    # Импорт здесь, а не наверху: тот модуль сам берёт отсюда EncarParsingError.
    import kbchachacha_parser
    kb_url = kbchachacha_parser.find_url(url)
    if kb_url:
        return kbchachacha_parser.get_vehicle_number_vin_and_grade(kb_url)

    car_id = extract_car_id(url)
    if not car_id:
        raise EncarParsingError(
            f"Не удалось найти id объявления (carid) в ссылке: {url}\n"
            f"Проверьте, что ссылка похожа на один из поддерживаемых "
            f"форматов:\n"
            f"  - https://fem.encar.com/cars/detail/42201853\n"
            f"  - http://www.encar.com/dc/dc_cardetailview.do?carid=41449755"
        )

    logger.info("Извлечён id объявления: %s", car_id)

    vehicle_no, vin, grade_text = _fetch_all(car_id)
    if vehicle_no:
        # VIN отдаётся наружу: при годном VIN шаг Carmoodo пропускается
        # целиком (см. vin_check). Это безопасно: сверка с Carmoodo
        # показала, что полный 17-значный VIN с Encar совпадает с
        # Carmoodo во всех случаях, а отклонения Encar — только отсутствие
        # VIN или обрезок, которые ловятся без второго источника (пустое
        # поле, длина не 17). VIN достаётся тем же запросом, бесплатно.
        return vehicle_no, vin, grade_text

    # Если оба способа не сработали — сообщаем об этом понятно и явно.
    raise EncarParsingError(
        f"Не удалось получить номер машины для объявления с id={car_id} "
        f"(ссылка: {url}).\n"
        f"Оба способа (прямой запрос к API и скачивание HTML-страницы) "
        f"не дали результата. Возможные причины:\n"
        f"  - объявление удалено или недоступно;\n"
        f"  - сайт изменил структуру данных;\n"
        f"  - сайт временно заблокировал запросы (попробуйте позже);\n"
        f"Подробности смотрите в логах выше (сообщения WARNING)."
    )


def get_vin(url: str) -> Optional[str]:
    """
    Дополнительная вспомогательная функция: получить VIN автомобиля
    по ссылке на объявление Encar (если он доступен в данных объявления).

    В отличие от get_vehicle_number, не поднимает исключение, если VIN
    не найден — просто возвращает None, т.к. VIN не всегда присутствует
    в объявлении на Encar (VIN там необязателен).
    """
    car_id = extract_car_id(url)
    if not car_id:
        raise EncarParsingError(
            f"Не удалось найти id объявления (carid) в ссылке: {url}"
        )

    _vehicle_no, vin, _grade_text = _fetch_all(car_id)
    return vin


# ---------------------------------------------------------------------------
# Тестовый блок. Запускается только если файл запущен напрямую командой:
#     python encar_parser.py
# (а не при импорте модуля из другого файла).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_url = (
        "https://fem.encar.com/cars/detail/42201853?"
        "pageid=dc_carsearch&listAdvType=pic&carid=42201853&view_type=normal"
    )

    print("Тестовая ссылка:", test_url)
    print("Ожидаемый результат: 123저7671")
    print("-" * 60)

    try:
        result = get_vehicle_number(test_url)
        print("Получен номер машины:", result)
        if result == "123저7671":
            print("✅ Тест пройден успешно!")
        else:
            print(
                "⚠️ Номер получен, но НЕ совпадает с ожидаемым — "
                "возможно, объявление изменилось или структура данных "
                "поменялась."
            )
    except EncarParsingError as e:
        print("❌ Ошибка при получении номера машины:")
        print(e)
