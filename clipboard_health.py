"""
Состояние канала буфера обмена Horizon — жив или нет.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Читать это состояние должен diagnostics (чтобы
монитор оповестил владельца, а /diag показал), а писать —
partsnumber_date. Импортировать partsnumber_date в diagnostics нельзя:
он тянет за собой Playwright, а diagnostics зовут при каждом /diag.
Отдельный модуль без зависимостей развязывает это.

ЗАЧЕМ ЭТО НУЖНО. Канал буфера может отказывать массово (наблюдалось 88
отказов за день и 11 провалов PARTSNUMBER из 20). Без сигнала это видно
только в логе, а владелец не получает ни одного сообщения — день
теряется молча. Само по себе состояние
ничего не чинит: канал ломается на стороне Horizon, и замера, который
сказал бы, что там чинить, у нас нет. Задача этого модуля — только
сделать поломку видимой.

Состояние живёт в памяти процесса: бот, PARTSNUMBER и монитор работают
в одном процессе, а после перезапуска канал всё равно проверяется
заново.
"""
import threading
import time
from typing import Optional

_lock = threading.Lock()
_alive = True
_changed_at: Optional[float] = None


def mark_dead() -> None:
    """Канал признан неработающим (сработал [CLIPBOARD_DEAD])."""
    global _alive, _changed_at
    with _lock:
        if _alive:
            _alive = False
            _changed_at = time.time()


def mark_alive() -> None:
    """Буфер снова доехал — канал работает."""
    global _alive, _changed_at
    with _lock:
        if not _alive:
            _alive = True
            _changed_at = time.time()


def is_alive() -> bool:
    with _lock:
        return _alive


def last_change() -> Optional[float]:
    """Когда состояние сменилось последний раз (time.time()), или None."""
    with _lock:
        return _changed_at


def reset_for_tests() -> None:
    """Только для тестов: вернуть модуль в исходное состояние."""
    global _alive, _changed_at
    with _lock:
        _alive = True
        _changed_at = None
