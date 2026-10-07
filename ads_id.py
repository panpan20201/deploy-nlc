# -*- coding: utf-8 -*-
"""
ads_id.py
=========

Автоматический сброс рекламного идентификатора Google (GAID) на планшете.

ЗАЧЕМ
-----

HeyDealer ограничивает проверки: 10 номеров в сутки на устройство.
Очистка данных приложения и переустановка лимит НЕ снимают — проверено.
А сброс рекламного идентификатора Google — снимает, тоже проверено на
живом устройстве. Значит, лимит привязан именно к нему.

Без автоматики упор в лимит означает простой до тех пор, пока человек не
подойдёт к планшету и не сбросит идентификатор руками через настройки.
С автоматикой бот восстанавливается сам, за полминуты, и человек вообще
не замечает, что что-то было не так.

⛔ ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ И НИКОГДА НЕ ДОЛЖЕН ДЕЛАТЬ

Он НЕ трогает аккаунт в HeyDealer. Приложение предупреждает:
«탈퇴하신 계정은 3개월 동안 헤이딜러 앱 이용이 불가능합니다» — удалённый
аккаунт блокируется на ТРИ МЕСЯЦА. Это дверь в одну сторону: ошибка
здесь выводит устройство из работы на квартал, тогда как упор в дневной
лимит стоит максимум одних суток. Поэтому весь модуль работает
исключительно в настройках Google и в HeyDealer не заходит вовсе.

ПОЧЕМУ ЧЕРЕЗ ADB, А НЕ ЧЕРЕЗ APPIUM
------------------------------------

Сброс живёт в настройках Google — то есть в ЧУЖОМ приложении. Appium-
сессия под него конфликтовала бы с нашими: на одном устройстве живёт
только одна сессия, и её создание молча обрывает текущую.

`uiautomator dump` + `input tap` не требуют сессии вообще, работают
поверх любого экрана и попутно дают XML-дамп — то самое доказательство,
которое иначе пришлось бы добывать отдельным заходом. Здесь диагностика
получается побочным продуктом самой работы.

ПОЧЕМУ ПОИСК КНОПКИ ПО ЧАСТЯМ СЛОВ, А НЕ ПО ТОЧНОМУ ТЕКСТУ
-----------------------------------------------------------

Точный текст кнопки зависит от версии Google Play Services и языка
интерфейса: в разных версиях встречается и «сбросить рекламный ID», и
«удалить рекламный ID», по-корейски и по-английски. Привязка к точной
строке — известный класс хрупкости, характерный и для других шагов
(текст у логотипа PARTSNUMBER, hint у поля ввода Carmoodo).

Поэтому ищем узел, в тексте которого есть И слово «реклама», И слово
действия — на любом из двух языков. А если не нашли ничего, честно
сохраняем дамп экрана и сдаёмся, не пытаясь угадать координаты.
"""

import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

HEYDEALER_PACKAGE = "kr.perfectree.heydealer"

# Точка входа в настройки рекламы. Первый вариант — современный интент,
# второй — прямой запуск активности (в старых версиях Play Services
# интента может не быть). Пробуем по очереди.
ADS_SETTINGS_ENTRY_POINTS = (
    ["shell", "am", "start", "-a", "com.google.android.gms.settings.ADS_PRIVACY"],
    ["shell", "am", "start", "-n",
     "com.google.android.gms/com.google.android.gms.ads.settings.AdsSettingsActivity"],
)

# Слова для поиска кнопки сброса. Нужно совпадение по ОДНОМУ слову из
# каждой группы — так мы переживаем и смену формулировки, и смену языка.
ADS_WORDS = ("광고", "advertising", "ad id", "рекламн")
ACTION_WORDS = ("재설정", "삭제", "초기화", "reset", "delete", "сброс", "удал")

# Кнопки подтверждения в диалоге, который появляется после нажатия.
#
# Ищем по тексту, а тапаем по ближайшему кликабельному родителю, и НЕ
# требуем clickable="true" у самой надписи. В современных интерфейсах
# надпись почти всегда лежит ВНУТРИ кликабельного контейнера и сама
# некликабельна — это видно и в дампе HeyDealer, где кнопка
# "네, 알겠습니다" имеет clickable="false". С таким требованием диалог
# подтверждения не находился бы вовсе, и сброс молча не выполнялся бы.
CONFIRM_WORDS = (
    # ⚠️ РУССКИЕ СЛОВА ЗДЕСЬ НЕ ДЛЯ КРАСОТЫ. Интерфейс планшета —
    # РУССКИЙ, и кнопка подтверждения подписана "ОК" КИРИЛЛИЦЕЙ:
    # "О" — это U+041E, "К" — U+041A. Латинское "ok" (U+006F, U+006B)
    # выглядит точно так же, но это ДРУГИЕ СИМВОЛЫ, и сравнение строк
    # их не отождествляет. Без кириллического варианта бот находит
    # диалог, но не узнаёт в нём кнопку и застревает (подтверждено
    # дампом экрана планшета).
    "ок",                      # кириллица — основной вариант на этом планшете
    "да", "сброс", "продолж", "удал",
    "ok", "confirm", "reset", "delete", "yes", "continue",   # латиница
    "확인", "삭제", "재설정", "초기화", "네", "예", "계속",      # корейский
)

# Слова, по которым НЕЛЬЗЯ нажимать ни при каких условиях: это отказ от
# действия. Проверяются ПЕРВЫМИ.
#
# Здесь та же история с алфавитами: без русских слов на русском
# интерфейсе защиты от нажатия "Отмена" не было бы вовсе, кроме правила
# "берём самую правую кнопку" — а полагаться на расположение как на
# единственную защиту нельзя.
CANCEL_WORDS = (
    "отмена", "отменить", "нет", "закрыть", "позже", "не сейчас",
    "cancel", "no", "dismiss", "later",
    "취소", "아니", "닫기", "나중에",
)

CONFIRM_WAIT_SEC = 6.0  # сколько ждать появления диалога (он выезжает с
# анимацией, одного мгновенного слепка не хватает)
CONFIRM_POLL_SEC = 0.7

UI_DUMP_DEVICE_PATH = "/sdcard/nlc_ui_dump.xml"
ADB_TIMEOUT_SEC = 20
SCREEN_SETTLE_SEC = 1.5  # пауза, чтобы экран успел отрисоваться после
# перехода: uiautomator снимает мгновенный слепок, и на недорисованном
# экране мы бы просто не нашли кнопку


def _adb_path() -> str:
    sdk = os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT")
    if sdk:
        for name in ("adb.exe", "adb"):
            candidate = os.path.join(sdk, "platform-tools", name)
            if os.path.exists(candidate):
                return candidate
    return "adb"


def _adb(args: List[str], udid: Optional[str] = None) -> Tuple[int, str, str]:
    cmd = [_adb_path()]
    if udid:
        cmd += ["-s", udid]
    cmd += args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=ADB_TIMEOUT_SEC,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return -1, "", str(exc)


def _ui_dump(udid: Optional[str] = None) -> Optional[str]:
    """
    Снимает XML-слепок текущего экрана через uiautomator.

    Делается в два шага (сохранить на устройстве → вычитать), потому что
    вывод прямо в stdout поддерживается не на всех прошивках и иногда
    приходит обрезанным.
    """
    code, _, err = _adb(["shell", "uiautomator", "dump", UI_DUMP_DEVICE_PATH], udid)
    if code != 0:
        logger.warning("Не удалось снять слепок экрана: %s", err.strip())
        return None
    code, out, err = _adb(["shell", "cat", UI_DUMP_DEVICE_PATH], udid)
    if code != 0 or "<hierarchy" not in out:
        logger.warning("Слепок экрана не прочитался: %s", err.strip())
        return None
    return out


def _save_dump(xml: str, reason: str) -> Optional[str]:
    """Сохраняет слепок рядом со скриптом — чтобы было что разбирать."""
    filename = f"adsid_debug_{reason}_{time.strftime('%Y%m%d_%H%M%S')}.xml"
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        logger.info('Слепок экрана сохранён в "%s" — пришли этот файл для разбора.', filename)
        return filename
    except OSError as exc:
        logger.warning("Не удалось сохранить слепок: %s", exc)
        return None


def _center(bounds: str) -> Optional[Tuple[int, int]]:
    """Центр элемента из атрибута bounds вида "[12,34][56,78]"."""
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _node_text(node) -> str:
    return f"{node.get('text', '')} {node.get('content-desc', '')}".lower()


def _find_reset_button(xml: str) -> Optional[Tuple[int, int]]:
    """
    Ищет кнопку сброса рекламного ID: узел, в тексте которого есть и
    слово про рекламу, и слово про действие (см. пояснение в шапке).

    Если подходящий узел сам по себе некликабельный (часто текст лежит
    внутри кликабельного контейнера), поднимаемся к ближайшему
    кликабельному родителю — тапать по надписи внутри кнопки не всегда
    срабатывает.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    # Карта "ребёнок → родитель": в ElementTree нет ссылки на родителя,
    # а она нужна для подъёма к кликабельному контейнеру.
    parents = {child: parent for parent in root.iter() for child in parent}

    for node in root.iter():
        text = _node_text(node)
        if not any(w in text for w in ADS_WORDS):
            continue
        if not any(w in text for w in ACTION_WORDS):
            continue

        # Пишем в лог, ПО ЧЕМУ именно собираемся тапнуть. Иначе в логе
        # остаются только координаты, а по ним невозможно понять,
        # нажали мы кнопку сброса или случайно попали в заголовок
        # страницы — особенно когда тап приходится у самого верха экрана
        # (например, y=152).
        label = (node.get("text") or node.get("content-desc") or "").strip()
        logger.info("Найден подходящий элемент: %r", label)

        current = node
        for _ in range(4):  # не больше четырёх уровней вверх
            if current.get("clickable") == "true":
                return _center(current.get("bounds"))
            current = parents.get(current)
            if current is None:
                break
        # Кликабельного родителя нет — тапнем по самой надписи.
        return _center(node.get("bounds"))
    return None


def _clickable_target(node, parents, require_clickable: bool = False) -> Optional[Tuple[int, int]]:
    """
    Координаты для тапа: ближайший кликабельный родитель или сам узел.

    :param require_clickable: если True и ничего кликабельного не нашлось,
        вернуть None вместо координат самой надписи. Нужно для диалога:
        его ЗАГОЛОВОК ("Сбросить рекламный идентификатор?") содержит те же
        слова, что и кнопка, и без этой проверки мог бы попасть в
        кандидаты — то есть мы бы тапали по тексту вопроса вместо ответа.
    """
    current = node
    for _ in range(4):
        if current.get("clickable") == "true":
            return _center(current.get("bounds"))
        current = parents.get(current)
        if current is None:
            break
    return None if require_clickable else _center(node.get("bounds"))


def _find_confirm_button(xml: str) -> Optional[Tuple[int, int]]:
    """
    Кнопка подтверждения в диалоге.

    Три правила, каждое проверено на живом устройстве:

    1. НЕ требуем clickable у самой надписи — тапаем по ближайшему
       кликабельному родителю (см. пояснение у CONFIRM_WORDS).
    2. Сначала отсеиваем кнопки отказа. "취소" ("отмена") стоит рядом с
       "확인" ("ок"), и нажать не то — значит тихо ничего не сделать.
    3. Если подходящих кнопок несколько, берём САМУЮ ПРАВУЮ: в диалогах
       Material подтверждение всегда справа, а отказ слева. Это не
       догадка о конкретном приложении, а правило оформления системы.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    parents = {child: parent for parent in root.iter() for child in parent}
    candidates = []

    for node in root.iter():
        text = _node_text(node).strip()
        if not text:
            continue
        if any(word in text for word in CANCEL_WORDS):
            continue
        if not any(text == w or text.startswith(w) for w in CONFIRM_WORDS):
            continue
        point = _clickable_target(node, parents, require_clickable=True)
        if point:
            candidates.append((point, text))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0][0])  # по горизонтали
    point, label = candidates[-1]
    logger.info("Кнопка подтверждения: %r в точке %s", label, point)
    return point


def _list_visible_buttons(xml: str) -> str:
    """
    Перечисляет надписи на экране — для лога, когда кнопку найти не
    удалось. Без этого в логе остаётся только "не нашли", и следующий
    заход начинается с того же незнания.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return "(слепок не разобрался)"
    labels = []
    for node in root.iter():
        text = _node_text(node).strip()
        if text and len(text) < 40:
            labels.append(text)
    return ", ".join(repr(t) for t in labels[:15]) or "(надписей нет)"


def _tap(x: int, y: int, udid: Optional[str] = None) -> bool:
    code, _, err = _adb(["shell", "input", "tap", str(x), str(y)], udid)
    if code != 0:
        logger.warning("Тап не прошёл: %s", err.strip())
        return False
    return True


AD_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _extract_ad_id(xml: str) -> Optional[str]:
    """
    Вытаскивает сам рекламный идентификатор с экрана настроек.

    Экран настроек показывает строку
    "Ваш рекламный идентификатор: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx".

    Это превращает проверку успеха из догадки в факт. Косвенный признак
    вроде "диалог закрылся, наверное, сработало" может рапортовать об
    успехе, когда ничего не сделано. Поэтому сравниваем идентификатор
    ДО и ПОСЛЕ: изменился — значит, сброс действительно произошёл.
    Спорить тут не с чем.
    """
    if not xml:
        return None
    match = AD_ID_PATTERN.search(xml)
    return match.group(0).lower() if match else None


def _dialog_is_open(xml: str) -> bool:
    """
    Открыт ли сейчас диалог подтверждения.

    Признак — наличие кликабельной кнопки ОТКАЗА ("ОТМЕНА", "Cancel",
    "취소"). Именно отказа, а не подтверждения, и вот почему:

    на обычном экране настроек есть строка "Сбросить рекламный
    идентификатор" — она подходит под слова подтверждения ("сброс") и
    кликабельна. Если искать диалог по кнопке подтверждения, чистый
    экран настроек будет неотличим от открытого диалога: очистка приняла
    бы список настроек за зависший диалог, нажала "Назад" и вышла из
    настроек ещё до того, как что-то сделать.

    А вот кнопка отказа есть только в диалоге — в списке настроек ей
    взяться неоткуда. Признак получается однозначным.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return False
    parents = {child: parent for parent in root.iter() for child in parent}
    for node in root.iter():
        text = _node_text(node).strip()
        if not text:
            continue
        if any(word in text for word in CANCEL_WORDS):
            if _clickable_target(node, parents, require_clickable=True):
                return True
    return False


def _dismiss_stale_dialog(udid: Optional[str]) -> bool:
    """
    Закрывает диалог, оставшийся открытым с прошлого раза.

    Если предыдущий запуск нашёл диалог, но не смог нажать подтверждение
    и завершился, диалог остаётся висеть. Следующий запуск открывает
    настройки и упирается в тот же самый диалог: экран под ним
    заблокирован, кнопка сброса недоступна, всё встаёт намертво до
    ручного вмешательства. Без этой очистки один незакрытый диалог
    выводил бы из строя весь механизм восстановления.

    :return: True, если пришлось что-то закрывать
    """
    xml = _ui_dump(udid)
    if not xml or not _dialog_is_open(xml):
        return False
    logger.info("На экране висит диалог с прошлого раза — закрываю его кнопкой «Назад».")
    _adb(["shell", "input", "keyevent", "KEYCODE_BACK"], udid)
    time.sleep(SCREEN_SETTLE_SEC)
    return True


def reset_advertising_id(udid: Optional[str] = None) -> Tuple[bool, str]:
    """
    Сбрасывает рекламный идентификатор Google на планшете.

    Порядок действий и почему именно такой:

      1. Закрываем HeyDealer. Приложение читает рекламный ID при старте и
         держит в памяти, поэтому без перезапуска оно продолжило бы
         пользоваться СТАРЫМ идентификатором и лимит остался бы на месте.
      2. Открываем настройки рекламы (двумя способами, см. выше).
      3. Ищем и жмём кнопку сброса.
      4. Подтверждаем в диалоге, если он появился. Диалог есть не во всех
         версиях — его отсутствие не считаем ошибкой.
      5. Уходим на домашний экран, чтобы не оставить планшет в чужих
         настройках.

    :return: (получилось ли, короткое пояснение для лога и человека)
    """
    logger.info("Сброс рекламного ID: начинаю.")

    # --- 1. Закрыть HeyDealer -------------------------------------------
    _adb(["shell", "am", "force-stop", HEYDEALER_PACKAGE], udid)

    # --- 2. Открыть настройки рекламы ------------------------------------
    opened = False
    for entry_point in ADS_SETTINGS_ENTRY_POINTS:
        code, out, _ = _adb(entry_point, udid)
        if code == 0 and "Error" not in out:
            opened = True
            break
    if not opened:
        return False, ("не удалось открыть настройки рекламы Google — "
                       "проверь, что на планшете есть сервисы Google Play")

    time.sleep(SCREEN_SETTLE_SEC)

    # --- 2а. Убрать диалог, оставшийся с прошлого раза -------------------
    _dismiss_stale_dialog(udid)

    # --- 3. Запомнить текущий идентификатор и нажать кнопку сброса -------
    xml = _ui_dump(udid)
    if xml is None:
        return False, "не удалось снять слепок экрана настроек"

    ad_id_before = _extract_ad_id(xml)
    if ad_id_before:
        logger.info("Рекламный ID до сброса: %s", ad_id_before)
    else:
        logger.info("Идентификатор на экране не виден — проверю результат по "
                    "закрытию диалога (менее надёжно).")

    target = _find_reset_button(xml)
    if target is None:
        # Не угадываем координаты — сохраняем слепок и честно сдаёмся.
        # Один неверный тап в чужих настройках может изменить что угодно.
        _save_dump(xml, "reset_button_not_found")
        return False, ("кнопка сброса не найдена на экране настроек — "
                       "сохранён слепок экрана для разбора")

    logger.info("Кнопка сброса найдена, нажимаю (%d, %d).", *target)
    if not _tap(*target, udid=udid):
        return False, "не удалось нажать кнопку сброса"
    time.sleep(SCREEN_SETTLE_SEC)

    # --- 4. Дождаться диалога и подтвердить -------------------------------
    #
    # Ждём появления диалога несколько секунд (он выезжает с анимацией,
    # одного мгновенного слепка не хватает) и, главное, НЕ СЧИТАЕМ
    # ОТСУТСТВИЕ ПОДТВЕРЖДЕНИЯ УСПЕХОМ. Иначе получается худшая из
    # возможных ошибок — молчаливая: бот считает лимит снятым, засчитывает
    # сброс в дневную норму и уходит в паузу, а идентификатор остаётся
    # прежним.
    #
    # Лучше честно сказать "не смог", чем тихо соврать: во втором случае
    # человек узнает правду только когда снова упрётся в лимит.
    confirm = None
    deadline = time.time() + CONFIRM_WAIT_SEC
    xml_after = None
    while time.time() < deadline:
        xml_after = _ui_dump(udid)
        if xml_after:
            confirm = _find_confirm_button(xml_after)
            if confirm:
                break
        time.sleep(CONFIRM_POLL_SEC)

    if confirm is None:
        if xml_after:
            logger.error(
                "Диалог подтверждения не найден за %.0f сек. Надписи на экране: %s",
                CONFIRM_WAIT_SEC, _list_visible_buttons(xml_after),
            )
            _save_dump(xml_after, "confirm_button_not_found")
        _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], udid)
        return False, ("нажал кнопку сброса, но не нашёл, чем подтвердить — "
                       "идентификатор, скорее всего, НЕ сброшен "
                       "(сохранён слепок экрана для разбора)")

    logger.info("Подтверждаю в диалоге (%d, %d).", *confirm)
    if not _tap(*confirm, udid=udid):
        return False, "не удалось нажать подтверждение"
    time.sleep(SCREEN_SETTLE_SEC)

    # --- 5. Проверить результат по самому идентификатору -----------------
    #
    # Главная проверка: идентификатор ДОЛЖЕН ИЗМЕНИТЬСЯ. Всё остальное —
    # косвенные признаки, по которым легко отрапортовать об успешном
    # сбросе, которого не было. Экран обновляется не мгновенно,
    # поэтому пару секунд опрашиваем.
    if ad_id_before:
        deadline = time.time() + CONFIRM_WAIT_SEC
        while time.time() < deadline:
            xml_final = _ui_dump(udid)
            ad_id_after = _extract_ad_id(xml_final)
            if ad_id_after and ad_id_after != ad_id_before:
                _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], udid)
                logger.info("Рекламный ID после сброса: %s — изменился, сброс подтверждён.",
                            ad_id_after)
                return True, f"рекламный ID сброшен (новый: {ad_id_after[:8]}...)"
            time.sleep(CONFIRM_POLL_SEC)

        logger.error("Рекламный ID не изменился: как был %s, так и остался.", ad_id_before)
        _save_dump(_ui_dump(udid) or "", "id_not_changed")
        _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], udid)
        return False, ("подтверждение нажато, но идентификатор не изменился — "
                       "сброс НЕ состоялся")

    # --- 5б. Запасная проверка, если идентификатор на экране не показан ---
    # Слабее предыдущей: она лишь подтверждает, что диалог закрылся, а не
    # что действие выполнено. Используется, только когда сверить нечего.
    xml_final = _ui_dump(udid)
    if xml_final and _dialog_is_open(xml_final):
        _save_dump(xml_final, "dialog_still_open")
        _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], udid)
        return False, ("диалог подтверждения не закрылся после нажатия — "
                       "идентификатор не сброшен")

    _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], udid)
    logger.info("Сброс рекламного ID: выполнен (проверено по закрытию диалога).")
    return True, "рекламный ID сброшен"
