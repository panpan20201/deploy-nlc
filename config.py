# -*- coding: utf-8 -*-
"""
config.py
=========

Читает настройки из файла .env и ПРОВЕРЯЕТ их при старте.

КАК УСТРОЕНА ПРОВЕРКА
---------------------

Все настройки (BOT_TOKEN, пароли Carmoodo, доступ к PARTSNUMBER, ключ
DeepL) проверяются ОДИН РАЗ при старте. Иначе забытая или опечатанная
строка в .env вылезала бы не при запуске, а в середине проверки живого
клиента — и выглядела бы как загадочный сбой шага, хотя на самом деле
была бы банальной опечаткой в настройках.

Проверка сразу говорит, чего не хватает и к чему это приведёт.
Разделение простое:

  * ОБЯЗАТЕЛЬНЫЕ настройки (сейчас это только BOT_TOKEN) — без них бот
    не имеет смысла, поэтому он честно отказывается стартовать.
  * ВАЖНЫЕ, но не критичные — без них система запустится, но часть
    возможностей отключится. Про каждую пишется предупреждение с
    объяснением, ЧТО именно перестанет работать. Так пропажа заметна
    сразу в первых строках лога, а не через час непонятных сбоев.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# --- Обязательное ---------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Устройство и Appium --------------------------------------------------
APPIUM_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
ANDROID_UDID = os.getenv("ANDROID_UDID")
ANDROID_DEVICE_NAME = os.getenv("ANDROID_DEVICE_NAME", "emulator-5554")

# --- Carmoodo -------------------------------------------------------------
CARMOODO_USERNAME = os.getenv("CARMOODO_USERNAME")
CARMOODO_PASSWORD = os.getenv("CARMOODO_PASSWORD")

# --- PARTSNUMBER ----------------------------------------------------------
PARTSNUMBER_LOGIN_URL = os.getenv("PARTSNUMBER_LOGIN_URL")
PARTSNUMBER_USERNAME = os.getenv("PARTSNUMBER_USERNAME")
PARTSNUMBER_PASSWORD = os.getenv("PARTSNUMBER_PASSWORD")

# --- Перевод --------------------------------------------------------------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")


if not BOT_TOKEN:
    raise ValueError(
        "Не найден BOT_TOKEN. Проверь, что в папке проекта есть файл .env "
        "и в нём есть строка вида BOT_TOKEN=твой_токен_бота "
        "(см. пример в файле .env.example)."
    )


# Что проверяем: (значение, название в .env, что сломается без него)
_IMPORTANT_SETTINGS = [
    (
        CARMOODO_USERNAME and CARMOODO_PASSWORD,
        "CARMOODO_USERNAME / CARMOODO_PASSWORD",
        "бот не сможет сам войти в Carmoodo, если приложение разлогинится. "
        "Разлогин случается регулярно, и тогда ВСЕ запросы VIN будут падать, "
        "пока кто-нибудь не войдёт руками на планшете.",
    ),
    (
        PARTSNUMBER_USERNAME and PARTSNUMBER_PASSWORD and PARTSNUMBER_LOGIN_URL,
        "PARTSNUMBER_LOGIN_URL / PARTSNUMBER_USERNAME / PARTSNUMBER_PASSWORD",
        "шаг PARTSNUMBER (детали и дата выпуска) работать не будет — "
        "в ответах этот раздел всегда будет пустым.",
    ),
    (
        DEEPL_API_KEY,
        "DEEPL_API_KEY",
        "незнакомые корейские термины в истории ремонта останутся "
        "непереведёнными и будут показаны с пометкой [?].",
    ),
    (
        ANDROID_UDID,
        "ANDROID_UDID",
        "устройство будет выбираться по имени, а не по серийному номеру. "
        "Пока планшет подключён один — это работает, но если к компьютеру "
        "подключат второе устройство, бот может начать управлять не тем.",
    ),
]


def check_settings() -> int:
    """
    Проверяет настройки и пишет в лог понятные предупреждения.
    Вызывается из bot.py сразу после настройки логирования.

    :return: количество найденных проблем (0 — всё на месте)
    """
    problems = 0
    for is_set, name, consequence in _IMPORTANT_SETTINGS:
        if not is_set:
            problems += 1
            logger.warning("В .env не заполнено: %s — %s", name, consequence)

    if problems == 0:
        logger.info("Настройки .env проверены — заполнено всё необходимое.")
    else:
        logger.warning(
            "Проверка .env: не заполнено пунктов — %d. Бот запустится, но с "
            "ограничениями (см. предупреждения выше).",
            problems,
        )
    return problems
