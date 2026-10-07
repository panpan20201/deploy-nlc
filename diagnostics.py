# -*- coding: utf-8 -*-
"""
diagnostics.py
==============

Проверка готовности окружения.

ЗАЧЕМ ЭТО НУЖНО
---------------

У системы много внешних зависимостей, которые могут отвалиться молча:
планшет отключили от USB, Appium-сервер закрыли вместе с окном, на
планшете отключили ADBKeyBoard, кто-то удалил папку с эталонами картинок.
Ни одна из этих поломок не видна изнутри бота — он узнаёт о них только
когда падает шаг живого клиента, и выглядит это как загадочная ошибка
где-то в глубине Appium.

Здесь всё наоборот: проверки идут ЗАРАНЕЕ и говорят прямым текстом, что
именно сломано и что с этим делать. Три сценария использования:

  1. При старте бота (`run_preflight`) — сразу видно, готова ли система.
     Если планшет не подключён, узнать об этом лучше в первую секунду,
     а не через час, когда придёт первый клиент.

  2. По команде /diag из Telegram — можно проверить состояние с телефона,
     не подходя к компьютеру, а не присылать ссылку и ждать пару минут,
     чтобы понять, жива ли система.

  3. Перед шагом на планшете (`quick_device_check`) — если планшета нет,
     честно сказать об этом за долю секунды вместо трёх минут таймаутов
     и двух попыток впустую.

ПРИНЦИП: каждая проверка сообщает не только "плохо", но и ЧТО СДЕЛАТЬ.
Сообщение "устройство не найдено" бесполезно; "проверь USB-кабель и
разблокируй планшет" — полезно.
"""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

import clipboard_health
import net_check

logger = logging.getLogger(__name__)

APPIUM_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
ANDROID_UDID = os.getenv("ANDROID_UDID")

CARMOODO_PACKAGE = "kr.co.ggkucar.app"
HEYDEALER_PACKAGE = "kr.perfectree.heydealer"
ADBKEYBOARD_PACKAGE = "com.android.adbkeyboard"

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
REQUIRED_TEMPLATES = (
    "template_partsnumber_logo.png",
    "template_info_icon.png",
    "template_info_icon_small.png",
    "template_open_vin_button.png",
)

ADB_TIMEOUT_SEC = 15
APPIUM_STATUS_TIMEOUT_SEC = 5


@dataclass
class Check:
    """Результат одной проверки."""

    name: str
    ok: bool
    detail: str = ""       # что обнаружено
    fix: str = ""          # что сделать, если плохо
    critical: bool = True  # False — работает, но с ограничениями

    @property
    def icon(self) -> str:
        if self.ok:
            return "✅"
        return "❌" if self.critical else "⚠️"


# ---------------------------------------------------------------------------
# ADB и устройство
# ---------------------------------------------------------------------------

def _adb_path() -> str:
    """
    Путь к adb.

    Сначала ищем в ANDROID_HOME (так он и стоит на рабочей машине), потом
    просто "adb" в расчёте на PATH. Возвращаем строку, а не проверяем
    существование: если adb вообще нет, это выяснится при первом запуске
    и попадёт в понятное сообщение проверки.
    """
    sdk = os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT")
    if sdk:
        candidate = os.path.join(sdk, "platform-tools", "adb.exe")
        if os.path.exists(candidate):
            return candidate
        candidate = os.path.join(sdk, "platform-tools", "adb")
        if os.path.exists(candidate):
            return candidate
    return "adb"


def _run_adb(args: List[str], timeout: int = ADB_TIMEOUT_SEC) -> "tuple[int, str, str]":
    """Запускает adb и возвращает (код возврата, stdout, stderr)."""
    cmd = [_adb_path()] + args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", "adb не найден"
    except subprocess.TimeoutExpired:
        return -1, "", f"adb не ответил за {timeout} сек"
    except Exception as exc:
        return -1, "", str(exc)


def check_adb_and_device() -> Check:
    """
    Виден ли планшет и в каком он состоянии.

    Различаем три исхода, потому что чинятся они по-разному:
      * adb вообще не запускается — не установлен или не прописан путь;
      * список устройств пуст — кабель, питание, режим USB;
      * устройство есть, но в состоянии "unauthorized" — не подтверждено
        разрешение на отладку прямо на экране планшета. Это самая
        коварная ситуация: устройство как бы видно, но управлять им
        нельзя, и Appium выдаёт совершенно невнятную ошибку.
    """
    code, out, err = _run_adb(["devices"])
    if code != 0:
        return Check(
            "ADB", False,
            detail=f"adb не запускается ({err.strip() or 'неизвестная причина'})",
            fix="Проверь, что установлен Android platform-tools и переменная "
                "ANDROID_HOME указывает на папку SDK (внутри должна быть "
                "папка platform-tools с adb.exe).",
        )

    devices = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices[parts[0]] = parts[1]

    if not devices:
        return Check(
            "Планшет", False,
            detail="ни одного устройства не подключено",
            fix="Проверь USB-кабель, включи и разблокируй планшет. "
                "Затем выполни 'adb devices' — планшет должен быть в списке.",
        )

    if ANDROID_UDID:
        state = devices.get(ANDROID_UDID)
        if state is None:
            return Check(
                "Планшет", False,
                detail=f"нужный планшет ({ANDROID_UDID}) не виден; "
                       f"подключено другое: {', '.join(devices) or '—'}",
                fix="Либо подключи нужный планшет, либо обнови ANDROID_UDID "
                    "в .env (актуальный номер покажет 'adb devices').",
            )
        if state != "device":
            return Check(
                "Планшет", False,
                detail=f"состояние '{state}' вместо 'device'",
                fix="Посмотри на экран планшета: там должен висеть запрос "
                    "на разрешение отладки по USB — подтверди его "
                    "(и поставь галочку 'всегда разрешать').",
            )
        return Check("Планшет", True, detail=f"{ANDROID_UDID} — готов")

    ready = [udid for udid, state in devices.items() if state == "device"]
    if not ready:
        return Check(
            "Планшет", False,
            detail=f"устройства видны, но не готовы: {devices}",
            fix="Подтверди запрос на отладку по USB на экране планшета.",
        )
    return Check(
        "Планшет", True,
        detail=f"{', '.join(ready)} — готов (ANDROID_UDID в .env не задан)",
    )


def check_apps_installed() -> Check:
    """Установлены ли на планшете оба нужных приложения."""
    code, out, err = _run_adb(["shell", "pm", "list", "packages"])
    if code != 0:
        return Check(
            "Приложения", False,
            detail=f"не удалось получить список ({err.strip()})",
            fix="Сначала почини связь с планшетом (см. проверку выше).",
        )

    missing = [p for p in (CARMOODO_PACKAGE, HEYDEALER_PACKAGE) if p not in out]
    if missing:
        return Check(
            "Приложения", False,
            detail=f"не установлены: {', '.join(missing)}",
            fix="Установи недостающее приложение на планшет и войди в него руками.",
        )
    return Check("Приложения", True, detail="Carmoodo и HeyDealer на месте")


def check_adbkeyboard() -> Check:
    """
    ADBKeyBoard — клавиатура для ввода корейского текста.

    Без неё не работает ввод номера машины НИ В ОДНОМ из приложений:
    обычный 'adb shell input text' не умеет не-латиницу, а send_keys в
    WebView-полях Carmoodo падает. То есть отсутствие этой клавиатуры
    ломает вообще всё — и при этом снаружи выглядит как невнятная ошибка
    ввода где-то в середине сценария.
    """
    code, out, _ = _run_adb(["shell", "ime", "list", "-s"])
    if code != 0:
        return Check(
            "ADBKeyBoard", False,
            detail="не удалось проверить (нет связи с планшетом)",
            fix="Сначала почини связь с планшетом.",
        )
    if ADBKEYBOARD_PACKAGE not in out:
        return Check(
            "ADBKeyBoard", False,
            detail="клавиатура не включена",
            fix="Выполни на компьютере:\n"
                "  adb install ADBKeyboard.apk\n"
                "  adb shell ime enable com.android.adbkeyboard/.AdbIME\n"
                "  adb shell ime set com.android.adbkeyboard/.AdbIME\n"
                "Без неё не вводится корейский текст — не работает ни один шаг.",
        )

    code, current, _ = _run_adb(["shell", "settings", "get", "secure", "default_input_method"])
    if code == 0 and ADBKEYBOARD_PACKAGE not in current:
        return Check(
            "ADBKeyBoard", False,
            detail=f"установлена, но активна другая клавиатура ({current.strip()})",
            fix="Выполни: adb shell ime set com.android.adbkeyboard/.AdbIME",
        )
    return Check("ADBKeyBoard", True, detail="включена и активна")


# ---------------------------------------------------------------------------
# Appium
# ---------------------------------------------------------------------------

def check_appium() -> Check:
    """
    Отвечает ли Appium-сервер.

    Обычная причина, по которой он не отвечает, — окно сервера случайно
    закрыли. Снаружи это выглядит как "бот перестал работать", хотя сам
    бот жив и здоров.
    """
    try:
        import requests

        response = requests.get(
            f"{APPIUM_SERVER_URL.rstrip('/')}/status", timeout=APPIUM_STATUS_TIMEOUT_SEC
        )
        if response.status_code == 200:
            return Check("Appium", True, detail=f"отвечает на {APPIUM_SERVER_URL}")
        return Check(
            "Appium", False,
            detail=f"ответил кодом {response.status_code}",
            fix="Перезапусти Appium-сервер (окно 'Appium Server').",
        )
    except Exception as exc:
        return Check(
            "Appium", False,
            detail=f"не отвечает на {APPIUM_SERVER_URL} ({type(exc).__name__})",
            fix="Запусти Appium-сервер: открой новое окно командной строки "
                "и выполни 'appium'. Или перезапусти start_all.bat целиком. "
                "Скорее всего, окно сервера просто закрыли.",
        )


def appium_is_up() -> bool:
    """Короткая проверка без подробностей — для использования по ходу работы."""
    return check_appium().ok


# ---------------------------------------------------------------------------
# Файлы и настройки
# ---------------------------------------------------------------------------

def check_templates() -> Check:
    """
    Эталоны картинок для PARTSNUMBER.

    Без них модуль не сможет ничего найти на экране: он видит Horizon
    только как поток пикселей, и распознавание идёт сравнением с этими
    картинками. Пропажа файла ломает шаг полностью.
    """
    if not os.path.isdir(TEMPLATES_DIR):
        return Check(
            "Эталоны PARTSNUMBER", False,
            detail=f"папка не найдена: {TEMPLATES_DIR}",
            fix="Верни папку templates рядом с файлами бота.",
            critical=False,
        )
    missing = [n for n in REQUIRED_TEMPLATES if not os.path.exists(os.path.join(TEMPLATES_DIR, n))]
    if missing:
        return Check(
            "Эталоны PARTSNUMBER", False,
            detail=f"нет файлов: {', '.join(missing)}",
            fix="Восстанови недостающие картинки в папке templates — "
                "без них шаг PARTSNUMBER не сможет найти элементы на экране.",
            critical=False,
        )
    return Check("Эталоны PARTSNUMBER", True, detail=f"{len(REQUIRED_TEMPLATES)} файлов на месте")


def check_env() -> Check:
    """Заполнены ли важные настройки в .env."""
    try:
        import config

        problems = config.check_settings()
    except Exception as exc:
        return Check("Настройки .env", False, detail=str(exc),
                     fix="Проверь файл .env по образцу .env.example.")
    if problems:
        return Check(
            "Настройки .env", False,
            detail=f"не заполнено пунктов: {problems}",
            fix="Подробности — в логе при старте бота (строки с WARNING). "
                "Система работает, но часть возможностей отключена.",
            critical=False,
        )
    return Check("Настройки .env", True, detail="заполнено всё необходимое")


def check_disk_write() -> Check:
    """Можно ли писать рядом с ботом — тут живут кэш, логи и дампы экранов."""
    folder = os.path.dirname(os.path.abspath(__file__))
    probe = os.path.join(folder, ".write_test.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return Check("Запись на диск", True, detail="папка доступна для записи")
    except OSError as exc:
        return Check(
            "Запись на диск", False,
            detail=str(exc),
            fix="Без записи не сохраняются кэш, логи и дампы экранов — "
                "то есть разбирать будущие сбои будет не по чему. "
                "Проверь права на папку и свободное место.",
        )


def check_clipboard_channel() -> Check:
    """
    Работает ли канал буфера обмена Horizon.

    Не проверка оборудования, а состояние, замеченное последним запуском
    PARTSNUMBER: сама по себе эта функция никуда не ходит. Смысл в том,
    чтобы монитор увидел смену состояния и написал владельцу: иначе день
    с массовыми отказами буфера (десятки отказов, больше половины
    проверок PARTSNUMBER провалено) проходит молча.

    critical=False: бот работает, просто без карточки деталей и даты
    выпуска. Это ограничение, а не поломка.
    """
    if clipboard_health.is_alive():
        return Check("Буфер Horizon", True, detail="канал синхронизации работает",
                     critical=False)

    since = clipboard_health.last_change()
    when = time.strftime("%H:%M", time.localtime(since)) if since else "неизвестно когда"
    return Check(
        "Буфер Horizon", False,
        detail=f"скопированное из удалённой машины не доезжает (с {when}). "
               f"PARTSNUMBER будет падать: детали и дата выпуска недоступны.",
        fix="Канал ломается на стороне Horizon, из бота это не чинится. "
            "Помогает переподключение сессии Horizon — закрыть окно "
            "и войти заново. VIN и история ремонта при этом работают.",
        critical=False,
    )


# ---------------------------------------------------------------------------
def check_catalog_reachable() -> Check:
    """
    Открывается ли соединение с каталогом PARTSNUMBER.

    Без интернета шаг PARTSNUMBER выясняет это больше двух минут на
    попытку (131 секунду), честно отрабатывая свои таймауты. Соединение
    проверяется за доли секунды.

    critical=False: без каталога бот продолжает отдавать VIN и историю
    ремонта, то есть работает, просто неполно.

    ⚠️ Проверяется именно хост каталога, а не «интернет вообще»: бывает,
    что интернет есть (сторонний сервер отдаёт 723 КБ/с), а путь до
    каталога еле дышит на 18 КБ/с. И наоборот — открытое соединение не
    обещает, что шаг пройдёт, поэтому зелёная строка здесь означает ровно
    одно: «связь есть», а не «PARTSNUMBER сработает».
    """
    if net_check.catalog_reachable():
        return Check("Связь с каталогом", True,
                     detail=f"{net_check.catalog_host()} отвечает",
                     critical=False)
    return Check(
        "Связь с каталогом", False,
        detail=f"до {net_check.catalog_host()} не достучаться",
        fix="Проверь интернет. Пока связи нет, VIN и история ремонта будут "
            "приходить как обычно, а раздел с деталями и датой выпуска — нет.",
        critical=False,
    )


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------

def run_all_checks() -> List[Check]:
    """
    Все проверки по порядку — от нижнего слоя к верхнему.

    Порядок не случаен: если нет связи с планшетом, то проверки
    приложений и клавиатуры всё равно ничего не покажут. Идя снизу вверх,
    первой в списке окажется КОРНЕВАЯ причина, а не её последствия.
    """
    checks = [check_adb_and_device()]

    if checks[0].ok:
        checks.append(check_apps_installed())
        checks.append(check_adbkeyboard())

    checks.append(check_appium())
    checks.append(check_templates())
    checks.append(check_env())
    checks.append(check_disk_write())
    checks.append(check_clipboard_channel())
    checks.append(check_catalog_reachable())
    return checks


def quick_device_check() -> Optional[str]:
    """
    Быстрая проверка перед шагом на планшете.

    Смысл — сэкономить время и нервы: если планшета нет, шаг всё равно
    провалится, но провалится он через три минуты таймаутов и две
    попытки, а в логе будет невнятная ошибка Appium. Здесь мы узнаём об
    этом за долю секунды и можем сразу сказать понятную причину.

    :return: None, если всё в порядке; иначе текст проблемы
    """
    check = check_adb_and_device()
    if check.ok:
        return None
    return check.detail


def run_preflight() -> bool:
    """
    Проверка при старте бота. Пишет результат в лог.

    НЕ мешает боту запуститься даже при проблемах — и это осознанно:
    планшет могут подключить через минуту после запуска, а бот, который
    отказался стартовать, не сможет ни принять сообщение, ни ответить на
    /diag. Гораздо полезнее запуститься и честно рассказать о проблемах.

    :return: True, если всё в порядке
    """
    logger.info("--- Проверка готовности системы ---")
    checks = run_all_checks()
    problems = [c for c in checks if not c.ok]

    for check in checks:
        if check.ok:
            logger.info("%s %s: %s", check.icon, check.name, check.detail)
        else:
            level = logger.error if check.critical else logger.warning
            level("%s %s: %s", check.icon, check.name, check.detail)
            for line in check.fix.split("\n"):
                level("      → %s", line)

    if not problems:
        logger.info("--- Всё готово к работе ---")
        return True

    critical = [c for c in problems if c.critical]
    if critical:
        logger.error(
            "--- Система НЕ готова: критических проблем %d. Бот запустится, "
            "но проверки будут падать, пока это не исправлено. ---",
            len(critical),
        )
    else:
        logger.warning(
            "--- Система работает с ограничениями (проблем: %d) ---", len(problems)
        )
    return False


def format_report() -> str:
    """Тот же отчёт, но для отправки в Telegram (HTML)."""
    import html as html_module

    checks = run_all_checks()
    lines = ["<b>Проверка системы</b>", ""]
    for check in checks:
        lines.append(f"{check.icon} <b>{html_module.escape(check.name)}</b>: "
                     f"{html_module.escape(check.detail)}")
        if not check.ok and check.fix:
            first_fix_line = check.fix.split("\n")[0]
            lines.append(f"    ↳ {html_module.escape(first_fix_line)}")

    problems = [c for c in checks if not c.ok]
    lines.append("")
    if not problems:
        lines.append("Всё готово к работе.")
    elif any(c.critical for c in problems):
        lines.append("⛔ Есть критические проблемы — проверки будут падать.")
    else:
        lines.append("Работает с ограничениями (см. пункты выше).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Уборка
# ---------------------------------------------------------------------------

DEBUG_FILE_PATTERNS = ("carmoodo_debug_*.xml", "trim_selection_*.xml")
DEBUG_SCREENSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "debug_screenshots"
)
DEBUG_KEEP_DAYS = 14


def cleanup_old_debug_files(keep_days: int = DEBUG_KEEP_DAYS) -> int:
    """
    Удаляет старые дампы экранов и отладочные скриншоты.

    Зачем это вообще: каждый разобранный сбой оставляет после себя файл —
    XML-дамп экрана или PNG-скриншот. По одному они крошечные, но за
    месяцы работы их накапливаются тысячи, и папка проекта превращается в
    свалку, в которой уже не найти свежий дамп по нужному сбою. А
    скриншоты Horizon весят прилично и могут занять заметное место на
    диске.

    Две недели — компромисс: разбор сбоя почти всегда происходит в тот же
    день или на следующий, так что двухнедельный запас с лихвой
    покрывает даже отпуск.

    :return: сколько файлов удалено
    """
    import glob
    import time as _time

    cutoff = _time.time() - keep_days * 24 * 60 * 60
    folder = os.path.dirname(os.path.abspath(__file__))
    removed = 0

    targets = []
    for pattern in DEBUG_FILE_PATTERNS:
        targets.extend(glob.glob(os.path.join(folder, pattern)))
    if os.path.isdir(DEBUG_SCREENSHOTS_DIR):
        targets.extend(glob.glob(os.path.join(DEBUG_SCREENSHOTS_DIR, "*")))

    for path in targets:
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            # Файл занят или уже удалён — не повод шуметь в логе при старте.
            pass

    if removed:
        logger.info("Уборка: удалено старых отладочных файлов — %d (старше %d дней).",
                    removed, keep_days)
    return removed
