"""
Сводка по результатам работы бота — в отличие от monitor.py, который
следит за оборудованием.

ЗАЧЕМ ЭТОТ МОДУЛЬ. monitor.py проверяет планшет, Appium, клавиатуру,
буфер Horizon — то есть железо. Этот модуль следит за делом: доля полных
ответов, частота отказов по каждой части, расход суточных лимитов. Без
него всё это видно только при ручном разборе логов: неполные ответы не
вызывают сигнала, и владелец узнаёт о поломках в тот момент, когда они
случаются у него на глазах, — то есть самым дорогим способом из
возможных.

ЧТО СЧИТАЕТСЯ УДАЧЕЙ. Раздел засчитывается полным при статусе "ok" или
"empty". "empty" — это ОТВЕТ ПО СУЩЕСТВУ («истории ремонта нет», «машины
нет в каталоге»), а не сбой: считать его неудачей значило бы пугать
владельца исправной работой. Неудача — только "error".

ПОЧЕМУ ОШИБКИ ЗДЕСЬ НЕ ФАТАЛЬНЫ. Статистика — побочный продукт. Сломанная
запись в базу не имеет права отменить ответ человеку, который ждёт его
две минуты.
"""
import logging
import time
from typing import Optional

import storage

logger = logging.getLogger(__name__)

# Разделы отчёта в том же порядке, в каком они идут человеку.
SECTIONS = (
    ("repair", "HeyDealer", "история ремонта"),
    ("vin", "Carmoodo", "VIN"),
    ("parts", "PARTSNUMBER", "детали и дата"),
)

_UDACHNYE = ("ok", "empty")


def _ensure_table() -> None:
    conn = storage._connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS check_outcome (
            at      REAL NOT NULL,
            plate   TEXT NOT NULL,
            vin     TEXT NOT NULL,
            repair  TEXT NOT NULL,
            parts   TEXT NOT NULL,
            seconds REAL NOT NULL
        )
        """
    )
    conn.commit()


def record(plate: str, vin: str, repair: str, parts: str,
           seconds: float, at: Optional[float] = None) -> None:
    """Запомнить итог одной проверки. Никогда не поднимает исключений."""
    try:
        with storage._lock:
            _ensure_table()
            conn = storage._connect()
            conn.execute(
                "INSERT INTO check_outcome (at, plate, vin, repair, parts, seconds) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (at if at is not None else time.time(),
                 plate, vin or "error", repair or "error", parts or "error",
                 float(seconds)),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Не удалось записать статистику проверки: %s", exc)


def _day_bounds(when: Optional[float] = None):
    now = time.localtime(when or time.time())
    start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    return start, start + 86400.0


def summary_for(when: Optional[float] = None) -> str:
    """
    Сводка за сутки — готовым текстом для Telegram.

    Считает не «сколько раз что-то упало», а то, что видит человек:
    сколько проверок дали ПОЛНЫЙ ответ, а в неполных — какой части не
    хватило. Именно по этому числу и надо судить, стало лучше или хуже.
    """
    try:
        start, end = _day_bounds(when)
        with storage._lock:
            _ensure_table()
            rows = storage._connect().execute(
                "SELECT vin, repair, parts, seconds FROM check_outcome "
                "WHERE at >= ? AND at < ?",
                (start, end),
            ).fetchall()
    except Exception as exc:
        logger.warning("Не удалось собрать сводку: %s", exc)
        return "📊 Сводку собрать не удалось — подробности в логе."

    den = time.strftime("%d.%m", time.localtime(start))
    if not rows:
        return f"📊 Сводка за {den}: проверок не было."

    vsego = len(rows)
    polnyh = 0
    otkazy = {key: 0 for key, _, _ in SECTIONS}
    for row in rows:
        znacheniya = {"vin": row["vin"], "repair": row["repair"], "parts": row["parts"]}
        if all(znacheniya[key] in _UDACHNYE for key, _, _ in SECTIONS):
            polnyh += 1
        else:
            for key, _, _ in SECTIONS:
                if znacheniya[key] not in _UDACHNYE:
                    otkazy[key] += 1

    srednee = round(sum(r["seconds"] for r in rows) / vsego)
    dolya = round(polnyh / vsego * 100)

    stroki = [
        f"📊 Сводка за {den}",
        # Формулировка «полных: N», а не «N полных» — сознательно: она не
        # требует согласования числительного и одинаково верна для 1, 2 и 5.
        f"Проверок: {vsego}, полных: {polnyh} ({dolya}%).",
        f"Среднее время проверки: {srednee} сек.",
    ]

    nepolnyh = vsego - polnyh
    if nepolnyh:
        stroki.append(f"Неполных: {nepolnyh}. Чего не хватило:")
        for key, istochnik, chelovecheski in SECTIONS:
            if otkazy[key]:
                stroki.append(f"  • {chelovecheski} ({istochnik}): {otkazy[key]}")

    return "\n".join(stroki)


# ---------------------------------------------------------------------------
# Отметка «сводка за день уже отправлена»
#
# Держать отметку в памяти монитора нельзя: она сбрасывалась бы при
# каждом перезапуске бота, и сводка уходила бы повторно (на живой работе
# так и случалось — дважды за вечер, в 21:21 и в 21:31). А watchdog
# перезапускает бота сам, после любого падения, так что дубли были бы
# вопросом времени.
#
# Храним в той же базе, что и сами проверки: она уже есть, уже переживает
# перезапуск и уже копируется в резервные копии.
# ---------------------------------------------------------------------------

def _ensure_meta_table() -> None:
    conn = storage._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()


_SUMMARY_KEY = "last_summary_day"


def summary_already_sent(day: str) -> bool:
    """Уходила ли сводка за этот день (день в виде ГГГГ-ММ-ДД)."""
    try:
        with storage._lock:
            _ensure_meta_table()
            row = storage._connect().execute(
                "SELECT value FROM meta WHERE key=?", (_SUMMARY_KEY,)
            ).fetchone()
        return row is not None and row["value"] == day
    except Exception as exc:
        # Не смогли прочитать — считаем, что не отправляли. Лишняя сводка
        # безобиднее пропавшей.
        logger.warning("Не удалось прочитать отметку о сводке: %s", exc)
        return False


def mark_summary_sent(day: str) -> None:
    """Запомнить, что сводка за этот день отправлена."""
    try:
        with storage._lock:
            _ensure_meta_table()
            conn = storage._connect()
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_SUMMARY_KEY, day),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Не удалось запомнить отметку о сводке: %s", exc)
