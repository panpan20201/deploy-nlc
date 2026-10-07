# -*- coding: utf-8 -*-
"""
Скрипт для автоматизации мобильного приложения Carmoodo (카모두,
kr.co.ggkucar.app) через Appium (Python client, UiAutomator2 driver).

Сценарий: ввод номера автомобиля в разделе "자동차 사양조회" ("проверка
детальных характеристик"), ожидание загрузки результата, извлечение VIN
(차대번호) из результата.

ВАЖНОЕ АРХИТЕКТУРНОЕ ОТЛИЧИЕ ОТ HeyDealer: Carmoodo — это WebView-
приложение (сайт внутри обёртки), а не нативный Jetpack Compose UI.
Из-за этого элементы в дереве — общие android.view.View/android.widget.
EditText без resource-id, различать их можно только по text, content-desc
или hint. Как и в HeyDealer, текст часто лежит в некликабельном элементе,
а клик нужно делать по кликабельному родителю.

Сценарные функции из heydealer_history.py модуль НАМЕРЕННО не импортирует —
переиспользуемые функции (log, build_driver, click_clickable_ancestor_of_text,
adb_input_text) скопированы сюда без изменений по логике, чтобы модули
оставались независимыми друг от друга. Общими остаются только настройки
ускорения Appium и таймаут команд (см. импорт ниже).

Требования к окружению:
    pip install Appium-Python-Client selenium

Перед запуском:
    - Appium-сервер должен быть запущен на 127.0.0.1:4723
    - Android-эмулятор/устройство должно быть запущено и видно через `adb devices`
    - Приложение kr.co.ggkucar.app должно быть установлено, пользователь
      уже залогинен (сессия сохраняется — noReset: true, как и в HeyDealer)
    - На устройстве должна быть установлена и включена как метод ввода
      клавиатура ADBKeyBoard (com.android.adbkeyboard/.AdbIME)
"""

import json
import logging  # см. пояснение у log() ниже
import os
import re  # сравнение текста диалога без пробелов
import subprocess
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Настройки ускорения Appium — общие для обоих приложений (оба на WebView,
# см. подробное объяснение в самой функции). Импортируем, а не дублируем,
# чтобы правка настроек в одном месте влияла на оба сценария сразу.
from heydealer_history import _apply_speed_settings, apply_appium_command_timeout

load_dotenv()

# АДРЕС APPIUM-СЕРВЕРА — см. подробное объяснение в heydealer_history.py
# (там же образец строки для .env). Здесь — та же логика, специально
# продублирована без импорта из heydealer_history.py (модули по задумке
# независимы друг от друга, см. пояснение в шапке файла), чтобы оба
# модуля стучались на ОДИН И ТОТ ЖЕ Appium-сервер (у планшета одна общая
# точка входа для обоих приложений — HeyDealer и Carmoodo).
APPIUM_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")

# УСТРОЙСТВО — см. подробное объяснение в heydealer_history.py (там же
# инструкция, как узнать udid планшета через `adb devices`). Здесь та же
# логика, продублирована по той же причине, что и APPIUM_SERVER_URL выше —
# модули независимы друг от друга, но оба должны стучаться на ОДНО И ТО ЖЕ
# устройство (планшет с установленными HeyDealer и Carmoodo).
ANDROID_DEVICE_NAME = os.getenv("ANDROID_DEVICE_NAME", "emulator-5554")
ANDROID_UDID = os.getenv("ANDROID_UDID")

# Учётные данные для входа в Carmoodo — нужны, чтобы бот мог сам
# залогиниться заново, если приложение выкинуло на экран авторизации
# (см. .env.example). Если не заданы — автоматический вход выполняться
# не будет, код только честно сообщит в логе, что нужен ручной вход.
CARMOODO_USERNAME = os.getenv("CARMOODO_USERNAME")
CARMOODO_PASSWORD = os.getenv("CARMOODO_PASSWORD")


# ---------------------------------------------------------------------------
# Константы: пакет приложения, тексты, таймауты
# ---------------------------------------------------------------------------

APP_PACKAGE = "kr.co.ggkucar.app"

MENU_ITEM_TEXT = "상세사양조회"  # пункт главного меню -> экран "자동차 사양조회"
# ⚠️ На главном экране есть ЕЩЁ ОДНО поле ввода номера — оно ведёт в
# другой раздел приложения, а не в "자동차 사양조회". Мы его не трогаем:
# попадаем на нужный экран через клик по пункту меню MENU_ITEM_TEXT, а
# перед поиском поля дополнительно убеждаемся, что мы действительно на
# нужном экране (см. SPEC_SCREEN_MARKER_XPATH ниже).

PLATE_INPUT_HINT = "차량번호"  # поиск поля по hint — только запасной
# вариант, основной признак — resource-id (см. ниже).
#
# В текущей версии приложения у поля ввода:
#     <android.widget.EditText resource-id="carNum" hint="" showing-hint="false" ...>
# то есть атрибут hint ПУСТОЙ. Подсказка "차량번호입력" отрисована ОТДЕЛЬНЫМ
# элементом-заголовком НАД полем (bounds [326,139][473,198]), а не внутри
# него — поэтому XPath по одному @hint не находит ничего.
#
# Привязка к hint (как и привязка к тексту у логотипа PARTSNUMBER) ставит
# критичный шаг в зависимость от косметической детали вёрстки, которую
# приложение может поменять в любом обновлении. Поэтому основной признак —
# resource-id "carNum": это внутренний идентификатор поля, он не зависит
# ни от языка интерфейса, ни от того, показывается ли подсказка.
PLATE_INPUT_RESOURCE_ID = "carNum"

# Порядок вариантов в объединённом XPath — это и есть порядок надёжности:
# сначала resource-id (стабильный), затем hint (на случай, если в
# какой-то версии приложения подсказка вернётся внутрь поля).
PLATE_INPUT_XPATH = (
    f'//android.widget.EditText[@resource-id="{PLATE_INPUT_RESOURCE_ID}"]'
    f' | //android.widget.EditText[@hint="{PLATE_INPUT_HINT}"]'
)

# Последний рубеж, если приложение сменит и resource-id: на экране
# "자동차 사양조회" по дампу ровно ОДИН EditText, так что "единственное поле
# ввода на экране" — однозначный признак. Используется только после того,
# как подтверждён маркер экрана (см. SPEC_SCREEN_MARKER_XPATH ниже), иначе
# был бы риск попасть на поле-обманку с главного экрана.
PLATE_INPUT_ANY_XPATH = "//android.widget.EditText"

# Заголовок экрана "자동차 사양조회" — им мы подтверждаем, что шаг 2 (тап по
# пункту меню) реально перевёл нас на нужный экран, ПРЕЖДЕ чем искать на
# нём поле ввода. Это разделяет две совершенно разные причины сбоя, которые
# без такой проверки выглядели бы в логе одинаково ("поле не найдено"):
#   - мы вообще не на том экране (не сработал тап по меню);
#   - экран правильный, но изменилась разметка поля.
# В дампе заголовок присутствует и как @text, и как @content-desc —
# проверяем оба, по той же причине, что и с маркером конца списка в
# HeyDealer (WebView часто кладёт надпись только в content-desc).
SPEC_SCREEN_MARKER_TEXT = "자동차 사양조회"
SPEC_SCREEN_MARKER_XPATH = (
    f'//*[@text="{SPEC_SCREEN_MARKER_TEXT}"]'
    f' | //*[@content-desc="{SPEC_SCREEN_MARKER_TEXT}"]'
)

SEARCH_BUTTON_DESC = "검색하기"  # кнопка поиска, content-desc, кликабельна напрямую
SEARCH_BUTTON_XPATH = f'//*[@content-desc="{SEARCH_BUTTON_DESC}"]'

VIN_LABEL_PREFIX = "차대번호"  # метка и значение слиты без разделителя в
# одну строку, например: "차대번호WBA5A7101FD817194" — как и у других полей
# этого приложения ("형식년도2015년식", "주행거리95,044km" и т.п.). VIN —
# это остаток строки после отсечения префикса (7 символов).
VIN_RESULT_XPATH = f'//*[starts-with(@text, "{VIN_LABEL_PREFIX}")]'

HOME_NAV_DESC = "Home"  # нижняя навигация, есть на всех экранах приложения —
# самый надёжный способ сбросить состояние перед следующим номером,
# надёжнее чем driver.back() (меньше зависит от того, на каком именно
# экране произошла ошибка)
HOME_NAV_XPATH = f'//*[@content-desc="{HOME_NAV_DESC}"]'

# Сколько ждать, пока после клика по "Home" экран определится: главный
# это или форма входа (см. разбор в reset_to_home_screen). Выход происходит
# по факту, обычно за доли секунды; число — потолок, а не пауза.
HOME_SETTLE_TIMEOUT = 10

DAILY_LIMIT_TEXT = "일 30건"  # предупреждение о дневном лимите запросов
# (30 в день), видно на экране ввода номера — если результат не пришёл,
# это одна из возможных причин, стоит проверить и залогировать понятно

# ---------------------------------------------------------------------------
# ЭКРАН ВЫБОРА ТРИМА ("세부모델 및 트림을 선택하세요") — встречается, например,
# на Hyundai Avante XD (номер 04거6548): если Carmoodo не может однозначно
# определить машину по одному номеру (у одной модели/года выпуска бывает
# несколько похожих комплектаций/тримов), вместо прямого результата (VIN)
# показывается СЕТКА вариантов — нужно выбрать один из них, прежде чем
# поиск продолжится. Без обработки этого экрана ожидание VIN или диалога
# ошибки (см. wait_for_vin_or_error_dialog) заканчивалось бы таймаутом.
#
# Текст заголовка экрана (по XML-дампу, один-в-один):
#   "정확한 차량정보 조회를 위하여 세부모델 및 트림을 선택하세요."
# Уникальный, устойчивый маркер этого экрана — слово "세부모델" (оно не
# встречается больше нигде в обычном сценарии get_vin).
TRIM_SCREEN_MARKER_TEXT = "세부모델"
TRIM_SCREEN_MARKER_XPATH = f'//*[contains(@text, "{TRIM_SCREEN_MARKER_TEXT}")]'

# Сами варианты трима — элементы сетки, у КАЖДОГО из них есть атрибут
# is-collection-item="true" (по тому же XML-дампу) — этим они
# отличаются от остального текста на этом же экране (заголовков, года
# выпуска и т.п., у которых этого атрибута нет). Сам текст (например,
# "1.5 DOHC SPORT 기본형") — это ПОЛНОЕ описание одного варианта трима.
TRIM_OPTION_XPATH = '//*[@is-collection-item="true"]'

# Кнопка подтверждения выбора — ВАЖНО: называется "조회" (не "검색하기",
# как кнопка поиска на предыдущем экране!). По тому же XML-дампу:
# пока ни один вариант не выбран, у кнопки enabled="false" (серая,
# неактивная) — активируется (enabled="true") только после того, как тап
# по одному из TRIM_OPTION_XPATH реально засчитался приложением.
TRIM_CONFIRM_BUTTON_XPATH = '//android.widget.Button[@text="조회"]'
TRIM_CONFIRM_BUTTON_ENABLE_TIMEOUT = 5  # сколько ждать, что кнопка станет
# активной после тапа по варианту — если за это время не активировалась,
# скорее всего тап по варианту не засчитался (см. _pick_matching_trim_option)

# ---------------------------------------------------------------------------
# СОПОСТАВЛЕНИЕ ТРИМА ENCAR <-> ВАРИАНТА В CARMOODO
# ---------------------------------------------------------------------------
# Текст трима с Encar (например, "아반떼 XD 5DR 1.5 DOHC 스포츠 기본형" —
# см. encar_parser.get_vehicle_number_and_grade) почти никогда не совпадает
# с текстом варианта в Carmoodo побуквенно — используются то английские, то
# корейские обозначения одного и того же (например, "5DR" на Encar и
# "5도어" в Carmoodo — оба означают "5-дверный"). Список составлен по
# Hyundai Avante XD (см. выше) и пополняется по мере появления других
# марок/моделей, по той же логике, что и KNOWN_FIELD_LABELS в
# partsnumber_date.py.
#
# ПОПОЛНЕНИЕ СПИСКА: если код выбрал неправильный (или вообще никакой)
# вариант трима — нужны XML-дамп экрана выбора трима (код сам сохраняет его
# через dump_screen_for_debug при неудаче) и текст трима с Encar для этой
# же машины; по ним добавляется недостающее соответствие.
TRIM_TEXT_SYNONYMS: dict[str, str] = {
    "5DR": "5도어",
    "SPORT": "스포츠",
}


# ---------------------------------------------------------------------------
# ЭКРАН АВТОРИЗАЦИИ ("로그인")
# ---------------------------------------------------------------------------
# Иногда приложение само разлогинивается — всплывает нативный диалог с
# кнопкой OK (код его штатно закрывает, см. dismiss_stray_dialog_if_present),
# но ПОСЛЕ закрытия приложение оказывается уже не на рабочем экране, а на
# экране входа. Если этого не распознать, все дальнейшие ожидания элементов
# уходят в таймаут, потому что искомых элементов на экране входа просто
# нет.
#
# Структура экрана (по XML-дампу):
#   EditText  text="휴대폰번호(숫자만) 입력"   — телефон (плейсхолдер в text)
#   EditText  hint="비밀번호를 입력하세요", password="true" — пароль
#   View      content-desc="로그인", clickable="true"       — кнопка входа
#   View      text="자동로그인"                             — "автовход"
#
# Признак экрана входа — кликабельный элемент с content-desc="로그인".
# Он есть ТОЛЬКО на этом экране и ни на одном рабочем, поэтому проверка
# по нему не даёт ложных срабатываний.
LOGIN_SCREEN_XPATH = '//*[@content-desc="로그인" and @clickable="true"]'

# Поля ввода на экране входа. Пароль отличаем по атрибуту password="true"
# (надёжнее, чем по hint: hint — это текст плейсхолдера, его в приложении
# могут поменять, а password="true" — свойство самого поля).
LOGIN_PASSWORD_FIELD_XPATH = '//android.widget.EditText[@password="true"]'
LOGIN_PHONE_FIELD_XPATH = '//android.widget.EditText[not(@password="true")]'

# Сколько ждать, что после нажатия "로그인" экран входа исчезнет (то есть
# вход реально прошёл). Если за это время экран не сменился — считаем, что
# вход не удался (неверные данные, требуется подтверждение по SMS и т.п.).
LOGIN_COMPLETE_TIMEOUT = 20


def is_login_screen(driver) -> bool:
    """
    Проверяет, находится ли приложение прямо сейчас на экране входа.
    Быстрая проверка одним запросом (без ожиданий) — предназначена для
    вызова в местах, где экран входа МОЖЕТ появиться, но обычно не должен.
    """
    try:
        return len(driver.find_elements(AppiumBy.XPATH, LOGIN_SCREEN_XPATH)) > 0
    except Exception:
        # Сессия могла оборваться — это не наша забота здесь, пусть
        # разбирается вызывающий код на своих явных ожиданиях.
        return False


def perform_login(driver) -> bool:
    """
    Выполняет вход в приложение: вводит телефон и пароль из .env
    (CARMOODO_USERNAME / CARMOODO_PASSWORD), нажимает "로그인" и ждёт, что
    экран входа исчезнет.

    Возвращает True при успешном входе, False — если войти не удалось
    (данные не заданы в .env, поля/кнопка не найдены, либо экран входа не
    исчез за LOGIN_COMPLETE_TIMEOUT — например, неверный пароль или
    требуется дополнительное подтверждение).

    НЕ бросает исключений: разлогин — штатная жизненная ситуация, а не
    авария, и вызывающий код должен решать сам, что делать дальше (см.
    get_vin — там при неудачном входе запрос просто завершается без VIN,
    как и при любом другом "не удалось получить").

    ВВОД — через ADB_INPUT_TEXT broadcast (adb_input_text), тот же способ,
    что и для номера машины, а НЕ через send_keys.
    ПОЧЕМУ ИМЕННО ТАК: send_keys на поле телефона падает с ошибкой
        "ACTION_SET_PROGRESS has failed on the element ... login_id"
    Причина — особенность UiAutomator2: если у элемента среди доступных
    действий есть ACTION_SET_PROGRESS (у этого поля он есть), а
    передаваемый текст состоит ТОЛЬКО ИЗ ЦИФР, драйвер трактует его как
    значение ползунка/прогресса и пытается выставить его вместо ввода
    текста. Номер телефона — сплошные цифры, поэтому попадает ровно в этот
    случай. Через broadcast такой проблемы нет: текст отправляется
    клавиатурой как обычный набор символов, независимо от того, какие
    действия объявляет сам элемент.
    """
    if not CARMOODO_USERNAME or not CARMOODO_PASSWORD:
        log("LOGIN", "Приложение разлогинено, но CARMOODO_USERNAME/CARMOODO_PASSWORD не заданы в .env — автоматический вход невозможен, нужен ручной вход на устройстве.")
        return False

    log("LOGIN", "Обнаружен экран входа — выполняю автоматический вход...")
    try:
        phone_field = WebDriverWait(driver, DEFAULT_TIMEOUT, poll_frequency=POLL_INTERVAL).until(
            EC.presence_of_element_located((AppiumBy.XPATH, LOGIN_PHONE_FIELD_XPATH))
        )
        phone_field.click()
        time.sleep(0.35)  # даём клавиатуре открыться, прежде чем слать
        # broadcast — та же страховка, что и при вводе номера машины
        adb_input_text(CARMOODO_USERNAME)
        time.sleep(0.2)

        password_field = driver.find_element(AppiumBy.XPATH, LOGIN_PASSWORD_FIELD_XPATH)
        password_field.click()
        time.sleep(0.35)
        adb_input_text(CARMOODO_PASSWORD)
        time.sleep(0.2)

        # Кнопка "로그인" может оказаться перекрыта открытой клавиатурой —
        # прячем её перед кликом. driver.hide_keyboard() на некоторых
        # прошивках бросает исключение, если клавиатура уже скрыта, поэтому
        # оборачиваем и игнорируем ошибку: если клавиатуры нет, нам же лучше.
        try:
            driver.hide_keyboard()
            time.sleep(0.3)
        except Exception:
            pass

        login_btn = driver.find_element(AppiumBy.XPATH, LOGIN_SCREEN_XPATH)
        login_btn.click()
    except Exception as exc:
        log("LOGIN", f"Не удалось заполнить форму входа: {exc}")
        dump_screen_for_debug(driver, "login_form_failed")
        return False

    # Успех определяем по ИСЧЕЗНОВЕНИЮ экрана входа, а не по появлению
    # какого-то конкретного элемента главного экрана: так проверка не
    # зависит от того, на какой именно экран приложение выбросит после
    # входа (это может отличаться в зависимости от состояния аккаунта).
    try:
        WebDriverWait(driver, LOGIN_COMPLETE_TIMEOUT, poll_frequency=POLL_INTERVAL).until(
            lambda d: len(d.find_elements(AppiumBy.XPATH, LOGIN_SCREEN_XPATH)) == 0
        )
    except TimeoutException:
        log("LOGIN", "Вход не выполнен: экран входа не исчез. Возможные причины — неверные логин/пароль в .env, либо приложение требует дополнительное подтверждение.")
        dump_screen_for_debug(driver, "login_did_not_complete")
        return False

    log("LOGIN", "Вход выполнен успешно.")
    time.sleep(0.5)  # даём приложению дорисовать экран после входа
    return True


def ensure_logged_in(driver) -> bool:
    """
    Если приложение оказалось на экране входа — выполняет вход.

    Возвращает:
      True  — приложение готово к работе (либо и так было залогинено,
              либо вход только что успешно выполнен);
      False — приложение на экране входа, и войти не удалось.
    """
    if not is_login_screen(driver):
        return True
    return perform_login(driver)


def _normalize_trim_text(text: str) -> set:
    """
    Приводит текст трима (что с Encar, что вариант из Carmoodo) к набору
    "нормализованных" слов для сравнения: верхний регистр, разбивка по
    пробелам, замена известных синонимов (TRIM_TEXT_SYNONYMS) на единый
    вид. Возвращает МНОЖЕСТВО слов (порядок слов в тексте роли не играет —
    ни на Encar, ни в Carmoodo он не гарантированно одинаковый).
    """
    words = text.upper().split()
    normalized = set()
    for word in words:
        normalized.add(TRIM_TEXT_SYNONYMS.get(word, word))
    return normalized

DIALOG_OK_BUTTON_ID = "android:id/button1"  # кнопка "OK" нативного
# AlertDialog — стандартный системный resource-id, НЕ часть WebView.
# По XML-дампам диалог всплывает поверх WebView в
# двух случаях — "조회된 결과가 없습니다." (результатов не найдено) и
# "차량번호를 형식에 맞게 입력해주세요." (неверный формат номера). Кнопка
# кликабельна напрямую.
DIALOG_OK_BUTTON_XPATH = f'//*[@resource-id="{DIALOG_OK_BUTTON_ID}"]'
DIALOG_MESSAGE_ID = "android:id/message"  # текст сообщения в том же диалоге
DIALOG_MESSAGE_XPATH = f'//*[@resource-id="{DIALOG_MESSAGE_ID}"]'

# ---------------------------------------------------------------------------
# НЕ ВСЯКИЙ ДИАЛОГ ОЗНАЧАЕТ "МАШИНЫ НЕТ В БАЗЕ"
#
# Пример из реального лога:
#
#     VIN не получен: приложение показало диалог с сообщением
#     "경기조합 전산과 연결에 오류가 있습니다."
#
# ("Ошибка соединения с вычислительным центром объединения Кёнги").
#
# Это СБОЙ НА СТОРОНЕ СЕРВИСА, а не ответ про машину. Если считать ответом
# по существу ЛЮБОЙ диалог, пользователь получит "машина не найдена в
# базе" — уверенное и неверное утверждение. Последствия шире, чем кажется:
# без VIN пропускается и PARTSNUMBER, и из трёх разделов ответа выпадают
# два, причём с формулировкой, которая не даёт повода перепроверить позже.
#
# Поэтому такие диалоги отделены: они помечаются как временный сбой
# сервиса, не кладутся в базу как ответ и не списывают запрос из дневной
# квоты (Carmoodo до поиска дело не довела).
#
# Признак "오류" ("ошибка") подтверждён реальным логом. Остальные добавлены
# по смыслу и на практике пока не встречались — если в логе появится
# диалог о сбое, который по ним не опознался, его признак добавляется
# сюда по тому же образцу.
#
# Сравнение идёт по тексту БЕЗ ПРОБЕЛОВ — приложения корейского рынка
# сплошь и рядом разделяют слова неразрывными пробелами (\xa0), и поиск
# по строке с обычным пробелом молча не находил бы ничего.
# Маркеры записываются без пробелов.
# ---------------------------------------------------------------------------
SERVICE_ERROR_DIALOG_MARKERS = (
    "오류",      # "ошибка" — подтверждено реальным логом
    "실패",      # "неудача" — по смыслу, в логе пока не встречалось
    "잠시후",    # "чуть позже" (обычно "повторите чуть позже") — то же
    "점검중",    # "идут технические работы" — то же
)


def looks_like_service_error_dialog(message_text: str) -> bool:
    """
    Диалог сообщает о сбое сервиса, а не об отсутствии машины в базе?

    :param message_text: текст из тела диалога (android:id/message)
    """
    if not message_text:
        return False
    squeezed = re.sub(r"\s+", "", message_text)
    return any(marker in squeezed for marker in SERVICE_ERROR_DIALOG_MARKERS)

DEFAULT_TIMEOUT = 20
APP_LAUNCH_TIMEOUT = 20  # сколько ждать главного меню на шаге 2.
#
# Число отвечает ровно за одно: сколько ждать запуска приложения. Случай
# «ждём того, чего на экране быть не может» (форма входа вместо меню)
# распознаётся отдельно, за секунду-две, и от величины таймаута не
# зависит вовсе (см. wait_for_menu_or_login).
#
# По логу шаг 2 при исправном экране укладывается в 2 секунды. Двадцать —
# это десятикратный запас на холодный старт. Если он окажется мал, это
# будет видно по строке шага 2 с полным таймаутом, и число легко поднять.
#
# Почему холодный старт вообще требует отдельного таймаута (то же
# наблюдалось в HeyDealer на только что включённом устройстве): при
# частых прогонах приложение обычно уже "прогрето" в памяти устройства и
# открывается быстро. Но при первом запуске с нуля (сплэш-экран, первая
# инициализация) может понадобиться больше времени. В Carmoodo, в отличие
# от HeyDealer, __enter__() не ждёт конкретный элемент — риск холодного
# старта приходится на первый тап по пункту меню внутри get_vin() (шаг 2),
# поэтому этот таймаут применяется именно там.
SEARCH_RESULT_TIMEOUT = 30
POLL_INTERVAL = 0.15


# ---------------------------------------------------------------------------
# Вспомогательные функции — скопированы из heydealer_history.py без
# изменений в логике (модули независимы, см. пояснение в шапке файла)
# ---------------------------------------------------------------------------

_logger = logging.getLogger("carmoodo_vin")


def log(step: str, msg: str) -> None:
    """
    Логгер модуля.

    Пишет через logging, а не через `print()`: так сообщения попадают в
    файл лога, получают время, уровень важности и метку конкретного
    запроса — без этого разбор сбоя постфактум практически невозможен.
    Кроме того, `print()` с корейским текстом может упасть с
    UnicodeEncodeError в Windows-консоли и уронить шаг на ровном месте.

    Формат сообщения — "[шаг] текст".
    Подробности — в nlc_logging.py.
    """
    _logger.info("[%s] %s", step, msg)


def build_driver() -> webdriver.Remote:
    """Создаёт сессию Appium с нужными desired capabilities."""
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = ANDROID_DEVICE_NAME
    if ANDROID_UDID:
        options.udid = ANDROID_UDID
    options.app_package = APP_PACKAGE
    options.no_reset = True
    # appActivity намеренно не указываем — подхватится дефолтная активность

    # Ускорение на реальном планшете — то же, что и
    # в heydealer_history.build_driver: отключает анимации переходов между
    # экранами на время сессии, Appium сам вернёт их обратно при закрытии.
    options.set_capability("appium:disableWindowAnimation", True)

    # appWaitActivity="*" защищает от ошибки на создании сессии
    # (webdriver.Remote(...), после ~34 секунд ожидания):
    #   Cannot start the 'kr.co.ggkucar.app' application.
    #   Original error: '.LogoActivity' or 'kr.co.ggkucar.app.LogoActivity'
    #   never started.
    #
    # ПОЧЕМУ ТАК ПРОИСХОДИТ: appActivity мы намеренно не задаём, поэтому
    # Appium сам определяет стартовую активность приложения (у Carmoodo это
    # LogoActivity — экран-заставка) и ЖДЁТ, что именно она станет текущей.
    # Но заставка живёт доли секунды и сразу сменяется главным экраном —
    # особенно при включённом disableWindowAnimation, когда переходы
    # быстрее. Appium физически не успевает её застать: к моменту проверки
    # на экране уже другая активность, "та самая" так и не появилась —
    # и он объявляет запуск неудачным, хотя приложение реально открылось.
    # Та же ошибка возникает, если приложение уже было в переднем плане:
    # новая LogoActivity тогда просто не создаётся.
    #
    # appWaitActivity="*" — штатное документированное решение ровно для
    # этого случая: "дождись ЛЮБОЙ активности этого приложения, не
    # конкретно заставки". Ослабление здесь безопасно, потому что реальную
    # готовность приложения мы всё равно проверяем сами и своими способами:
    # ниже идёт явная команда activate_app(), а затем reset_to_home_screen()
    # и ensure_logged_in() в CarmoodoSession.__enter__(), которые ждут
    # конкретные элементы через WebDriverWait. То есть Appium'у мы не
    # поручаем решать, "загрузилось ли приложение" — он только запускает.
    options.set_capability("appium:appWaitActivity", "*")
    # Запас по времени на холодный старт приложения (по умолчанию у Appium
    # 20 секунд). Здесь это уже не "ожидание конкретной заставки", а просто
    # верхняя граница на запуск процесса. Холодный старт на первом тапе
    # внутри get_vin() отдельно страхует APP_LAUNCH_TIMEOUT.
    options.set_capability("appium:appWaitDuration", 45000)

    driver = webdriver.Remote(APPIUM_SERVER_URL, options=options)

    # Страховка от бесконечного зависания на неотвечающем Appium/устройстве.
    # Функция общая с HeyDealer (там же подробное объяснение, почему без
    # неё повисший шаг навсегда блокирует планшет и молча останавливает бота).
    #
    # ⚠️ ТОЛЬКО ПОСЛЕ СОЗДАНИЯ ДРАЙВЕРА, И ЭТО НЕ КОСМЕТИКА.
    # RemoteConnection._client_config проставляется в __init__ соединения
    # (selenium 4.47, remote_connection.py:333). До этого момента
    # классовый set_timeout() падает с AttributeError, и страховка от
    # зависания молча не включается.
    #
    # Драйвер передаётся не для красоты: подтверждение читает таймаут
    # соединения ИМЕННО этого драйвера, а не классовый атрибут, куда мы
    # сами только что записали, — иначе проверка подтверждала бы саму себя.
    apply_appium_command_timeout(driver=driver)
    driver.implicitly_wait(0)  # используем только явные ожидания

    # Те же настройки ускорения, что и в HeyDealer —
    # Carmoodo тоже приложение на WebView, и та же проблема с ожиданием
    # "покоя интерфейса" перед каждой командой замедляет сценарий на
    # реальном планшете. Функция переиспользуется из heydealer_history,
    # чтобы настройки и объяснение к ним жили в одном месте (см. там
    # подробный комментарий, почему waitForIdleTimeout=0 безопасен).
    _apply_speed_settings(driver)

    # Та же страховка, что и в HeyDealer: если приложение не было открыто на
    # экране устройства в момент создания сессии — одних capabilities
    # (appPackage + noReset) Appium'у иногда недостаточно, чтобы самому
    # запустить нужное приложение, driver остаётся смотреть на прежний
    # экран (например, Home), и все дальнейшие ожидания элементов
    # приложения уходят в таймаут. activate_app() — явная команда Appium
    # "запусти/выведи на передний план это приложение", решает проблему
    # независимо от исходного состояния устройства.
    driver.activate_app(APP_PACKAGE)
    time.sleep(1.0)  # даём приложению реально открыться после запуска

    return driver


def click_clickable_ancestor_of_text(driver, text: str, timeout: int = DEFAULT_TIMEOUT):
    """
    Находит TextView с точным текстом и кликает на его ближайшего
    clickable-родителя (сам TextView часто не кликабелен).
    """
    xpath = f'//android.widget.TextView[@text="{text}"]/ancestor::*[@clickable="true"][1]'
    el = WebDriverWait(driver, timeout, poll_frequency=POLL_INTERVAL).until(
        EC.presence_of_element_located((AppiumBy.XPATH, xpath))
    )
    el.click()
    return el


def dump_screen_for_debug(driver, reason: str) -> None:
    """
    ДИАГНОСТИКА: сохраняет XML-дамп текущего экрана (driver.page_source) в
    файл рядом со скриптом. Нужно, чтобы разобраться, как РЕАЛЬНО устроены
    элементы на экране, а не гадать — Carmoodo это WebView-приложение, и
    структура элементов там может не совпадать с ожиданиями, скопированными
    из HeyDealer (нативное Compose-приложение). Ничего не ломает и не
    останавливает скрипт — просто пишет файл и продолжает выполнение
    (исключение, из-за которого сработала диагностика, после этого летит
    дальше как обычно).
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"carmoodo_debug_{reason}_{timestamp}.xml"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log("DEBUG", f'Дамп экрана сохранён в файл "{filename}" (папка со скриптом) — '
            f'пришли этот файл, чтобы разобраться в структуре экрана.')
    except Exception as dump_exc:
        log("DEBUG", f"Не удалось сохранить дамп экрана: {dump_exc}")


def dismiss_stray_dialog_if_present(driver, timeout: float = 2.5) -> bool:
    """
    СТРАХОВКА от зависающего AlertDialog:
    короткая, НЕ ждущая полный DEFAULT_TIMEOUT проверка — не висит ли на
    экране нативный диалог с кнопкой OK (DIALOG_OK_BUTTON_XPATH). Если
    висит — кликает OK и возвращает True, иначе сразу (без долгого
    ожидания) возвращает False.

    Нужна на случай, если диалог всплывёт в неожиданный момент — не только
    сразу после поиска (это уже обрабатывается в шаге 6), но и, например,
    если пользователь Carmoodo сам не закрыл его вручную в прошлый раз, или
    приложение показало его с какой-то задержкой. Без этой страховки
    зависший диалог перекрывает нижнюю навигацию — клик по "Home" в шаге 7
    промахивается мимо, и СЛЕДУЮЩИЙ вызов get_vin() для другого номера
    стартует уже в поломанном состоянии.

    timeout короткий (2-3 сек по умолчанию) намеренно: в нормальном случае
    диалога нет, и это всего лишь одна лишняя, но быстрая проверка перед
    каждым кликом по "Home" — не должна заметно замедлять сценарий.
    """
    try:
        ok_btn = WebDriverWait(driver, timeout, poll_frequency=POLL_INTERVAL).until(
            EC.presence_of_element_located((AppiumBy.XPATH, DIALOG_OK_BUTTON_XPATH))
        )
    except TimeoutException:
        return False

    log("DIALOG", 'Обнаружен зависший диалог с кнопкой OK — закрываю перед тем, как продолжить.')
    ok_btn.click()
    time.sleep(0.3)
    return True


def find_plate_input_field(driver, timeout: int = DEFAULT_TIMEOUT):
    """
    Находит поле ввода номера на экране "자동차 사양조회" — с тремя
    ступенями надёжности вместо одного жёсткого XPath по hint, который
    ломается при обновлении вёрстки приложения (подробное объяснение — у
    константы PLATE_INPUT_RESOURCE_ID).

    ПОРЯДОК ДЕЙСТВИЙ:

    0) Сначала убеждаемся, что мы ВООБЩЕ на нужном экране (заголовок
       "자동차 사양조회"). Это не формальность: без этой проверки два
       совершенно разных сбоя выглядят в логе одинаково — "не сработал тап
       по пункту меню, мы всё ещё на главном" и "экран правильный, но
       изменилась разметка поля". Лечатся они по-разному, поэтому и в логе
       должны выглядеть по-разному. Плюс это страховка от того, чтобы
       случайно ввести номер в поле-обманку на главном экране.

    1) Основной способ — resource-id="carNum" (плюс hint как запасной
       вариант в том же XPath).

    2) Если не нашли — берём единственный EditText на экране. Это уже
       безопасно, потому что шаг 0 подтвердил, что экран правильный. Если
       полей ноль или больше одного — НЕ угадываем (тот же принцип, что и
       при выборе трима: лучше честно сдаться, чем ввести номер не туда) и
       сохраняем дамп экрана для разбора.

    :return: найденный элемент поля ввода
    :raises TimeoutException: если экран не тот или поле найти не удалось
    """
    # --- Шаг 0: подтверждаем, что мы на экране "자동차 사양조회" ----------
    try:
        WebDriverWait(driver, timeout, poll_frequency=POLL_INTERVAL).until(
            EC.presence_of_element_located((AppiumBy.XPATH, SPEC_SCREEN_MARKER_XPATH))
        )
    except TimeoutException:
        log("3", f'Экран "{SPEC_SCREEN_MARKER_TEXT}" так и не открылся за {timeout} сек — '
            f'похоже, тап по пункту меню на шаге 2 не сработал.')
        dump_screen_for_debug(driver, "step3_spec_screen_not_opened")
        raise

    # --- Шаг 1: основной поиск (resource-id, затем hint) -----------------
    try:
        field = WebDriverWait(driver, timeout, poll_frequency=POLL_INTERVAL).until(
            EC.presence_of_element_located((AppiumBy.XPATH, PLATE_INPUT_XPATH))
        )
        log("3", f'Поле ввода найдено (resource-id="{PLATE_INPUT_RESOURCE_ID}" или hint="{PLATE_INPUT_HINT}").')
        return field
    except TimeoutException:
        log("3", f'Поле ввода не найдено ни по resource-id="{PLATE_INPUT_RESOURCE_ID}", '
            f'ни по hint="{PLATE_INPUT_HINT}" — пробую запасной способ '
            f'("единственный EditText на экране").')

    # --- Шаг 2: запасной способ — единственное поле ввода на экране ------
    # Долгого ожидания здесь уже не нужно: экран подтверждён на шаге 0 и
    # полноценное ожидание отработало на шаге 1 — если бы поле должно было
    # появиться со временем, оно бы уже появилось.
    fields = driver.find_elements(AppiumBy.XPATH, PLATE_INPUT_ANY_XPATH)
    if len(fields) == 1:
        log("3", "Поле ввода найдено запасным способом: на экране ровно один EditText. "
            "ВНИМАНИЕ: значит, разметка поля снова изменилась — стоит обновить "
            "PLATE_INPUT_RESOURCE_ID по свежему дампу экрана.")
        dump_screen_for_debug(driver, "step3_input_found_by_fallback")
        return fields[0]

    log("3", f'Запасной способ тоже не сработал: на экране {len(fields)} полей ввода '
        f'(ожидалось ровно одно) — не угадываю, в какое вводить номер.')
    dump_screen_for_debug(driver, "step3_input_field_not_found")
    raise TimeoutException(
        f'Поле ввода номера на экране "{SPEC_SCREEN_MARKER_TEXT}" не найдено: '
        f'ни по resource-id="{PLATE_INPUT_RESOURCE_ID}", ни по hint="{PLATE_INPUT_HINT}", '
        f'а EditText на экране {len(fields)} штук (ожидалось ровно одно).'
    )


def wait_for_vin_or_error_dialog(driver, timeout: int):
    """
    Ждёт ОДНОГО из ТРЁХ исходов поиска (гонка, по аналогии с
    wait_for_element_with_scroll из HeyDealer — но там гонка была между
    "элемент появился" и "нужно ещё проскроллить", а здесь между разными
    РЕЗУЛЬТАТАМИ поиска):

      1) элемент с текстом, начинающимся с "차대번호" — успех, машина
         найдена, дальше извлекаем VIN как обычно;
      2) нативный AlertDialog с кнопкой OK (DIALOG_OK_BUTTON_XPATH) —
         значит, вылезла ошибка ("결과 없음" / неверный формат номера);
      3) экран выбора трима (TRIM_SCREEN_MARKER_XPATH)
         — Carmoodo не смогла однозначно определить машину по одному
         номеру и просит выбрать конкретную комплектацию/трим (см.
         подробное объяснение у констант TRIM_SCREEN_MARKER_TEXT выше).

    Возвращает кортеж (outcome, element):
      - ("vin", vin_element)      — найден результат с VIN
      - ("dialog", ok_button_el)  — найден диалог ошибки, ok_button_el —
        сам элемент кнопки OK (чтобы вызывающий код мог сразу кликнуть по
        нему, не ища заново)
      - ("trim_selection", None)  — появился экран выбора трима (элемент
        не нужен — дальше вызывающий код сам ищет варианты через
        TRIM_OPTION_XPATH, см. _pick_matching_trim_option)
      - ("timeout", None)         — ничего из вышеперечисленного не
        появилось за timeout

    ВАЖНО: и диалог, и экран выбора трима — это состояния, которые не могут
    "случайно" совпасть с VIN_RESULT_XPATH или друг с другом (разные,
    непересекающиеся тексты/resource-id) — гонка между ними безопасна,
    ложных срабатываний быть не должно.
    """
    end_time = time.time() + timeout
    while True:
        vin_elems = driver.find_elements(AppiumBy.XPATH, VIN_RESULT_XPATH)
        if vin_elems:
            return "vin", vin_elems[0]

        dialog_elems = driver.find_elements(AppiumBy.XPATH, DIALOG_OK_BUTTON_XPATH)
        if dialog_elems:
            return "dialog", dialog_elems[0]

        trim_screen_elems = driver.find_elements(AppiumBy.XPATH, TRIM_SCREEN_MARKER_XPATH)
        if trim_screen_elems:
            return "trim_selection", None

        if time.time() >= end_time:
            return "timeout", None

        time.sleep(POLL_INTERVAL)


def _pick_matching_trim_option(driver, grade_hint: str) -> bool:
    """
    Обрабатывает экран выбора трима — находит среди
    вариантов сетки (TRIM_OPTION_XPATH) тот, что лучше всего соответствует
    тексту трима с Encar (grade_hint, см. encar_parser.get_vehicle_number_
    and_grade), тапает по нему (координатным тапом — та же причина, что и
    в tap_by_text: элементы WebView без ARIA-разметки не кликабельны через
    обычный accessibility-клик), дожидается, что кнопка "조회" стала
    активной, и жмёт её.

    НАМЕРЕННО НЕ УГАДЫВАЕТ, если совпадение неуверенное: лучше вернуть
    False и оставить машину без VIN (как и при "не найдено"), чем молча
    выбрать НЕПРАВИЛЬНЫЙ трим и получить VIN от ДРУГОЙ машины — это было
    бы гораздо хуже отсутствия результата, потому что выглядело бы как
    успех. Порог совпадения: как минимум ПОЛОВИНА слов из текста трима
    Encar должна найтись в тексте варианта (после нормализации через
    _normalize_trim_text) — см. код ниже.

    :param grade_hint: текст трима с Encar, например
        "아반떼 XD 5DR 1.5 DOHC 스포츠 기본형"
    :return: True, если вариант выбран и кнопка "조회" нажата; False, если
        подходящий вариант не нашёлся (grade_hint пуст, или ни один
        вариант не набрал достаточного совпадения) — в этом случае
        вызывающий код должен сохранить дамп экрана и сдаться, а не гадать.
    """
    if not grade_hint or not grade_hint.strip():
        log("TRIM", "Нет данных о триме с Encar (grade_hint пуст) — сопоставлять не с чем.")
        return False

    option_elems = driver.find_elements(AppiumBy.XPATH, TRIM_OPTION_XPATH)
    if not option_elems:
        log("TRIM", "Экран выбора трима определён, но ни одного варианта не найдено (TRIM_OPTION_XPATH пуст).")
        return False

    hint_words = _normalize_trim_text(grade_hint)

    best_elem = None
    best_score = 0.0
    best_text = ""
    scored_options = []  # для подробного лога при неудаче

    for elem in option_elems:
        option_text = (elem.text or "").strip()
        if not option_text:
            continue
        option_words = _normalize_trim_text(option_text)
        if not option_words:
            continue

        # Доля СЛОВ ВАРИАНТА, которые нашлись среди слов подсказки Encar.
        # Считаем именно так (а не наоборот и не через объединение), т.к.
        # варианты в Carmoodo обычно КОРОЧЕ полного текста Encar (например,
        # "1.5 DOHC SPORT 기본형" короче, чем "아반떼 XD 5DR 1.5 DOHC 스포츠
        # 기본형") — нас интересует, насколько ПОЛНОСТЬЮ короткий вариант
        # "покрывается" длинной подсказкой, а не наоборот.
        matched = option_words & hint_words
        score = len(matched) / len(option_words)
        scored_options.append((score, option_text))

        if score > best_score:
            best_score = score
            best_elem = elem
            best_text = option_text

    # Порог 0.5 — намеренно НЕ строже (не 0.8-1.0): у вариантов часто есть
    # слова уточнения ("SPORT", "DOHC"), которые могут не входить в короткую
    # подсказку Encar дословно, но совпадение хотя бы половины слов на
    # практике (проверено на Avante XD) уже надёжно отличает
    # правильный вариант от заведомо неподходящих (у которых пересечение
    # почти нулевое). Порог не строже 0.5 — сознательный компромисс между
    # "не выбрать чужой трим" и "не отказаться от совпадения из-за одного
    # лишнего слова"; если на практике окажется, что порог промахивается
    # (выбирает не тот вариант, либо слишком часто отказывается) — сюда и
    # смотреть в первую очередь.
    MATCH_THRESHOLD = 0.5
    scored_options.sort(reverse=True)
    log("TRIM", f"Подсказка с Encar: {grade_hint!r}. Варианты по убыванию совпадения: {scored_options}")

    if best_elem is None or best_score < MATCH_THRESHOLD:
        log("TRIM", f"Не нашлось уверенного совпадения (порог {MATCH_THRESHOLD}, лучший результат {best_score:.2f}) — не выбираю наугад.")
        return False

    log("TRIM", f"Выбираю вариант {best_text!r} (совпадение {best_score:.2f}) для подсказки {grade_hint!r}.")

    rect = best_elem.rect
    cx = rect["x"] + rect["width"] // 2
    cy = rect["y"] + rect["height"] // 2
    finger = PointerInput(interaction.POINTER_TOUCH, "finger1")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(cx, cy)
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.05)
    actions.pointer_action.pointer_up()
    actions.perform()

    # Ждём, что кнопка "조회" стала активной (enabled="true") — это
    # подтверждение, что тап реально засчитался приложением (а не просто
    # попал мимо элемента из-за неточных координат/перерисовки).
    try:
        WebDriverWait(
            driver, TRIM_CONFIRM_BUTTON_ENABLE_TIMEOUT, poll_frequency=POLL_INTERVAL
        ).until(
            lambda d: d.find_element(AppiumBy.XPATH, TRIM_CONFIRM_BUTTON_XPATH).get_attribute("enabled") == "true"
        )
    except TimeoutException:
        log("TRIM", f'Тап по варианту {best_text!r} не активировал кнопку "조회" — похоже, тап не засчитался.')
        return False

    confirm_btn = driver.find_element(AppiumBy.XPATH, TRIM_CONFIRM_BUTTON_XPATH)
    confirm_btn.click()
    time.sleep(0.3)
    return True


def tap_by_text(driver, text: str, timeout: int = DEFAULT_TIMEOUT):
    """
    Функция специфична для Carmoodo (в HeyDealer аналога нет) — нужна из-за
    отличия структуры экранов Carmoodo, видного по XML-дампу.

    Находит элемент с точным текстом text (ЛЮБОГО класса, не только
    TextView) и вместо accessibility-клика (el.click()) выполняет ФИЗИЧЕСКИЙ
    тап по координатам его центра.

    Почему это понадобилось: click_clickable_ancestor_of_text() ищет
    ближайшего clickable="true" родителя у текста — это работает для
    HeyDealer (нативный UI) и для части экранов Carmoodo (например, для
    логотипа сверху и для нижней навигации — там под текстом ДЕЙСТВИТЕЛЬНО
    есть обёртка clickable="true" с content-desc). НО для пунктов сетки
    меню на главном экране (например, "상세사양조회") XML-дамп
    показывает: сам элемент — лист дерева без единого clickable="true" предка
    вообще. Это типично для WebView: движок доступности Chrome помечает
    clickable="true" только те элементы, у которых есть подходящая
    ARIA-разметка (role="button" и т.п.) — у этих пунктов её, судя по
    всему, нет, хотя реальный клик по ним на странице работает через
    обычный JS-обработчик касания. Поэтому вместо accessibility-клика
    делаем обычный тап по экрану в нужных координатах — WebView увидит
    его как настоящее касание пользователя, независимо от того, что
    показывает дерево доступности.
    """
    xpath = f'//*[@text="{text}"]'
    el = WebDriverWait(driver, timeout, poll_frequency=POLL_INTERVAL).until(
        EC.presence_of_element_located((AppiumBy.XPATH, xpath))
    )
    rect = el.rect
    cx = rect["x"] + rect["width"] // 2
    cy = rect["y"] + rect["height"] // 2

    finger = PointerInput(interaction.POINTER_TOUCH, "finger1")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(cx, cy)
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.05)
    actions.pointer_action.pointer_up()
    actions.perform()
    return el


def screen_is_only_spinner(driver) -> bool:
    """
    Показывает ли экран ТОЛЬКО крутилку загрузки и больше ничего.

    Так выглядит зависшее на загрузке приложение: в дампе экрана ровно
    один значащий элемент —

        <android.widget.ProgressBar bounds="[368,622][432,686]" />

    Крутилка в центре экрана, ни одной надписи, ни одного поля. Ждать там
    нечего ни 20 секунд, ни сколько угодно, и повторная попытка в новой
    сессии упирается в то же самое. А поскольку Carmoodo идёт ПЕРВЫМ в
    цепочке (без VIN нет PARTSNUMBER), без распознавания этого состояния
    останавливалась бы вся проверка целиком.

    Признак ПОЛОЖИТЕЛЬНЫЙ и очень узкий: не «нужного элемента нет», а
    «есть крутилка И нет ни одной надписи». Обычный экран загрузки, за
    которым что-то появится, тоже сюда попадёт — и это нормально:
    вызывающий код сначала честно ждёт, и только исчерпав ожидание,
    спрашивает «а не крутилка ли там».
    """
    try:
        page = driver.page_source
    except Exception:
        return False

    if "ProgressBar" not in page:
        return False

    # Любая непустая надпись означает, что на экране есть содержимое, а
    # не только крутилка.
    for match in re.finditer(r'(?:text|content-desc)="([^"]*)"', page):
        if match.group(1).strip():
            return False
    return True


def screen_is_blind_login_form(driver) -> bool:
    """
    Форма входа нарисована, но Appium не видит в ней ни полей, ни кнопки.

    Встречается после разлогина: после закрытия диалога с OK на экране
    обычный вход — телефон, пароль, «로그인» (видно на скриншоте планшета), —
    но в дереве элементов от формы остаётся пустой контейнер
    resource-id="login-form" и кнопки «인증하기» / «비밀번호 찾기» под ним.
    is_login_screen ищет «로그인» и честно отвечает «входа нет», попытки
    уходят в таймаут поиска меню, а без VIN выпадают и детали.

    Состояние устойчивое — то же дерево и через минуту, и в новой
    Appium-сессии (noReset=true приложение не трогает). Проверено вручную:
    после terminate/activate через ~10 с в дереве снова 2 поля и
    «로그인», и обычный автовход срабатывает. Поэтому лечение то же, что у
    крутилки, — перезапуск приложения.
    """
    try:
        page = driver.page_source
    except Exception:
        return False
    return 'resource-id="login-form"' in page and "로그인" not in page and "EditText" not in page


def restart_app(driver) -> bool:
    """
    Закрывает и заново открывает приложение Carmoodo.

    Нужна, чтобы зависание Carmoodo не останавливало весь запрос вместе с
    PARTSNUMBER. Зависшее приложение само не расклинится: пересоздание
    Appium-сессии его не трогает (noReset=true сохраняет состояние),
    поэтому повторная попытка утыкается ровно в ту же крутилку. Нужна
    команда именно приложению.

    terminate_app снимает процесс, activate_app поднимает заново — уже с
    чистого экрана.
    """
    log("RESTART", "Перезапускаю приложение Carmoodo (было зависшее "
        "состояние, само оно не расклинится).")
    try:
        driver.terminate_app(APP_PACKAGE)
        time.sleep(1.0)
        driver.activate_app(APP_PACKAGE)
        time.sleep(2.0)
        return True
    except Exception as exc:
        log("RESTART", f"Не удалось перезапустить приложение: {exc}")
        return False


def wait_for_menu_or_login(driver, menu_text: str, timeout: float) -> Optional[str]:
    """
    Ждёт, что появится РАНЬШЕ: пункт главного меню или форма входа.

    Возвращает "menu", "login" или None (не дождались ни того, ни другого).

    ЗАЧЕМ. Типичный сценарий: приложение открылось на странице,
    ОСТАВШЕЙСЯ С ПРОШЛОЙ СЕССИИ (noReset=true состояние не сбрасывает), а
    клик по значку "Home" увёл его на форму входа — сессия внутри
    приложения к тому моменту истекла. Если ждать только пункт меню, шаг 2
    тратит весь таймаут впустую, и запрос в целом занимает около 100
    секунд там, где хватило бы двадцати.

    Причина — гонка. Проверка входа вызывается при создании сессии и в
    начале get_vin, но обе спрашивают состояние ОДИН РАЗ и мгновенно
    (is_login_screen — обычный find_elements без ожидания). Если форма
    входа в этот момент ещё не отрисовалась, проверка честно отвечает
    "экрана входа нет" — ответ верный на момент вопроса и неверный через
    долю секунды.

    ЗАЩИТА В ДВУХ МЕСТАХ, И ОБЕ НУЖНЫ. В корне — reset_to_home_screen
    проверяет, КУДА попал клик по "Home", и логинится на месте (там же
    подробный разбор). Здесь — страховка на случай, если форма входа
    всплывёт в любой другой момент: вместо однократного вопроса заранее
    ждём оба исхода сразу и реагируем на тот, который наступит.

    ЧЕМ ЭТО ЛУЧШЕ, ЧЕМ ПРОСТО УМЕНЬШИТЬ ТАЙМАУТ. Уменьшение таймаута
    сокращает потерю, но не устраняет её: мы всё равно ждём до упора
    того, чего не будет. Здесь же выход происходит через секунду-две
    после появления формы входа, независимо от величины таймаута. А сам
    таймаут остаётся большим ровно для того, ради чего он и ставился, —
    для честного холодного старта приложения.
    """
    menu_xpath = f'//*[@text="{menu_text}"]'
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.find_elements(AppiumBy.XPATH, menu_xpath):
            return "menu"
        if is_login_screen(driver):
            return "login"
        time.sleep(POLL_INTERVAL)
    return None


def reset_to_home_screen(driver, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """
    Возвращает приложение на стартовый экран кликом по нижней навигации
    "Home" — общая логика, вынесенная из шага 7 get_vin(), чтобы её же
    можно было использовать и при СОЗДАНИИ сессии (см. CarmoodoSession.__enter__).

    ЗАЧЕМ: приложение Carmoodo не сбрасывает своё состояние между сессиями (у нас noReset=true — так и
    задумано, иначе пришлось бы каждый раз заново логиниться). Из-за этого,
    если сессия Carmoodo была прервана НЕ штатно — например, при ручном
    тестировании через Appium Inspector, когда экран остался открытым на
    какой-то промежуточной странице (например, на экране выбора трима
    "세부모델 및 트림을 선택하세요") — следующий обычный запуск через
    бота открывает приложение СРАЗУ на этом же старом экране, а не на
    главном. Код шага 2 (клик по пункту меню "상세사양조회") в этот момент
    ищет элемент, которого просто нет на текущем экране, и падает с
    NoSuchElementError.

    Клик по "Home" в КОНЦЕ get_vin() (шаг 7, после каждого номера)
    защищает от порчи состояния МЕЖДУ вызовами get_vin() внутри одной и
    той же сессии, но не от ситуации, когда сессия вообще НАЧИНАЕТСЯ в
    непонятном состоянии (например, после стороннего вмешательства —
    ручного теста, случайно оставленного приложения и т.п.). Поэтому эта
    же функция вызывается ЕЩЁ и в CarmoodoSession.__enter__, сразу после
    запуска приложения — это
    гарантирует, что ПЕРВЫЙ вызов get_vin() в сессии тоже стартует с
    известного, чистого экрана, независимо от того, в каком состоянии
    приложение было оставлено до этого.

    Возвращает True, если клик по "Home" удался, False — если элемент
    "Home" не нашёлся за timeout (например, действительно первый холодный
    запуск, когда нижней навигации ещё нет на экране, — это не считается
    ошибкой, вызывающий код просто идёт дальше своим чередом).
    """
    try:
        # Страховка (как и в шаге 7): если завис нативный AlertDialog —
        # он перекрывает нижнюю навигацию, и клик по "Home" промахнётся.
        dismiss_stray_dialog_if_present(driver)

        home_btn = WebDriverWait(
            driver, timeout, poll_frequency=POLL_INTERVAL
        ).until(EC.presence_of_element_located((AppiumBy.XPATH, HOME_NAV_XPATH)))
        home_btn.click()

        # ⚠️ КЛИК ПРОШЁЛ — ЭТО ЕЩЁ НЕ ЗНАЧИТ, ЧТО МЫ НА ГЛАВНОМ ЭКРАНЕ.
        #
        # Типичный случай: приложение открылось на странице, оставшейся с
        # прошлой сессии, а клик по значку "Home" увёл его НА ФОРМУ ВХОДА —
        # сессия внутри приложения к тому моменту истекла, и Home это
        # вскрыл. Если засчитать успех по факту клика, следующая проверка
        # входа может спросить состояние на долю секунды раньше, чем форма
        # отрисуется, тоже ничего не увидеть, и шаг 2 уйдёт ждать пункт
        # меню на весь таймаут.
        #
        # Поэтому успех засчитывается не по отправленному действию, а по
        # наступившему результату: смотрим, куда мы в действительности
        # попали.
        outcome = wait_for_menu_or_login(
            driver, MENU_ITEM_TEXT, HOME_SETTLE_TIMEOUT
        )

        if outcome == "login":
            log("RESET", 'Клик по "Home" привёл на форму входа — значит, '
                'сессия внутри приложения истекла. Вхожу сразу здесь, чтобы '
                'следующий шаг не искал пункт меню на экране, где его нет.')
            return ensure_logged_in(driver)

        if outcome is None:
            # Ни главного экрана, ни формы входа. Ничего не выдумываем:
            # такой случай на практике не встречался. Идём дальше, но
            # фиксируем его в логе, чтобы он не прошёл незамеченным.
            log("RESET", f'После клика по "Home" за {HOME_SETTLE_TIMEOUT} сек '
                f'не появилось ни главного экрана, ни формы входа. Иду '
                f'дальше — следующий шаг разберётся сам.')
            return True

        return True
    except Exception as exc:
        log("RESET", f'Не удалось вернуться на главный экран через "Home": {exc}')
        return False


def adb_input_text(text: str) -> None:
    """
    Ввод текста (в т.ч. корейского) через кастомную клавиатуру ADBKeyBoard,
    т.к. обычный send_keys/adb shell input text не поддерживает не-латиницу.
    send_keys() может не сработать с корейским текстом в WebView-поле, а
    ADB-бродкаст уже проверен и работает — поэтому используем именно его,
    а не что-то новое.
    """
    cmd = [
        "adb", "shell", "am", "broadcast",
        "-a", "ADB_INPUT_TEXT",
        "--es", "msg", text,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"adb broadcast ADB_INPUT_TEXT завершился с ошибкой: {result.stderr}")


# ---------------------------------------------------------------------------
# Сессия Appium с переиспользованием между вызовами
# ---------------------------------------------------------------------------
#
# Структура зеркально повторяет HeyDealerSession из heydealer_history.py:
#   - CarmoodoSession.__enter__ — запускает Appium-сессию и сразу
#     оказывается на главном экране приложения (никаких доп. шагов не
#     нужно — в отличие от HeyDealer, где шаг 2 сразу кликал по вкладке
#     истории)
#   - CarmoodoSession.get_vin(plate_number) — весь сценарий получения VIN
#     по одному номеру, использует уже готовый self.driver
#   - CarmoodoSession.__exit__ — driver.quit(), один раз в конце
#
# Использование:
#   with CarmoodoSession() as session:
#       vin1 = session.get_vin("54노7511")
#       vin2 = session.get_vin("221누9948")
class CarmoodoSession:
    """Appium-сессия Carmoodo, переиспользуемая между несколькими
    запросами get_vin() без пересоздания приложения."""

    def __init__(self):
        self.driver = None
        # Причина последнего неудачного
        # get_vin() в виде короткого кода. Нужна потому, что get_vin()
        # возвращает None сразу в НЕСКОЛЬКИХ совершенно разных по смыслу
        # случаях, и для пользователя это очень разные новости:
        #   "not_found"     — машины нет в базе Carmoodo (перепроверять
        #                     бесполезно, это окончательный ответ);
        #   "daily_limit"   — исчерпан лимит 30 запросов в сутки (данные
        #                     есть, но сегодня их уже не получить —
        #                     совершенно другой совет пользователю);
        #   "trim_unmatched"— не удалось сопоставить комплектацию;
        #   "login_failed"  — приложение разлогинено и войти не вышло
        #                     (это проблема на нашей стороне, а не
        #                     отсутствие машины).
        # Помимо них: "no_response", "service_error", "unexpected_result"
        # (см. места присвоения в get_vin()).
        # Без этого кода все случаи выглядели бы снаружи одинаково —
        # просто None, — и пользователь не понимал бы, повторять ему
        # запрос или это бесполезно.
        self.last_reason: Optional[str] = None

    def __enter__(self) -> "CarmoodoSession":
        # -------------------------------------------------------------
        # Шаг 1: запуск приложения / создание сессии
        # -------------------------------------------------------------
        log("1", "Запуск сессии Appium и приложения Carmoodo...")
        self.driver = build_driver()
        time.sleep(0.5)  # короткая страховочная пауза перед первым
        # обращением к дереву элементов — по аналогии с HeyDealer, где
        # дальнейшие шаги всё равно ждут нужный элемент через собственный
        # WebDriverWait/click_clickable_ancestor_of_text, так что длинный
        # фиксированный sleep здесь не нужен

        # Приложение могло быть оставлено на постороннем экране с прошлого
        # раза (noReset=true не сбрасывает состояние; подробное объяснение —
        # в reset_to_home_screen). Сбрасываем на главный экран СРАЗУ при
        # создании сессии, а не только между вызовами get_vin() — иначе
        # именно ПЕРВЫЙ вызов в сессии стартует "вслепую" на неизвестном
        # экране и падает на шаге 2 (клик по пункту меню).
        log("1", "Сбрасываю приложение на главный экран (на случай постороннего состояния с прошлого раза)...")
        reset_to_home_screen(self.driver, timeout=10)

        # Приложение могло быть оставлено разлогиненным
        # (см. подробное объяснение у LOGIN_SCREEN_XPATH). Логинимся сразу
        # при создании сессии, чтобы первый же запрос не упирался в экран
        # входа. Неудачу здесь НЕ считаем фатальной — сессия всё равно
        # создаётся, а get_vin ниже сам ещё раз проверит вход и вернёт
        # понятный результат, если войти так и не получилось.
        ensure_logged_in(self.driver)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Закрываем Appium-сессию один раз, при выходе из блока `with` —
        # независимо от того, завершился ли блок нормально или с
        # исключением. Возвращаем False, чтобы НЕ подавлять исключение.
        if self.driver is not None:
            log("FINALLY", "Закрытие сессии Appium (driver.quit())...")
            self.driver.quit()
        return False

    def get_vin(
        self,
        plate_number: str,
        grade_hint: Optional[str] = None,
        _login_retry: bool = False,
        _restart_retry: bool = False,
        return_home: bool = True,
    ) -> Optional[str]:
        """
        Вводит номер автомобиля в приложении Carmoodo (используя уже
        открытую сессию self.driver) и возвращает VIN.

        В ОТЛИЧИЕ от HeyDealer (где отсутствие истории ремонта — это
        нормальный результат, а не ошибка), здесь тоже возврат None не
        считается исключительной ситуацией: если машина не найдена в базе
        Carmoodo, либо превышен дневной лимит запросов ("일 30건"), метод
        просто логирует понятную причину и возвращает None, а не бросает
        исключение — вызывающий код (например, Telegram-бот) не должен
        падать из-за одного "пустого" номера в очереди.

        :param plate_number: номер автомобиля (может содержать корейские символы)
        :param grade_hint: текст модели/трима машины
            с Encar (см. encar_parser.get_vehicle_number_and_grade), например
            "아반떼 XD 5DR 1.5 DOHC 스포츠 기본형". Нужен ТОЛЬКО если Carmoodo
            покажет экран выбора трима (см. TRIM_SCREEN_MARKER_TEXT) — по
            одному номеру машины она иногда не может однозначно определить
            комплектацию. Если экран выбора трима не появился — параметр
            просто не используется. Если он появился, а grade_hint не
            передан (None) или совпадение не нашлось — get_vin() вернёт
            None, как и при любом другом "не найдено" (см.
            _pick_matching_trim_option).
        :param _login_retry: служебный флаг, снаружи передавать не нужно.
            Ставится в True только при внутреннем повторе запроса после
            автоматического входа (см. обработку разлогина в шагах 2 и 6) —
            нужен, чтобы повтор случился максимум один раз и код не мог
            зациклиться, если приложение разлогинивает сразу же снова.
        :param _restart_retry: служебный флаг: приложение уже перезапускали
            в этом заходе. Не даёт зациклиться, если оно зависает снова
            сразу после перезапуска.
        :param return_home: вернуться ли в конце на главный экран (шаг 7);
            подробнее — в комментарии к шагу 7.
        :return: VIN строкой, либо None, если не удалось получить
        """
        driver = self.driver
        vin: Optional[str] = None
        # Сбрасываем причину прошлого запроса — иначе успешный запрос
        # унаследовал бы чужую пометку от предыдущего неудачного.
        self.last_reason = None

        # Приложение могло остаться на экране входа
        # (например, разлогинилось в прошлом запросе, или сессия создана
        # на разлогиненном приложении). Проверяем и логинимся ДО начала
        # сценария — иначе шаг 2 просто уйдёт в таймаут, ища пункт меню на
        # экране, где его нет.
        if not ensure_logged_in(driver):
            log("LOGIN", f'Приложение на экране входа и войти не удалось — VIN для номера "{plate_number}" получить нельзя.')
            self.last_reason = "login_failed"
            return None

        try:
            # -------------------------------------------------------------
            # Шаг 2: клик по пункту главного меню "상세사양조회"
            # (у пункта нет clickable-предка -> координатный тап, см. tap_by_text)
            # -------------------------------------------------------------
            log("2", f'Тап по пункту меню "{MENU_ITEM_TEXT}" '
                f'(таймаут до {APP_LAUNCH_TIMEOUT} сек на случай холодного '
                f'старта приложения)...')

            # Ждём ОБА возможных исхода сразу — пункт меню или форму
            # входа, — а не только тот, который мы надеемся увидеть.
            # Подробный разбор — в описании wait_for_menu_or_login.
            appeared = wait_for_menu_or_login(
                driver, MENU_ITEM_TEXT, APP_LAUNCH_TIMEOUT
            )

            if appeared == "login":
                log("2", "Вместо главного меню на экране форма входа — "
                    "приложение было разлогинено, а при создании сессии оно "
                    "ещё запускалось и форму показать не успело. Вхожу и "
                    "повторяю шаг.")
                if _login_retry:
                    # Второй раз подряд — значит, дело не в гонке при
                    # запуске, а в чём-то другом: неверный пароль, или
                    # приложение разлогинивает сразу же снова. Повторять
                    # дальше бессмысленно.
                    log("2", "Форма входа появилась снова сразу после входа — "
                        "дальше повторять смысла нет.")
                    self.last_reason = "login_failed"
                    return None
                if not ensure_logged_in(driver):
                    self.last_reason = "login_failed"
                    return None
                return self.get_vin(
                    plate_number, grade_hint=grade_hint,
                    _login_retry=True, return_home=return_home,
                )

            try:
                # Если пункт меню уже в дереве (appeared == "menu"),
                # ожидание внутри tap_by_text ниже отработает мгновенно.
                # Вызываем именно её: в ней тап по координатам центра, а
                # не просто поиск (см. пояснение в tap_by_text), и
                # дублировать эту логику здесь незачем.
                if appeared is None and (
                    screen_is_only_spinner(driver)
                    or screen_is_blind_login_form(driver)
                ):
                    # На экране одна крутилка и больше ничего — приложение
                    # зависло на загрузке. Ждать бессмысленно, и вторая
                    # попытка с новой Appium-сессией тоже не поможет:
                    # noReset=true сохраняет состояние приложения, поэтому
                    # она утыкается в ту же крутилку. Расклинить может
                    # только команда самому приложению.
                    #
                    # Цена промаха мала: перезапуск занимает секунды, а
                    # альтернатива — потерять весь запрос целиком, потому
                    # что без VIN не пойдёт и PARTSNUMBER.
                    # То же лечение — и для формы входа, которую
                    # Appium не видит (см. screen_is_blind_login_form).
                    log("2", "Приложение зависло (только крутилка загрузки или "
                        "форма входа без полей). Перезапускаю его и пробую ещё раз.")
                    if restart_app(driver) and not _restart_retry:
                        return self.get_vin(
                            plate_number, grade_hint=grade_hint,
                            _login_retry=_login_retry, return_home=return_home,
                            _restart_retry=True,
                        )
                    if _restart_retry:
                        log("2", "Приложение зависло снова сразу после "
                            "перезапуска — дальше повторять смысла нет.")
                        self.last_reason = "no_response"
                        dump_screen_for_debug(driver, "step2_stuck_after_restart")
                        return None

                tap_by_text(driver, MENU_ITEM_TEXT, timeout=DEFAULT_TIMEOUT)
            except Exception:
                dump_screen_for_debug(driver, "step2_menu_click_failed")
                raise
            time.sleep(0.5)  # даём экрану "자동차 사양조회" открыться, прежде
            # чем шаг 3 начнёт искать на нём поле ввода

            # -------------------------------------------------------------
            # Шаг 3: ожидание поля ввода номера на экране "자동차 사양조회"
            # и клик по нему.
            #
            # Поиск идёт через find_plate_input_field() — три ступени
            # надёжности плюс проверка, что мы вообще на нужном экране (см.
            # её описание). Один жёсткий XPath по hint="차량번호" здесь не
            # годится: подсказка отрисована отдельным заголовком над полем,
            # а не внутри него.
            # -------------------------------------------------------------
            log("3", "Поиск поля ввода номера на экране '자동차 사양조회'...")
            input_field = find_plate_input_field(driver, timeout=DEFAULT_TIMEOUT)
            input_field.click()
            time.sleep(0.35)  # даём системной/веб клавиатуре открыться,
            # прежде чем слать ADB_INPUT_TEXT — та же страховка, что и в
            # HeyDealer: broadcast до открытия клавиатуры может потеряться

            # -------------------------------------------------------------
            # Шаг 4: ввод номера через ADBKeyBoard broadcast
            # -------------------------------------------------------------
            log("4", f'Ввод номера "{plate_number}" через ADB_INPUT_TEXT broadcast...')
            adb_input_text(plate_number)
            time.sleep(0.2)

            # -------------------------------------------------------------
            # Шаг 5: клик по кнопке поиска "검색하기" (кликабельна напрямую)
            # -------------------------------------------------------------
            log("5", f'Клик по кнопке поиска "{SEARCH_BUTTON_DESC}"...')
            try:
                search_btn = WebDriverWait(driver, DEFAULT_TIMEOUT, poll_frequency=POLL_INTERVAL).until(
                    EC.presence_of_element_located((AppiumBy.XPATH, SEARCH_BUTTON_XPATH))
                )
            except TimeoutException:
                dump_screen_for_debug(driver, "step5_search_button_not_found")
                raise
            search_btn.click()

            # -------------------------------------------------------------
            # Шаг 6: ожидание результата — ГОНКА между несколькими исходами
            # (см. wait_for_vin_or_error_dialog): элемент с текстом,
            # начинающимся с "차대번호" (успех), нативный AlertDialog с
            # кнопкой OK (ошибка — "결과 없음" или неверный формат номера),
            # либо экран выбора трима.
            #
            # Диалог обязательно нужно распознать и закрыть (крайние
            # случаи — несуществующий номер / неверный формат): если
            # просто ждать VIN_RESULT_XPATH до таймаута, диалог остаётся
            # открытым, перекрывает нижнюю навигацию, и клик по "Home" в
            # шаге 7 промахивается — следующий вызов get_vin() для другого
            # номера стартует уже в поломанном состоянии.
            #
            # Цикл (а не одна проверка): после выбора трима (outcome ==
            # "trim_selection") результат ЕЩЁ НЕ готов — нужно снова ждать
            # VIN/диалог, теперь уже после подтверждения трима. Ограничено
            # MAX_TRIM_SELECTION_ATTEMPTS попытками (а не бесконечным
            # циклом) на случай, если экран выбора трима почему-то
            # появляется снова и снова (незнакомый пока сценарий — лучше
            # сдаться и оставить дамп экрана, чем зависнуть навсегда).
            # -------------------------------------------------------------
            MAX_TRIM_SELECTION_ATTEMPTS = 2
            trim_attempts = 0

            while True:
                log("6", f'Ожидание результата (VIN, диалог ошибки или экран выбора трима, до {SEARCH_RESULT_TIMEOUT} сек)...')
                outcome, element = wait_for_vin_or_error_dialog(driver, SEARCH_RESULT_TIMEOUT)

                if outcome == "trim_selection":
                    trim_attempts += 1
                    log("6", f'Появился экран выбора трима (попытка {trim_attempts}/{MAX_TRIM_SELECTION_ATTEMPTS}).')
                    if trim_attempts > MAX_TRIM_SELECTION_ATTEMPTS:
                        log("6", "Экран выбора трима появился повторно сверх лимита попыток — сдаюсь, чтобы не зависнуть.")
                        dump_screen_for_debug(driver, "trim_selection_repeated")
                        self.last_reason = "trim_unmatched"
                        return None

                    picked = _pick_matching_trim_option(driver, grade_hint)
                    if not picked:
                        log("6", f'Не удалось выбрать подходящий трим для номера "{plate_number}" (подсказка с Encar: {grade_hint!r}).')
                        dump_screen_for_debug(driver, "trim_selection_no_match")
                        self.last_reason = "trim_unmatched"
                        return None

                    # Трим выбран и "조회" нажата — возвращаемся к началу
                    # цикла и снова ждём результат (VIN/диалог/ещё раз
                    # экран выбора трима, если вдруг понадобится ещё один
                    # уровень уточнения — пока не встречалось на практике).
                    continue

                if outcome == "timeout":
                    # Ни VIN, ни диалог ошибки не появились за отведённое время —
                    # НЕ обязательно ошибка сценария: может сработать дневной
                    # лимит запросов (на экране ввода есть предупреждающий текст
                    # "일 30건" — 30 запросов в день). Логируем причину понятным
                    # текстом и возвращаем None, не бросая исключение.
                    limit_hit = len(driver.find_elements(AppiumBy.XPATH, f'//*[contains(@text, "{DAILY_LIMIT_TEXT}")]')) > 0
                    if limit_hit:
                        log("6", f'VIN не получен: похоже, исчерпан дневной лимит запросов ("{DAILY_LIMIT_TEXT}").')
                        self.last_reason = "daily_limit"
                    else:
                        log("6", f'VIN не получен: ни результат, ни диалог ошибки не появились за {SEARCH_RESULT_TIMEOUT} сек (номер "{plate_number}").')
                        # Не "не найдено": приложение вообще ничего не
                        # ответило. Скорее техническая заминка, чем ответ
                        # по существу — поэтому повтор имеет смысл.
                        self.last_reason = "no_response"
                    return None

                if outcome == "dialog":
                    # Вылезла ошибка ("조회된 결과가 없습니다." / "차량번호를
                    # 형식에 맞게 입력해주세요." и т.п.) — читаем текст сообщения
                    # для понятного лога (какая именно причина), закрываем диалог
                    # кликом по OK и возвращаем None, не бросая исключение.
                    try:
                        message_el = driver.find_element(AppiumBy.XPATH, DIALOG_MESSAGE_XPATH)
                        message_text = message_el.text.strip()
                    except Exception:
                        message_text = "(не удалось прочитать текст сообщения)"
                    log("6", f'VIN не получен: приложение показало диалог с сообщением "{message_text}" для номера "{plate_number}". Закрываю диалог (OK).')
                    element.click()  # element здесь — кнопка OK (см. возврат wait_for_vin_or_error_dialog)
                    time.sleep(0.3)

                    # Иногда этот диалог — не ошибка поиска, а сообщение о
                    # том, что приложение разлогинило пользователя. Внешне
                    # он выглядит так же (окно с кнопкой OK), но после
                    # закрытия приложение оказывается на экране входа, и
                    # без обработки ВСЕ последующие запросы падали бы по
                    # таймауту до ручного входа.
                    #
                    # Поэтому после закрытия диалога проверяем, не оказались
                    # ли мы на экране входа. Если да — логинимся и ПОВТОРЯЕМ
                    # запрос (рекурсивным вызовом get_vin), а не теряем его: с точки зрения пользователя разлогин
                    # посреди проверки не должен превращаться в "VIN не
                    # найден", ведь машина-то, возможно, в базе есть.
                    if is_login_screen(driver):
                        log("6", "После закрытия диалога приложение оказалось на экране входа — похоже, это был диалог о разлогине.")
                        if _login_retry:
                            log("6", "Повторный вход в рамках одного запроса уже выполнялся — не пытаюсь снова, чтобы не зациклиться.")
                            self.last_reason = "login_failed"
                            return None
                        if not perform_login(driver):
                            self.last_reason = "login_failed"
                            return None
                        log("6", f'Вход выполнен — повторяю запрос для номера "{plate_number}" с начала.')
                        # Рекурсивный повтор ровно ОДИН раз (_login_retry=True
                        # запрещает следующий) — так запрос не теряется из-за
                        # разлогина. Блок finally текущего вызова (шаг 7,
                        # возврат на главный экран) отработает как обычно,
                        # уже после того как повтор вернёт результат.
                        return self.get_vin(plate_number, grade_hint, _login_retry=True,
                                            return_home=return_home)

                    if looks_like_service_error_dialog(message_text):
                        # Сбой на стороне самой Carmoodo (см.
                        # SERVICE_ERROR_DIALOG_MARKERS).
                        # Про машину это не говорит ничего: она вполне может
                        # быть в базе, просто сервис до неё не достучался.
                        # Назвать это "не найдена" значило бы дать уверенный
                        # неверный ответ и лишить человека повода
                        # перепроверить позже.
                        log("6", "Это сообщение о сбое на стороне сервиса, а не "
                            "ответ про машину — помечаю как временную неудачу, "
                            "запрос из дневной квоты не засчитываю.")
                        self.last_reason = "service_error"
                        return None

                    # Обычный диалог об ошибке поиска ("조회된 결과가
                    # 없습니다." / неверный формат номера) — это ОТВЕТ ПО
                    # СУЩЕСТВУ, а не сбой: машины в базе нет.
                    self.last_reason = "not_found"
                    return None

                # outcome == "vin" — успех, element здесь — элемент с VIN
                vin_el = element
                raw_text = vin_el.text.strip()
                if not raw_text.startswith(VIN_LABEL_PREFIX):
                    # Подстраховка: element найден по starts-with, но текст мог
                    # обновиться между поиском и чтением .text (перерисовка
                    # WebView) — на всякий случай перепроверяем перед отсечением
                    # префикса
                    log("6", f'Неожиданный текст результата (не начинается с "{VIN_LABEL_PREFIX}"): "{raw_text}". VIN не извлечён.')
                    self.last_reason = "unexpected_result"
                    return None

                vin = raw_text[len(VIN_LABEL_PREFIX):].strip()
                if not vin:
                    log("6", f'Строка результата состоит только из метки "{VIN_LABEL_PREFIX}" без значения — VIN пуст.')
                    self.last_reason = "unexpected_result"
                    return None

                log("6", f'VIN получен: "{vin}"')
                return vin

        except Exception as e:
            log("ERROR", f'Произошла ошибка выполнения сценария для номера "{plate_number}": {e}')
            raise

        finally:
            # -------------------------------------------------------------
            # Шаг 7: возврат к началу через нижнюю навигацию "Home" — она
            # есть на всех экранах приложения, поэтому это самый надёжный
            # способ сбросить состояние перед следующим номером (надёжнее,
            # чем driver.back(), результат которого зависит от того, на
            # каком именно экране произошла ошибка). Выполняется ВСЕГДА, и
            # при успехе, и при любой ошибке внутри try — по той же логике,
            # что и шаг 10 в HeyDealerSession.get_repair_history(): сессия
            # переиспользуется между номерами, значит нельзя оставить
            # driver на "чужом" экране перед следующим вызовом.
            #
            # Обёрнуто в собственный try/except: если внутри try выше уже
            # было исключение, именно оно должно дойти до вызывающего кода
            # (raise в except-блоке выше это обеспечивает) — ошибка при
            # попытке нажать "Home" не должна его подменить собой.
            # -------------------------------------------------------------
            # ПРОПУСК ШАГА (return_home=False): по замеру этот шаг занимает
            # около 4 секунд из ~32, потраченных на весь Carmoodo, и в
            # работе бота они тратятся ВПУСТУЮ.
            #
            # Возврат на главный экран нужен ровно для одного: чтобы
            # СЛЕДУЮЩИЙ вызов get_vin() в ТОЙ ЖЕ сессии начинался с
            # известного места. Но бот закрывает сессию сразу после
            # получения VIN (одно устройство — одна живая сессия), и
            # новая сессия всё равно сама сбрасывается на главный экран в
            # CarmoodoSession.__enter__().
            #
            # По умолчанию шаг выполняется (return_home=True) — это нужно
            # ручному тестовому запуску внизу файла, где сессия
            # переиспользуется для нескольких номеров подряд. Бот же
            # передаёт False и экономит эти 4 секунды на каждом запросе.
            if return_home:
                log("7", f'Возврат к началу (клик по нижней навигации "{HOME_NAV_DESC}")...')
                # Общая функция reset_to_home_screen (используется и здесь,
                # и в CarmoodoSession.__enter__ — см. её подробное
                # описание): сначала закрыть возможный зависший диалог,
                # потом кликнуть Home.
                reset_to_home_screen(driver, timeout=DEFAULT_TIMEOUT)
            else:
                log("7", "Возврат на главный экран пропущен — сессия сейчас закроется, "
                    "а новая сама начнёт с главного экрана.")


# ---------------------------------------------------------------------------
# Точка входа для тестового запуска
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Сессия создаётся один раз через `with CarmoodoSession() as session:`,
    # get_vin() вызывается на ней несколько раз подряд для РАЗНЫХ тестовых
    # номеров — это показывает, что сессия переживает несколько запросов
    # подряд без пересоздания Appium-сессии/перезапуска приложения (в логах
    # не будет повторного шага "1" перед вторым/третьим номером).
    TEST_PLATE_NUMBER_1 = "54노7511"   # ожидаемый VIN: WBA5A7101FD817194
    TEST_PLATE_NUMBER_2 = "145두7195"  # из истории поиска на экране — для проверки переиспользования сессии
    TEST_PLATE_NUMBER_3 = "145두0001"  # НЕСУЩЕСТВУЮЩИЙ номер — крайний
    # случай (диалог "조회된 결과가 없습니다."). Стоит НЕ последним
    # намеренно: само по себе успешное закрытие диалога ещё не доказывает,
    # что сессия осталась в рабочем состоянии для СЛЕДУЮЩЕГО номера — а
    # именно порчу состояния после диалога и нужно исключить. Если номер
    # стоит последним, лог этого не покажет вообще. По логу шага "1"
    # (запуск сессии) будет видно, что он один на все четыре прогона, а
    # TEST_PLATE_NUMBER_4 ниже подтвердит, что сессия реально
    # восстановилась и продолжает нормально работать сразу после сбоя.
    TEST_PLATE_NUMBER_4 = "125라8678"  # контрольный запрос СРАЗУ ПОСЛЕ
    # несуществующего номера — если он тоже отработает штатно (а не упадёт
    # на шаге 2 из-за зависшего диалога), значит обработка диалога
    # действительно сохраняет рабочее состояние сессии, а не просто
    # логирует саму ошибку

    try:
        with CarmoodoSession() as session:
            vin1 = session.get_vin(TEST_PLATE_NUMBER_1)
            print(f"\n=== РЕЗУЛЬТАТ ({TEST_PLATE_NUMBER_1}) ===")
            print(json.dumps({"plate": TEST_PLATE_NUMBER_1, "vin": vin1}, ensure_ascii=False, indent=2))

            vin2 = session.get_vin(TEST_PLATE_NUMBER_2)
            print(f"\n=== РЕЗУЛЬТАТ ({TEST_PLATE_NUMBER_2}) ===")
            print(json.dumps({"plate": TEST_PLATE_NUMBER_2, "vin": vin2}, ensure_ascii=False, indent=2))

            vin3 = session.get_vin(TEST_PLATE_NUMBER_3)
            print(f"\n=== РЕЗУЛЬТАТ ({TEST_PLATE_NUMBER_3}, ожидается None — номер не существует) ===")
            print(json.dumps({"plate": TEST_PLATE_NUMBER_3, "vin": vin3}, ensure_ascii=False, indent=2))

            vin4 = session.get_vin(TEST_PLATE_NUMBER_4)
            print(f"\n=== РЕЗУЛЬТАТ ({TEST_PLATE_NUMBER_4}, контрольный запрос ПОСЛЕ несуществующего номера) ===")
            print(json.dumps({"plate": TEST_PLATE_NUMBER_4, "vin": vin4}, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"\nСкрипт завершился с ошибкой: {exc}")
