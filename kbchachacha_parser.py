# -*- coding: utf-8 -*-
"""
kbchachacha_parser.py
=====================

Номер машины и комплектация по ссылке на объявление KB Chachacha
(kbchachacha.com). Клиенты присылают не только ссылки Encar, но и такие —
без этого модуля бот ответил бы «не удалось получить номер машины».

ЧТО ОТДАЁТ СТРАНИЦА (проверено на двух реальных объявлениях, BMW X4
carSeq=28780780 и Korando carSeq=28791250, десктоп и мобильная версия):

    госномер      — мета-тег og:description: "(381가7365)BMWNew X4 (G02) ..."
                    и, запасным путём, таблица «차량정보» / «차량번호»
    комплектация  — JSON-LD "name": "BMW New X4 (G02) xDrive 20d M Sport Pro (2022년형)"
    VIN           — НЕТ. Он есть только в листе техосмотра на сторонних
                    сайтах (m-park, autocafe и др.), у каждого свой формат.

Без VIN машина идёт в Carmoodo по номеру — ровно так же, как ~5%
объявлений Encar, где продавец VIN не указал. Комплектация нужна именно
Carmoodo: по ней он выбирает вариант, когда номер даёт несколько.

ССЫЛКИ. Поддерживаются все виды, которые встречались:
    https://www.kbchachacha.com/public/car/detail.kbc?carSeq=28780780
    https://m.kbchachacha.com/public/web/car/detail.kbc?carSeq=28791250
    https://m.kbchachacha.com/public/web/common/sns/car/detail.kbc?c=...
Последняя — «поделиться» из приложения, с зашифрованным id. Разбирать её
не нужно: сайт сам переадресует на обычную, requests идёт по редиректу.

Ошибки — EncarParsingError, тот же класс, что у Encar: handlers.py уже
превращает его в понятный ответ человеку, и второй класс ради другого
сайта ничего бы не дал.
"""

import html
import json
import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

from encar_parser import EncarParsingError

logger = logging.getLogger("kbchachacha_parser")

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,ru;q=0.8,en;q=0.7",
}

# Корейский госномер: 2-3 цифры, слог, 4 цифры; у старых номеров перед
# ними бывает регион ("경기63나4085" — такие встречались в логах проекта).
PLATE_RE = r"(?:[가-힣]{2})?\d{2,3}[가-힣]\s?\d{4}"


def is_kbchachacha_url(url: str) -> bool:
    host = (urlparse((url or "").strip()).hostname or "").lower()
    return host == "kbchachacha.com" or host.endswith(".kbchachacha.com")


def find_url(text: str) -> Optional[str]:
    """Ссылка на KB Chachacha из сообщения целиком (рядом бывает текст)."""
    m = re.search(r"https?://[^\s]*kbchachacha\.com[^\s]*", text or "")
    return m.group(0) if m else None


def _clean_plate(raw: str) -> str:
    return re.sub(r"\s+", "", raw)


def parse_plate(page: str) -> Optional[str]:
    # 1. og:description начинается с номера в скобках — так на обеих версиях.
    m = re.search(
        r'<meta\s+property="og:description"\s+content="\((' + PLATE_RE + r')\)', page
    )
    if m:
        return _clean_plate(m.group(1))
    # 2. Таблица характеристик: десктоп «차량정보», мобильная «차량번호».
    m = re.search(
        r"(?:차량정보|차량번호)\s*</(?:th|dt)>\s*<(?:td|dd)[^>]*>\s*(" + PLATE_RE + r")\s*<",
        page,
    )
    if m:
        return _clean_plate(m.group(1))
    return None


def parse_grade(page: str) -> Optional[str]:
    """
    Модель и комплектация для Carmoodo, например
    "BMW New X4 (G02) xDrive 20d M Sport Pro".

    Год ("(2022년형)") убираем — в вариантах Carmoodo его нет; марку в
    начале оставляем — Carmoodo
    считает долю СВОИХ слов, нашедшихся в подсказке, и лишние слова
    подсказки результат не портят (см. _pick_matching_trim_option).
    """
    name = None
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.S
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product" and data.get("name"):
            name = data["name"]
            break
    if not name:
        m = re.search(r"productName\s*:\s*'([^']+)'", page)
        name = m.group(1) if m else None
    if not name:
        return None
    name = html.unescape(name)
    name = re.sub(r"\s*\(\d{4}년형\)\s*$", "", name)
    # Пустая комплектация у продавца даёт хвост "A220 Sedan -" (carSeq=28376980).
    name = re.sub(r"[\s\-·]+$", "", name).strip()
    return name or None


def get_vehicle_number_vin_and_grade(url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Тот же контракт, что у encar_parser.get_vehicle_number_vin_and_grade:
    (номер, VIN, комплектация). VIN всегда None — см. шапку модуля.
    """
    try:
        response = requests.get(
            url.strip(), headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
    except requests.RequestException as exc:
        raise EncarParsingError(f"KB Chachacha не ответил: {exc} (ссылка: {url})")

    if response.status_code != 200:
        raise EncarParsingError(
            f"KB Chachacha ответил кодом {response.status_code} (ссылка: {url})"
        )

    response.encoding = response.encoding or "utf-8"
    page = response.text
    plate = parse_plate(page)
    if not plate:
        raise EncarParsingError(
            f"На странице KB Chachacha не нашёлся номер машины "
            f"(ссылка: {url}, итоговый адрес: {response.url}). Объявление "
            f"удалено или сайт изменил вёрстку."
        )
    grade = parse_grade(page)
    logger.info(
        "KB Chachacha: номер %s, комплектация %r (адрес %s).", plate, grade, response.url
    )
    return plate, None, grade
