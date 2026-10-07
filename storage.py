# -*- coding: utf-8 -*-
"""
storage.py
==========

База данных пробитых автомобилей.

ПОЧЕМУ ЭТО ТЕПЕРЬ САМЫЙ ВАЖНЫЙ МОДУЛЬ ПРОЕКТА
----------------------------------------------

У HeyDealer есть лимит: 10 проверок номеров в сутки на устройство,
и обойти его нельзя (переустановка не помогает, а попытка удалить
аккаунт кнопкой 탈퇴 блокирует устройство на три месяца). Вместе с
лимитом Carmoodo в 30 запросов это означает: каждая проверка нового
номера — дефицитный расходуемый ресурс.

Отсюда простое следствие: НИ ОДНА уже сделанная проверка не должна
пропадать. Кэш здесь не удобство («быстрее ответим»), а способ вообще
уместиться в дневную квоту. Одна и та же машина
пересматривается дилерами по нескольку раз, и каждый повторный просмотр,
взятый из базы, — это ещё одна новая машина, которую удастся пробить
сегодня.

ПОЧЕМУ SQLITE, А НЕ JSON-ФАЙЛ
-----------------------------

JSON-файл пришлось бы целиком переписывать после каждого запроса. Пока
записей десятки, это нормально; когда их станут тысячи — каждая
проверка начнёт переписывать мегабайты, а любое неудачное выключение
будет рисковать всем накопленным сразу.

SQLite решает это тем, что он уже есть в Python (ничего не нужно
устанавливать) и:
  * пишет только изменённую строку, а не весь файл;
  * гарантирует целостность при внезапном выключении;
  * позволяет спрашивать «сколько всего машин пробито», «сколько
    проверок сэкономлено» — то есть видеть отдачу от базы в цифрах.

СРОКИ ХРАНЕНИЯ: РАЗНЫЕ ДЛЯ РАЗНЫХ ДАННЫХ
-----------------------------------------

Ключевая мысль: почти всё, что мы добываем, НЕ МЕНЯЕТСЯ СО ВРЕМЕНЕМ, и
хранить это неделю вместо вечности — значит выбрасывать дефицитный
ресурс на ветер.

  * VIN — связка «номер ↔ VIN» физически не может измениться: это
    свойство конкретного автомобиля. Храним БЕССРОЧНО.

  * Карточка PARTSNUMBER (детали, дата выпуска) — определяется по VIN и
    описывает, каким автомобиль сошёл с конвейера. Тоже не меняется.
    Храним БЕССРОЧНО.

  * История ремонта — единственное, что реально может пополниться:
    машина съездила на сервис, появилась новая запись. Но происходит это
    раз в месяцы, а не в дни. Срок задаётся в .env
    (REPAIR_HISTORY_TTL_DAYS), по умолчанию 30 дней.

Общий короткий срок (например, 6 часов) означал бы, что вчерашняя
проверка сегодня уже бесполезна, — при лимите в 10 запросов это
непозволительно.

В базу попадают ТОЛЬКО успешные результаты. Отрицательный ответ («не
найдено») мог быть вызван временной причиной — исчерпанным лимитом,
неопознанной комплектацией, — и запомнить его надолго означало бы
надолго повторять человеку неверный ответ.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vehicles.db")
LEGACY_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_plates.json")

DAY = 24 * 60 * 60

# Сколько дней хранить историю ремонта. Единственные данные, которые
# могут пополниться со временем. Настраивается в .env — если окажется,
# что дилерам важна совсем свежая история, срок можно уменьшить, не
# трогая код.
try:
    REPAIR_TTL_DAYS = int(os.getenv("REPAIR_HISTORY_TTL_DAYS", "30"))
except ValueError:
    REPAIR_TTL_DAYS = 30

# None означает «бессрочно» (см. объяснение в шапке файла).
SECTION_TTL = {
    "vin": None,
    "parts": None,
    "repair": REPAIR_TTL_DAYS * DAY,
}

# SQLite в Python по умолчанию запрещает работу с соединением из другого
# потока. У нас же обращения приходят и из основного цикла бота, и из
# рабочих потоков шагов, поэтому соединение открывается с
# check_same_thread=False, а от одновременной записи защищает обычный
# лок. Это проще и надёжнее, чем заводить по соединению на поток:
# запись у нас редкая (несколько раз за проверку), и бороться за
# параллелизм тут не за чем.
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn

    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    # WAL — режим журналирования, при котором чтение не блокируется
    # записью. Для нас важнее другое его свойство: при внезапном
    # отключении питания база остаётся целой, а не превращается в
    # обрубок. Бот перезапускается watchdog'ом, так что это не теория.
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")

    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_data (
            plate_number TEXT NOT NULL,
            section      TEXT NOT NULL,
            value        TEXT NOT NULL,   -- JSON
            created_at   REAL NOT NULL,
            PRIMARY KEY (plate_number, section)
        )
        """
    )
    # Журнал проверок: сколько раз запрашивали и сколько раз ответили из
    # базы. Нужен, чтобы видеть отдачу в цифрах — при дефицитных лимитах
    # это уже не любопытство, а рабочий показатель.
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lookup_log (
            plate_number TEXT NOT NULL,
            at           REAL NOT NULL,
            from_cache   INTEGER NOT NULL
        )
        """
    )
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_lookup_at ON lookup_log(at)")
    _conn.commit()
    return _conn


def get_with_age(plate_number: str, section: str) -> Optional[tuple]:
    """
    Достать раздел ВМЕСТЕ со временем его получения.

    Время нужно отчёту, чтобы сказать, на какой момент данные проверены.
    Замер по боевой базе показал, что типичная запись истории ремонта
    отдаётся в возрасте 22 дней (медиана; максимум 24 из разрешённых 30) —
    то есть повторная проверка почти всегда возвращает историю
    трёхнедельной давности, и человек, покупающий по этому отчёту машину,
    должен об этом знать.

    Срок годности здесь проверяется ТОТ ЖЕ, что и в get(): возраст выдаётся
    ради честности отчёта, а не ради обхода срока.

    :return: (значение, время получения) или None
    """
    ttl = SECTION_TTL.get(section)
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT value, created_at FROM vehicle_data WHERE plate_number=? AND section=?",
            (plate_number, section),
        ).fetchone()

    if row is None:
        return None
    if ttl is not None and time.time() - row["created_at"] > ttl:
        logger.debug("Раздел %s для %s устарел — перепроверим.", section, plate_number)
        return None
    try:
        return json.loads(row["value"]), row["created_at"]
    except json.JSONDecodeError:
        logger.warning("Испорченная запись в базе (%s/%s) — игнорирую.", plate_number, section)
        return None


def get(plate_number: str, section: str) -> Optional[Any]:
    """
    Достать раздел из базы, если он есть и не устарел.

    :return: сохранённое значение или None
    """
    found = get_with_age(plate_number, section)
    return None if found is None else found[0]


def put(plate_number: str, section: str, value: Any) -> None:
    """Сохранить раздел. Вызывается только для УСПЕШНЫХ результатов."""
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO vehicle_data (plate_number, section, value, created_at) "
            "VALUES (?, ?, ?, ?)",
            (plate_number, section, json.dumps(value, ensure_ascii=False), time.time()),
        )
        conn.commit()


def forget(plate_number: str, section: Optional[str] = None) -> int:
    """
    Забыть сохранённое — чтобы принудительно перепроверить машину.

    Нужно, когда данные заведомо устарели: например, машина только что
    была на сервисе, и дилеру нужна свежая история ремонта, а не
    сохранённая месяц назад.

    :param section: конкретный раздел или None — забыть всё про номер
    :return: сколько записей удалено
    """
    with _lock:
        conn = _connect()
        if section:
            cur = conn.execute(
                "DELETE FROM vehicle_data WHERE plate_number=? AND section=?",
                (plate_number, section),
            )
        else:
            cur = conn.execute("DELETE FROM vehicle_data WHERE plate_number=?", (plate_number,))
        conn.commit()
        return cur.rowcount


def record_lookup(plate_number: str, from_cache: bool) -> None:
    """Отметить проверку в журнале — для подсчёта сэкономленных запросов."""
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO lookup_log (plate_number, at, from_cache) VALUES (?, ?, ?)",
            (plate_number, time.time(), 1 if from_cache else 0),
        )
        conn.commit()


def stats() -> dict:
    """Цифры для /status."""
    with _lock:
        conn = _connect()
        vehicles = conn.execute(
            "SELECT COUNT(DISTINCT plate_number) AS n FROM vehicle_data"
        ).fetchone()["n"]
        today_start = time.time() - DAY
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(from_cache) AS cached "
            "FROM lookup_log WHERE at > ?",
            (today_start,),
        ).fetchone()

    total = row["total"] or 0
    cached = row["cached"] or 0
    return {
        "vehicles": vehicles,
        "lookups_24h": total,
        "from_cache_24h": cached,
        "saved_24h": cached,
    }


def stats_line() -> str:
    """Строка для /status."""
    s = stats()
    line = f"💾 В базе машин: {s['vehicles']}"
    if s["lookups_24h"]:
        line += (
            f"\nЗа сутки проверок: {s['lookups_24h']}, "
            f"из них без запроса к сервисам: {s['from_cache_24h']}"
        )
    return line


def _migrate_legacy_cache() -> None:
    """
    Переносит записи из старого JSON-кэша, если он остался.

    Разовая операция при переходе со старого формата хранения. Ценность прямая:
    там лежат уже пробитые машины, а каждая пробитая машина — это
    потраченный запрос из дневного лимита. Выбрасывать их только потому,
    что поменялся формат хранения, было бы расточительством.

    Старый файл после переноса переименовывается, а не удаляется: если
    что-то пойдёт не так, данные можно достать руками.
    """
    if not os.path.exists(LEGACY_CACHE_PATH):
        return
    try:
        with open(LEGACY_CACHE_PATH, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Старый кэш не удалось прочитать (%s) — пропускаю перенос.", exc)
        return

    moved = 0
    for plate, sections in legacy.items():
        if not isinstance(sections, dict):
            continue
        for section, entry in sections.items():
            # Запись без значения переносить нечего.
            if not isinstance(entry, dict) or entry.get("value") is None:
                continue
            value = entry.get("value")
            if value is None:
                continue
            # Время создания берём из старой записи: так сроки хранения
            # считаются честно, а не «как будто пробили только что».
            created = entry.get("at", time.time())
            with _lock:
                conn = _connect()
                conn.execute(
                    "INSERT OR IGNORE INTO vehicle_data "
                    "(plate_number, section, value, created_at) VALUES (?, ?, ?, ?)",
                    (plate, section, json.dumps(value, ensure_ascii=False), created),
                )
                conn.commit()
            moved += 1

    try:
        os.replace(LEGACY_CACHE_PATH, LEGACY_CACHE_PATH + ".migrated")
    except OSError:
        pass
    if moved:
        logger.info("Перенесено из старого кэша записей: %d (файл сохранён как *.migrated).", moved)


def init() -> None:
    """Открыть базу и перенести старый кэш. Вызывается при старте бота."""
    _connect()
    _migrate_legacy_cache()
    s = stats()
    ttl_text = "бессрочно" if SECTION_TTL["repair"] is None else f"{REPAIR_TTL_DAYS} дн."
    logger.info(
        "База пробитых машин готова: %s (машин: %d). Сроки хранения: "
        "VIN и детали — бессрочно, история ремонта — %s.",
        DB_PATH, s["vehicles"], ttl_text,
    )


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
