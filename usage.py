# -*- coding: utf-8 -*-
"""
usage.py
========

Учёт дневного лимита Carmoodo.

ЗАЧЕМ
-----

Carmoodo разрешает 30 запросов в сутки ("일 30건" — надпись прямо на
экране ввода). Это самый дефицитный ресурс во всей системе, и без учёта
он невидим: никто не знает, сколько запросов уже потрачено, пока лимит
внезапно не закончится посреди рабочего дня. Дальше все проверки
перестают давать VIN, а вместе с VIN отваливается и PARTSNUMBER — то
есть выпадают две трети результата, причём выглядит это как поломка, а
не как исчерпанный лимит.

Что даёт этот учёт:

1. Видно расход. Команда /status показывает, сколько запросов уже
   потрачено сегодня, — можно спланировать день, а не упереться в стену.

2. Предупреждение заранее. Когда остаётся немного, владелец получает
   сообщение в Telegram — до того, как лимит кончился.

3. Экономия времени после исчерпания. Если лимит уже упёрся, нет смысла
   заново создавать сессию Appium, запускать приложение и вводить номер
   (это почти минута) только чтобы получить тот же отказ. Мы отвечаем
   сразу и честно.

ЧЕСТНО О ТОЧНОСТИ
-----------------

Наш счётчик — ОЦЕНКА, а не истина. Мы считаем только запросы, сделанные
этим ботом, и не знаем о запросах, сделанных руками с планшета или из
другого места под тем же аккаунтом. Поэтому счётчик используется для
предупреждений, а окончательным признаком исчерпания служит сама
Carmoodo: её надпись про лимит распознаётся в carmoodo_vin.py и
приходит сюда как reason="daily_limit". Вот ЕЙ мы верим безоговорочно —
и после неё блокируем шаг до конца суток независимо от того, что
насчитал наш счётчик.
"""

import json
import logging
import os
import time  # нужен для интервалов между сбросами рекламного ID
from datetime import date
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_counters.json")

CARMOODO_DAILY_LIMIT = 30  # "일 30건" — надпись на экране приложения
CARMOODO_WARN_AT = 25      # на этом числе предупреждаем владельца

# Сколько раз подряд Carmoodo должна сказать про лимит, прежде чем мы
# перестанем к ней ходить до конца суток. Почему не один — подробно
# объяснено в mark_limit_hit().
LIMIT_CONFIRMATIONS_REQUIRED = 2

_state: dict = {}


def _today() -> str:
    """
    Сегодняшняя дата по МЕСТНОМУ времени.

    Именно местному, а не UTC: лимит обнуляется по корейским суткам, а
    компьютер стоит в Корее. С UTC счётчик сбрасывался бы в 9 утра по
    местному — ровно посреди рабочего дня.
    """
    return date.today().isoformat()


def _load() -> None:
    global _state
    if not os.path.exists(USAGE_PATH):
        _state = {}
    else:
        try:
            with open(USAGE_PATH, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Не удалось прочитать счётчики (%s) — начинаю с нуля.", exc)
            _state = {}
    _roll_over_if_new_day()


def _save() -> None:
    tmp = USAGE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USAGE_PATH)
    except OSError as exc:
        logger.warning("Не удалось сохранить счётчики (%s).", exc)


def _roll_over_if_new_day() -> None:
    """Новые сутки — счётчик и признак исчерпания обнуляются."""
    if _state.get("date") != _today():
        if _state.get("date"):
            logger.info(
                "Новые сутки: счётчик Carmoodo сброшен (за %s было %d запросов).",
                _state.get("date"), _state.get("carmoodo_count", 0),
            )
        _state.clear()
        _state["date"] = _today()
        _state["carmoodo_count"] = 0
        _state["carmoodo_limit_hit"] = False
        _state["carmoodo_limit_signals"] = 0
        _state["warned"] = False
        _state["ads_resets"] = 0
        _state["last_ads_reset_at"] = 0
        _save()


def record_carmoodo_request() -> int:
    """
    Отметить, что запрос к Carmoodo был реально выполнен.

    Вызывается, только когда поиск в приложении ДОШЁЛ до ответа (нашлась
    машина, не нашлась, показан экран выбора трима). Технические сбои —
    планшет отвалился, сессия не создалась — не считаем: до самого поиска
    там дело не дошло, а значит и запрос из дневной квоты не потрачен.

    :return: сколько запросов потрачено сегодня
    """
    _roll_over_if_new_day()
    _state["carmoodo_count"] = _state.get("carmoodo_count", 0) + 1

    # mark_limit_hit() блокирует шаг только после ДВУХ подтверждений
    # ПОДРЯД, поэтому счётчик подтверждений нужно где-то обнулять. Иначе
    # одно ложное срабатывание утром и одно вечером (между ними — десяток
    # нормальных запросов) сложились бы в двойку и выключили Carmoodo до
    # полуночи, а вместе с ней и PARTSNUMBER.
    #
    # Дошедший до ответа запрос — прямое доказательство, что лимит НЕ
    # исчерпан. Значит, накопленные подтверждения устарели: обнуляем.
    if _state.get("carmoodo_limit_signals"):
        logger.info(
            "Запрос к Carmoodo прошёл нормально — накопленные признаки "
            "исчерпания лимита (%d) сбрасываю: они не подтвердились.",
            _state["carmoodo_limit_signals"],
        )
        _state["carmoodo_limit_signals"] = 0

    _save()
    return _state["carmoodo_count"]


def mark_limit_hit() -> None:
    """
    Carmoodo сообщила, что лимит исчерпан.

    ВАЖНО — ПОЧЕМУ НЕ БЛОКИРУЕМ С ПЕРВОГО РАЗА. Признак исчерпания
    распознаётся так: поиск не дал результата за отведённое время, И на
    экране при этом видна надпись про дневной лимит. Это разумный
    признак, но он ВЫВОДНОЙ — он опирается на ветку таймаута. Достаточно
    одного неудачного стечения обстоятельств (приложение подтормозило, а
    надпись про лимит осталась висеть с прошлого раза), чтобы признак
    сработал ошибочно.

    А цена ошибки здесь несимметрично велика: блокировка действует до
    полуночи и выключает не только VIN, но и PARTSNUMBER, который без VIN
    не работает. То есть одно неверное распознавание способно отнять две
    трети функциональности на весь оставшийся день — и, что хуже, это
    выглядело бы как настоящее исчерпание лимита, никто бы не стал искать
    ошибку.

    Поэтому блокируем только после ДВУХ подтверждений подряд. Настоящий
    лимит подтвердится на следующем же запросе — цена ровно одна впустую
    потраченная минута, один раз за сутки. А случайное срабатывание
    просто не подтвердится и никого не заблокирует.
    """
    _roll_over_if_new_day()
    confirmations = int(_state.get("carmoodo_limit_signals", 0)) + 1
    _state["carmoodo_limit_signals"] = confirmations
    _save()

    if confirmations < LIMIT_CONFIRMATIONS_REQUIRED:
        logger.warning(
            "Carmoodo сообщила об исчерпании лимита (подтверждение %d из %d). "
            "Пока НЕ блокирую шаг: одиночный признак может быть ложным, а "
            "ошибочная блокировка стоила бы двух третей функциональности до "
            "полуночи. Если лимит настоящий, следующий запрос это подтвердит.",
            confirmations, LIMIT_CONFIRMATIONS_REQUIRED,
        )
        return

    if not _state.get("carmoodo_limit_hit"):
        logger.warning(
            "Carmoodo подтвердила исчерпание дневного лимита (%d раза подряд). "
            "Наш счётчик показывал %d из %d. До конца суток шаг Carmoodo "
            "пропускаем — нет смысла тратить минуту на заведомо отказной запрос.",
            confirmations, _state.get("carmoodo_count", 0), CARMOODO_DAILY_LIMIT,
        )
    _state["carmoodo_limit_hit"] = True
    _save()


def limit_reached() -> bool:
    """Исчерпан ли лимит (по слову самой Carmoodo)."""
    _roll_over_if_new_day()
    return bool(_state.get("carmoodo_limit_hit"))


def carmoodo_used() -> int:
    _roll_over_if_new_day()
    return int(_state.get("carmoodo_count", 0))


def should_warn() -> bool:
    """
    Пора ли предупредить владельца о близком исчерпании.

    Возвращает True РОВНО ОДИН РАЗ за сутки: предупреждение полезно, а
    повторяющееся каждые пять минут напоминание — это уже шум, который
    начинают игнорировать вместе со всеми остальными.
    """
    _roll_over_if_new_day()
    if _state.get("warned"):
        return False
    if carmoodo_used() >= CARMOODO_WARN_AT or limit_reached():
        _state["warned"] = True
        _save()
        return True
    return False


def status_line() -> str:
    """Строка для /status."""
    used = carmoodo_used()
    if limit_reached():
        return f"🔴 Carmoodo: дневной лимит исчерпан (потрачено ~{used} из {CARMOODO_DAILY_LIMIT})"
    left = max(0, CARMOODO_DAILY_LIMIT - used)
    icon = "🟡" if left <= (CARMOODO_DAILY_LIMIT - CARMOODO_WARN_AT) else "🟢"
    line = f"{icon} Carmoodo: потрачено ~{used} из {CARMOODO_DAILY_LIMIT} (осталось ~{left})"
    resets = ads_resets_today()
    if resets:
        line += f"\n🔄 Сбросов рекламного ID за сутки: {resets} из {MAX_ADS_RESETS_PER_DAY}"
    return line


_load()


# ---------------------------------------------------------------------------
# Сбросы рекламного ID (снятие дневного лимита HeyDealer)
# ---------------------------------------------------------------------------

HEYDEALER_DAILY_LIMIT = 10  # проверок номеров в сутки на устройство

try:
    MAX_ADS_RESETS_PER_DAY = int(os.getenv("MAX_ADS_RESETS_PER_DAY", "4"))
except ValueError:
    MAX_ADS_RESETS_PER_DAY = 4
# Значение по умолчанию — 4, то есть примерно 40 проверок HeyDealer в
# сутки. Это НЕ ограничение сервиса, а наш собственный предохранитель.
# Зачем он нужен: сброс запускается автоматически, а любая автоматика,
# запускающая сама себя по условию, способна зациклиться. Если признак
# "лимит исчерпан" начнёт срабатывать ошибочно (например, приложение
# после обновления станет показывать похожий экран по другому поводу),
# без ограничителя бот принялся бы сбрасывать идентификатор в цикле —
# десятки раз в час. Для сервиса это выглядит ровно как попытка обхода
# защиты, то есть лечение стало бы опаснее болезни.
#
# Сколько ставить: считай по формуле "нужно проверок в сутки / 10".
# Тридцать машин в день — ставь 3-4, шестьдесят — 6-7. Смысл предела не в
# том, чтобы ограничить работу, а в том, чтобы поймать взбесившуюся
# автоматику: упор в него при нормальной нагрузке почти наверняка
# означает ошибку распознавания, и в лог он пишется как заметное
# предупреждение, а не как рядовое событие.
# Меняется строкой MAX_ADS_RESETS_PER_DAY в .env, без правки кода.

try:
    MIN_SECONDS_BETWEEN_ADS_RESETS = int(os.getenv("MIN_SECONDS_BETWEEN_ADS_RESETS", "300"))
except ValueError:
    MIN_SECONDS_BETWEEN_ADS_RESETS = 300
# Второй предохранитель — от быстрого зацикливания внутри одного часа.
# При нормальной работе дневной лимит расходуется минимум за несколько
# проверок, то есть за много минут; два сброса подряд с интервалом в
# секунды означают ошибку, а не работу.
#
# НА ВРЕМЯ ОТЛАДКИ ставь 0 в .env: тогда паузы нет вовсе и можно гонять
# /resetads подряд, не дожидаясь пяти минут между попытками. Для
# рабочего режима верни 300 — иначе единственной защитой от зацикливания
# останется дневной предел, а он срабатывает слишком поздно.


def can_reset_ads() -> Tuple[bool, str]:
    """
    Можно ли сейчас сбрасывать рекламный ID.

    :return: (можно ли, причина отказа для лога)
    """
    _roll_over_if_new_day()

    done_today = int(_state.get("ads_resets", 0))
    if done_today >= MAX_ADS_RESETS_PER_DAY:
        return False, (
            f"за сутки уже выполнено сбросов: {done_today} из "
            f"{MAX_ADS_RESETS_PER_DAY}. Это предохранитель от зацикливания — "
            f"столько сбросов честной работой не набрать, так что, скорее "
            f"всего, признак исчерпания лимита распознаётся ошибочно"
        )

    last = float(_state.get("last_ads_reset_at", 0))
    elapsed = time.time() - last
    # MIN_SECONDS_BETWEEN_ADS_RESETS = 0 означает "проверку не делать
    # вовсе" — режим отладки, см. пояснение у константы.
    if MIN_SECONDS_BETWEEN_ADS_RESETS and last and elapsed < MIN_SECONDS_BETWEEN_ADS_RESETS:
        return False, (
            f"предыдущий сброс был {int(elapsed)} сек назад, минимальный "
            f"интервал — {MIN_SECONDS_BETWEEN_ADS_RESETS} сек"
        )

    return True, ""


def record_ads_reset() -> int:
    """Отметить выполненный сброс. Возвращает их число за сутки."""
    _roll_over_if_new_day()
    _state["ads_resets"] = int(_state.get("ads_resets", 0)) + 1
    _state["last_ads_reset_at"] = time.time()
    _save()
    return _state["ads_resets"]


def ads_resets_today() -> int:
    _roll_over_if_new_day()
    return int(_state.get("ads_resets", 0))
