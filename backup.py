"""
Резервные копии базы проверенных машин.

ЗАЧЕМ ЭТОТ МОДУЛЬ. Каждая запись в базе — это запрос, который больше не
придётся тратить из дефицитных суточных лимитов: 10 проверок у HeyDealer,
30 у Carmoodo. Терять базу нельзя, её нужно копировать отдельно.

Без резервных копий месяцы накопленной квоты жили бы в одном файле, и
первая же поломка диска, случайное удаление или битая запись стоили бы
их целиком.

ПОЧЕМУ НЕ ПРОСТО СКОПИРОВАТЬ ФАЙЛ. База работает в режиме WAL (см.
storage._connect): часть свежих данных лежит не в vehicles.db, а в
соседнем vehicles.db-wal. Копия, снятая обычным copyfile во время записи,
может оказаться неполной или битой — причём молча, и обнаружится это
ровно тогда, когда копия понадобится. Поэтому копируем встроенным
механизмом SQLite (Connection.backup): он согласован с пишущими и даёт
целостный снимок без остановки бота.

ПОЧЕМУ ОШИБКИ ЗДЕСЬ НЕ ФАТАЛЬНЫ. Резервное копирование — страховка, а не
основная работа. Упасть из-за того, что не удалось сделать копию, значит
обменять работающего бота на неработающего ради гипотетической будущей
поломки. Поэтому все ошибки только логируются, а проверки машин
продолжаются.
"""
import logging
import os
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Сколько поколений хранить. Четырнадцать — это две недели: достаточно,
# чтобы заметить порчу данных и откатиться к здоровой копии, и достаточно
# мало, чтобы не забить диск (одна копия сейчас около 80 КБ).
KEEP_COPIES = 14

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
_PREFIX = "vehicles-"
_SUFFIX = ".db"


def _backup_name(when: Optional[float] = None) -> str:
    stamp = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    return f"{_PREFIX}{stamp}{_SUFFIX}"


def make_backup(db_path: str, backup_dir: str = BACKUP_DIR) -> str:
    """
    Снять целостную копию базы. Возвращает путь к копии.

    Исключения НЕ подавляет: вызывающий код (backup_if_due) решает, что с
    ними делать. Такое разделение позволяет тестировать сам механизм
    копирования отдельно от политики «падать или не падать».
    """
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, _backup_name())

    source = sqlite3.connect(db_path)
    try:
        # Пишем во временный файл и переименовываем: если копирование
        # оборвётся на середине, в каталоге не останется файла, который
        # выглядит готовой копией, но ею не является.
        temporary = target + ".part"
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
        finally:
            destination.close()
        os.replace(temporary, target)
    finally:
        source.close()

    return target


def cleanup_old(backup_dir: str = BACKUP_DIR, keep: int = KEEP_COPIES) -> int:
    """
    Оставить последние `keep` копий, остальные удалить.

    Сортировка по ИМЕНИ, а не по времени файла: имя содержит дату в виде
    ГГГГ-ММ-ДД, поэтому лексикографический порядок совпадает с
    хронологическим и не зависит от того, что случилось с отметками
    времени при копировании папки на другой диск.

    :return: сколько копий удалено
    """
    if not os.path.isdir(backup_dir):
        return 0

    copies = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith(_PREFIX) and f.endswith(_SUFFIX)
    )
    izlishek = copies[:-keep] if keep > 0 else copies
    udaleno = 0
    for name in izlishek:
        try:
            os.remove(os.path.join(backup_dir, name))
            udaleno += 1
        except OSError as exc:
            logger.warning("Не удалось удалить старую копию %s: %s", name, exc)
    return udaleno


def backup_if_due(db_path: str, backup_dir: str = BACKUP_DIR) -> Optional[str]:
    """
    Сделать копию, если сегодняшней ещё нет. Ошибки не поднимает.

    Вызывается из обхода монитора (раз в 5 минут) — проверка «копия за
    сегодня уже есть» стоит одного обращения к файловой системе, поэтому
    частый вызов ничего не стоит, а отдельный планировщик не нужен.

    :return: путь к созданной копии или None, если копировать не пришлось
    """
    try:
        today = os.path.join(backup_dir, _backup_name())
        if os.path.exists(today):
            return None

        if not os.path.exists(db_path):
            logger.warning(
                "Резервное копирование пропущено: базы %s нет на месте.", db_path
            )
            return None

        path = make_backup(db_path, backup_dir)
        udaleno = cleanup_old(backup_dir)
        logger.info(
            "Резервная копия базы сохранена: %s (старых копий удалено: %d, "
            "хранится последние %d).",
            path, udaleno, KEEP_COPIES,
        )
        return path
    except Exception as exc:
        # Страховка не имеет права ронять основную работу — см. шапку.
        logger.warning(
            "Не удалось сделать резервную копию базы (%s). Бот продолжает "
            "работу, но копии за сегодня нет — стоит разобраться.", exc
        )
        return None
