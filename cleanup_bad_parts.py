# -*- coding: utf-8 -*-
"""
cleanup_bad_parts.py
====================

Разовая чистка базы от испорченных карточек PARTSNUMBER.

ЗАЧЕМ ЭТО ОТДЕЛЬНЫМ СКРИПТОМ

При сбое копирования (модалка не открылась, и выделение захватило
главную страницу каталога под ней) в базу могли попасть карточки вида:

    Enter a number: Enter a name
    EN: Kia
    Notepad: Models
    Main group number: 20-201: Sub engine assy

При сохранении такой текст не проходит проверку на якорь "Search..." и
до базы не доходит. Но раздел "parts" хранится
БЕССРОЧНО — карточка описывает машину такой, какой она сошла с
конвейера, и меняться не может, — поэтому уже сохранённые испорченные
записи живут в базе сами по себе и выдаются мгновенно, вообще без
обращения к PARTSNUMBER.

В pipeline.py стоит проверка при чтении, и она такие записи перехватит и
забудет по одной, по мере обращений. Этот скрипт делает то же самое
разом и сразу — чтобы не ждать, пока каждую машину кто-нибудь запросит,
и чтобы было видно масштаб: сколько записей испорчено.

КАК ЗАПУСТИТЬ

    python cleanup_bad_parts.py           # только показать, что нашлось
    python cleanup_bad_parts.py --delete  # показать и удалить

Без --delete скрипт НИЧЕГО не меняет. Так сделано намеренно: сначала
смотрим, что именно он собрался удалить, и только потом разрешаем.

Удаляются ТОЛЬКО разделы "parts". VIN и история ремонта не трогаются —
они добывались другими путями и этой ошибкой не затронуты. Потеря
карточки PARTSNUMBER не стоит ничего из дневных лимитов: этот шаг идёт
через браузер и никаких квот не расходует, машина просто перепроверится
при следующем запросе.
"""

import sqlite3
import sys

from partsnumber_date import CATALOG_PAGE_MARKERS, looks_like_catalog_page_text
from storage import DB_PATH


def main() -> int:
    delete = "--delete" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT plate_number, value FROM vehicle_data WHERE section = 'parts'"
    ).fetchall()

    bad = [r for r in rows if looks_like_catalog_page_text(r["value"])]

    print(f"База: {DB_PATH}")
    print(f"Карточек PARTSNUMBER всего: {len(rows)}")
    print(f"Из них испорченных: {len(bad)}")

    if not bad:
        print("\nЧистить нечего — все карточки выглядят нормально.")
        conn.close()
        return 0

    print("\nИспорченные записи (по каким признакам опознаны):")
    for row in bad:
        hits = [m for m in CATALOG_PAGE_MARKERS if m in row["value"]]
        print(f"  {row['plate_number']:14s} — {', '.join(hits)}")

    if not delete:
        print(
            "\nНичего не удалено (режим просмотра).\n"
            "Если список выше выглядит правильно, запусти с ключом --delete:\n"
            "    python cleanup_bad_parts.py --delete"
        )
        conn.close()
        return 0

    conn.executemany(
        "DELETE FROM vehicle_data WHERE plate_number = ? AND section = 'parts'",
        [(row["plate_number"],) for row in bad],
    )
    conn.commit()
    conn.close()

    print(
        f"\nУдалено записей: {len(bad)}. Эти машины будут перепроверены в "
        f"PARTSNUMBER при следующем запросе (дневных лимитов это не стоит — "
        f"шаг идёт через браузер)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
