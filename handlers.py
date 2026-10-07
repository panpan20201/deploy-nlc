# -*- coding: utf-8 -*-
"""
handlers.py
===========

Реакция бота на команды и сообщения. Вся тяжёлая логика — в pipeline.py,
здесь она только вызывается и оборачивается в обработчики Telegram.

ОСНОВНЫЕ ПРИНЦИПЫ
-----------------

1. МЕТКА ЗАПРОСА. Каждой проверке присваивается короткий идентификатор
   (например `a3f19c`). Он проставляется во ВСЕ строки лога, которые эта
   проверка породит, — включая строки из Appium-модулей, работающих в
   отдельных потоках. Когда человек пишет "у меня опять не сработало",
   достаточно попросить у него эту метку из сообщения и найти по ней в
   `logs/nlc.log` ровно шаги его проверки, без чужих: без метки выцепить
   нужный запрос из перемешанного потока строк практически нельзя.

2. ЧЕСТНОЕ СООБЩЕНИЕ ОБ ОЧЕРЕДИ. Планшет один, и запросы к нему идут по
   очереди. Если устройство занято, об этом сразу говорится — иначе
   второй человек молча ждал бы минуты, не понимая, работает бот или
   завис.

3. ПРОСТАЯ ОБРАБОТКА ОШИБОК. pipeline не бросает исключений (кроме
   ошибки разбора ссылки Encar) и всегда возвращает готовый текст,
   поэтому здесь ровно два случая: "ссылку не разобрали" и
   "непредвиденное" — последнее уже означает настоящую аварию, а не
   обычный сбой шага.

4. КОМАНДА /status — быстрый ответ на вопрос "бот вообще жив и чем занят".
"""

import asyncio
import html
import logging
import os
import re

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter
from telegram.ext import ContextTypes

import ads_id
import diagnostics
import nlc_logging
import pipeline
import storage
import usage
from encar_parser import EncarParsingError

logger = logging.getLogger(__name__)

# Планшет, на котором сбрасываем рекламный ID (тот же, что и для проверок).
ANDROID_UDID = os.getenv("ANDROID_UDID")

# Пользователи, для которых прямо сейчас идёт проверка. Отдельно от лока
# на устройство (тот про Appium): нужен, чтобы один человек не запускал
# несколько проверок разом. Иначе он же сам себе создаёт очередь и потом
# удивляется, почему всё так долго.
_active_users: set[int] = set()

WELCOME_TEXT = (
    "Привет! 👋\n\n"
    "Я бот для проверки истории подержанного автомобиля по объявлению с сайта Encar или KB Chachacha.\n\n"
    "Как пользоваться:\n"
    "1. Найди на Encar или KB Chachacha объявление о продаже интересующего тебя автомобиля.\n"
    "2. Скопируй ссылку на это объявление.\n"
    "3. Пришли мне эту ссылку сюда, в чат.\n\n"
    "Я извлеку номер автомобиля и проверю его по трём источникам:\n"
    "📋 историю ремонта (HeyDealer)\n"
    "🔑 VIN-номер (Carmoodo)\n"
    "🔧 детали и дату производства по VIN (PARTSNUMBER)\n\n"
    "Результат буду присылать по мере готовности каждой части — "
    "полная проверка обычно занимает пару минут.\n\n"
    "Команда /status покажет, свободен ли бот прямо сейчас.\n"
    "Команда /diag проверит, всё ли в порядке с оборудованием."
)

LINK_PATTERN = re.compile(r"(http|encar)", re.IGNORECASE)

# Слово рядом со ссылкой, которым можно попросить перепроверить машину
# заново, не беря сохранённые данные. Нужно, когда данные заведомо
# устарели — например, машина только что была на сервисе. Стоит запросов
# из дневных лимитов, поэтому работает только по явной просьбе.
REFRESH_PATTERN = re.compile(r"обнов|заново|перепровер|refresh", re.IGNORECASE)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Пользователь %s (id=%s) вызвал /start", user.username, user.id)
    await update.message.reply_text(WELCOME_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("Пользователь %s (id=%s) вызвал /help", user.username, user.id)
    await update.message.reply_text(WELCOME_TEXT)



# ---------------------------------------------------------------------------
# АДМИНИСТРАТОР И УВЕДОМЛЕНИЯ
#
# ADMIN_CHAT_ID — та же настройка из .env, по которой монитор шлёт
# оповещения о поломках: одна настройка на одного и того же человека.
# ---------------------------------------------------------------------------

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")


def _is_admin(user_id) -> bool:
    """
    Он ли это. Сравниваем СТРОКАМИ: из .env значение приходит строкой, а
    из Telegram — числом, и прямое сравнение 123456789 == "123456789"
    молча даёт False. Такая ошибка не падает и не пишет в лог — она
    просто тихо лишает администратора прав, и разбираться потом, почему
    команда отвечает «не админ», пришлось бы долго.
    """
    if not ADMIN_CHAT_ID:
        return False
    return str(user_id) == str(ADMIN_CHAT_ID).strip()


def _describe_user(user) -> str:
    """
    Человекочитаемое описание пользователя для уведомлений.

    Username есть не у всех — Telegram его не требует. Поэтому собираем
    из того, что есть, и ВСЕГДА добавляем id: имя можно поменять в любой
    момент, id — нет, и только по нему пользователя потом можно опознать.
    """
    parts = []
    full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
    if full_name:
        parts.append(full_name)
    if user.username:
        parts.append(f"@{user.username}")
    if not parts:
        parts.append("без имени и username")
    return f"{' '.join(parts)} (id={user.id})"


async def _notify_admin(context, text: str) -> None:
    """
    Отправляет сообщение администратору.

    НИКОГДА не бросает исключений. Уведомление — это удобство, а не часть
    проверки: если Telegram не ответил или ADMIN_CHAT_ID задан с опечаткой,
    проверка машины должна пройти как обычно. Обратное поведение означало
    бы, что опечатка в одной строке .env ломает основную работу бота.
    """
    if not ADMIN_CHAT_ID:
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as exc:
        logger.warning("Не удалось отправить уведомление администратору: %s", exc)


async def _notify_admin_result(context, user, request_id: str, final_text: str) -> None:
    """
    Копия итогового ответа клиенту — администратору.

    Результат уже в HTML (его так же видит клиент), поэтому шапку
    экранируем: имя пользователя может содержать «<», и Telegram
    отверг бы всё сообщение. Если шапка с результатом не влезает в
    лимит Telegram (4096 символов), шлём их двумя сообщениями. Как и
    _notify_admin, никогда не бросает исключений.
    """
    if not ADMIN_CHAT_ID:
        return
    header = (
        f"✅ Проверка завершена\n"
        f"Пользователь: {html.escape(_describe_user(user))}\n"
        f"Метка в логе: {request_id}\n"
        f"Клиент получил:"
    )
    try:
        combined = header + "\n\n" + final_text
        if len(combined) <= 4096:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=combined, parse_mode=ParseMode.HTML
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=header, parse_mode=ParseMode.HTML
            )
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=final_text, parse_mode=ParseMode.HTML
            )
    except Exception as exc:
        logger.warning("Не удалось отправить администратору копию результата: %s", exc)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Быстрый ответ на вопрос "бот жив и чем занят".

    Ценность не в самой информации, а в том, что до неё можно добраться
    из телефона, не подходя к компьютеру: без неё единственный способ
    понять, работает ли бот, — прислать ссылку и ждать пару минут.
    """
    busy = pipeline.device_busy()
    active = len(_active_users)
    if busy:
        lines = [
            "🟡 Бот работает, планшет сейчас занят проверкой.",
            f"Активных проверок: {active}.",
            "Новая ссылка встанет в очередь и начнётся сразу после текущей.",
        ]
    else:
        lines = ["🟢 Бот работает, планшет свободен — можно присылать ссылку."]

    # Расход дневного лимита Carmoodo — самый дефицитный ресурс системы
    # (30 запросов в сутки). Показываем его, чтобы лимит не заканчивался
    # неожиданно, посреди рабочего дня.
    lines.append("")
    lines.append(usage.status_line())
    lines.append(storage.stats_line())

    await update.message.reply_text("\n".join(lines))


async def diag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Полная проверка оборудования по требованию.

    Смысл — в том, что её можно запустить с телефона, не подходя к
    компьютеру, а не идти смотреть на окна на ноутбуке. Ответ приходит
    в чат: планшет отвалился, Appium закрыли, клавиатура
    слетела — с конкретным указанием, что сделать.

    Проверки лезут к adb и по сети, то есть блокируют поток на секунды —
    поэтому выполняются в отдельном потоке, чтобы не морозить бота.
    """
    user = update.effective_user
    logger.info("Пользователь %s (id=%s) вызвал /diag", user.username, user.id)

    notice = await update.message.reply_text("Проверяю оборудование... ⏳")
    report = await asyncio.to_thread(diagnostics.format_report)
    try:
        await notice.edit_text(report, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(report, parse_mode=ParseMode.HTML)


async def resetads_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ручной сброс рекламного идентификатора Google — снимает дневной лимит
    HeyDealer (10 проверок в сутки на устройство).

    Автоматический сброс запускается сам по признаку "лимит исчерпан"
    (см. pipeline.py). Команда нужна на случай, когда автоматика по
    какой-то причине не сработала, и для ручной проверки самого
    механизма: сбросить можно из чата, не подходя к планшету.

    Сброс занимает планшет, поэтому берётся тот же лок, что и у проверок:
    иначе тапы по настройкам Google наложились бы на идущий сценарий в
    приложении.
    """
    user = update.effective_user
    logger.info("Пользователь %s (id=%s) вызвал /resetads", user.username, user.id)

    allowed, why = usage.can_reset_ads()
    if not allowed:
        await update.message.reply_text(f"Сброс сейчас не выполняю: {why}.")
        return

    notice = await update.message.reply_text(
        "Сбрасываю рекламный ID (снимает дневной лимит HeyDealer)... ⏳"
    )

    async with pipeline.device_lock():
        # HeyDealer читает идентификатор при запуске, поэтому его сессию
        # надо закрыть — иначе приложение продолжит работать со старым
        # значением, и лимит останется на месте.
        await pipeline.close_device_sessions("сброс рекламного ID")
        success, message = await asyncio.to_thread(ads_id.reset_advertising_id, ANDROID_UDID)

    if success:
        count = usage.record_ads_reset()
        text = (
            f"✅ {message}.\n"
            f"Сбросов за сутки: {count} из {usage.MAX_ADS_RESETS_PER_DAY}.\n"
            "Проверки HeyDealer снова доступны."
        )
    else:
        text = (
            f"❌ Не получилось: {message}.\n\n"
            "Сбросить можно вручную: Настройки → Google → Реклама → "
            "сбросить рекламный идентификатор.\n"
            "⛔ Аккаунт в HeyDealer при этом НЕ удалять — удалённый "
            "блокируется на 3 месяца."
        )

    try:
        await notice.edit_text(text)
    except Exception:
        await update.message.reply_text(text)


# ===========================================================================
# Доставка готового результата при обрывах связи
# ===========================================================================
#
# Интернет на сервере может пропадать на минуты: готовый результат при
# этом не уходит ни новым сообщением (ConnectTimeout), ни запасной
# правкой (Timed out). Одной попытки мало — доставку повторяем несколько
# минут.
#
# Повторять можно не всё. Таймаут на ЧТЕНИИ ответа не значит, что
# сообщение не дошло: повтор отправки дал бы двойной ответ. Поэтому
# новое сообщение повторяем, только когда запрос ТОЧНО не
# ушёл, а при неясном исходе переходим на правку исходного сообщения —
# правка тем же текстом дубля не создаёт.

# Паузы между повторами, сек: вместе около пяти минут на каждый способ.
DELIVERY_RETRY_DELAYS = (5, 10, 20, 30, 60, 60, 60, 60)


def _not_sent_wait(exc: BaseException):
    """
    Сколько ждать перед повтором, если запрос ТОЧНО не дошёл до Telegram.
    None — исход неясен или ошибка не сетевая: повторять отправку нельзя.
    """
    if isinstance(exc, RetryAfter):
        # Flood control: Telegram сообщение не принял и сам сказал, сколько ждать.
        retry_after = exc.retry_after
        return retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
    if not isinstance(exc, NetworkError):
        return None
    cause = exc.__cause__
    # Соединение не установлено (нет сети, DNS) или очередь соединений
    # занята — запрос не отправлялся вовсе.
    if isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return 0.0
    # 502 от шлюза Telegram: до бота-сервера запрос не дошёл.
    if cause is None and "bad gateway" in str(exc).lower():
        return 0.0
    return None


async def _deliver_final(send_new, show_in_place, delays=DELIVERY_RETRY_DELAYS,
                         sleep=asyncio.sleep) -> str:
    """
    Доставляет результат: сначала новым сообщением (с уведомлением на
    телефон), если не вышло — правкой исходного. Возвращает "new",
    "edit" или "failed".

    send_new() — отправить новое сообщение (бросает при неудаче);
    show_in_place() — показать результат правкой, True при успехе.
    """
    for attempt in range(len(delays) + 1):
        try:
            await send_new()
            return "new"
        except Exception as exc:
            wait = _not_sent_wait(exc)
            if wait is None:
                logger.exception(
                    "Не удалось отправить результат новым сообщением; исход неясен, "
                    "повтор мог бы дать дубль — показываю правкой."
                )
                break
            if attempt == len(delays):
                logger.error("Результат не отправлен новым сообщением ни за одну из %d попыток: %s",
                             attempt + 1, exc)
                break
            pause = max(wait, delays[attempt])
            logger.warning("Результат не ушёл (попытка %d из %d): %s — повторю через %.0f с.",
                           attempt + 1, len(delays) + 1, exc, pause)
            await sleep(pause)

    logger.info("Показываю результат правкой исходного сообщения — новое отправить не вышло.")
    for attempt in range(len(delays) + 1):
        if await show_in_place():
            return "edit"
        if attempt < len(delays):
            await sleep(delays[attempt])

    logger.error("Результат так и не доставлен: ни новым сообщением, ни правкой.")
    return "failed"


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text

    if not LINK_PATTERN.search(text):
        logger.info("Сообщение от %s (id=%s) не похоже на ссылку.", user.username, user.id)
        await update.message.reply_text(
            "Не похоже, что это ссылка на объявление с Encar или KB Chachacha 🤔\n"
            "Пришли, пожалуйста, ссылку на страницу автомобиля с Encar или KB Chachacha, "
            "и я начну проверку."
        )
        return

    if user.id in _active_users:
        await update.message.reply_text(
            "⏳ У вас уже есть активная проверка — дождитесь её завершения, "
            "прежде чем присылать следующую ссылку."
        )
        return

    # Метка запроса: с этого момента ВСЕ строки лога этой проверки —
    # включая те, что напишут Appium-модули из своих потоков — помечены
    # ею. Работает благодаря contextvars, см. nlc_logging.py.
    request_id = nlc_logging.new_request_id()
    nlc_logging.set_request_id(request_id)

    logger.info("Проверка начата. Пользователь %s (id=%s), ссылка: %s",
                user.username, user.id, text)

    # Уведомление администратору о каждой проверке. Отправляется ДО
    # начала работы, а не после: проверка идёт больше минуты, и знать о
    # ней в момент запуска полезнее, чем узнать постфактум. Если
    # отправка не удалась — работа продолжается как ни в чём не бывало
    # (см. _notify_admin): уведомление это удобство, а не условие.
    if not _is_admin(user.id):
        await _notify_admin(
            context,
            f"🔍 Проверка запущена\n\n"
            f"Пользователь: {_describe_user(user)}\n"
            f"Ссылка: {text.strip()}\n"
            f"Метка в логе: {request_id}",
        )

    _active_users.add(user.id)
    try:
        # Если планшет занят, честно говорим об этом СРАЗУ. Молчаливое
        # ожидание в минуту-две неотличимо от зависшего бота, и человек
        # начинает слать ссылку повторно, делая очередь ещё длиннее.
        force_refresh = bool(REFRESH_PATTERN.search(text))
        if force_refresh:
            logger.info("Запрошена принудительная перепроверка (слово-ключ в сообщении).")

        if pipeline.device_busy():
            first_text = (
                "Ссылка получена. Сейчас идёт другая проверка — "
                "ваша встанет в очередь и начнётся следом. ⏳"
            )
        elif force_refresh:
            first_text = "Ссылка получена, перепроверяю заново (без сохранённых данных)... ⏳"
        else:
            first_text = "Ссылка получена, начинаю проверку... ⏳"

        sent_message = await update.message.reply_text(first_text)

        # ЗАЩИТА ОТ ДВОЙНОГО ОТВЕТА.
        #
        # Проверка идёт в одном сообщении, которое редактируется по мере
        # готовности частей. Если очередное обновление совпадает с уже
        # показанным текстом слово в слово (обычное дело: например, VIN не
        # получен, значит PARTSNUMBER пропущен, и после HeyDealer добавлять
        # уже нечего), Telegram отвергает такую правку с ошибкой
        # "message is not modified". Считай мы это сбоем, результат ушёл бы
        # ещё и новым сообщением — человек получил бы один и тот же ответ
        # два раза подряд.
        #
        # Поэтому текст последнего показанного сообщения запоминается:
        # совпадающие правки просто пропускаются (заодно экономим запросы
        # к Telegram), а "не изменилось" считается успехом, а не сбоем.
        # Неудачей правка считается только НАСТОЯЩАЯ — например, если
        # человек удалил сообщение бота.
        last_shown = {"text": first_text}

        async def render(text: str) -> bool:
            """Показать текст в том же сообщении. True — человек его увидел."""
            if text == last_shown["text"]:
                return True
            try:
                await sent_message.edit_text(text, parse_mode=ParseMode.HTML)
                last_shown["text"] = text
                return True
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    # Telegram считает, что показывать нечего — значит,
                    # нужный текст уже на экране. Это успех, а не сбой.
                    last_shown["text"] = text
                    return True
                logger.warning("Не удалось отредактировать сообщение: %s", exc)
                return False

        async def send_partial_result(progress_text: str) -> None:
            # pipeline каждый раз отдаёт ПОЛНЫЙ текст, накопленный на
            # данный момент, поэтому здесь ничего склеивать не нужно —
            # просто показываем то, что пришло, редактируя одно и то же
            # сообщение (а не заваливая чат новыми).
            await render(progress_text)

        final_text = await _run_check(text, send_partial_result, request_id, force_refresh)

        # Готовый результат приходит ОТДЕЛЬНЫМ НОВЫМ сообщением, а
        # сообщение с ходом проверки удаляется.
        #
        # Зачем. Telegram НЕ шлёт уведомление при редактировании
        # сообщения — только при новом. Если дописывать результат в то же
        # сообщение, человек никак не узнает, что проверка закончилась:
        # придётся самому открывать чат и смотреть. А проверка идёт
        # полторы-две минуты, за которые нормально переключиться на другие
        # дела. Новое сообщение даёт обычное уведомление на телефон.
        #
        # Порядок важен: СНАЧАЛА отправляем результат, ПОТОМ удаляем
        # старое. Если сделать наоборот и отправка сорвётся, человек
        # останется вообще ни с чем — и ходом проверки, и результатом.
        # При таком порядке худшее, что может случиться, — в чате
        # останутся два сообщения вместо одного.
        #
        # Обрывы связи переживает _deliver_final: повторяет отправку
        # несколько минут, а если новое сообщение не ушло — дописывает
        # результат в то, которое уже висит в чате (без уведомления, но
        # человек его увидит).
        async def send_new() -> None:
            await update.message.reply_text(final_text, parse_mode=ParseMode.HTML)

        delivery = await _deliver_final(send_new, lambda: render(final_text))

        if delivery == "new":
            try:
                await sent_message.delete()
            except Exception as exc:
                # Удаление — косметика. Telegram может отказать, например
                # если сообщение старше 48 часов. Результат человек уже
                # получил, так что это не повод шуметь в логе ошибкой.
                logger.debug("Не удалось удалить сообщение о ходе проверки: %s", exc)

        # Копия результата администратору — вдобавок к «Проверка
        # запущена»: администратор видит не только, что кто-то пробивает
        # машину, но и что он получил.
        if not _is_admin(user.id):
            await _notify_admin_result(context, user, request_id, final_text)

        logger.info("Проверка завершена.")
    finally:
        # Освобождаем пользователя ВСЕГДА. Без этого один сбой навсегда
        # заблокировал бы человеку возможность прислать новую ссылку — до
        # перезапуска бота.
        _active_users.discard(user.id)


async def _run_check(link: str, on_partial_result, request_id: str,
                     force_refresh: bool = False) -> str:
    """
    Запускает проверку и превращает возможные ошибки в понятный текст.

    Случаев ровно два, потому что pipeline не падает сам
    (см. правило 1 в его шапке) и всегда возвращает готовый ответ — даже
    когда часть шагов не удалась.
    """
    try:
        return await pipeline.process_encar_link(
            link, on_partial_result=on_partial_result, force_refresh=force_refresh
        )

    except EncarParsingError as exc:
        # Не удалось вытащить номер машины из ссылки: битая ссылка, не та
        # страница, изменилась вёрстка сайта. Проверка даже не начиналась.
        logger.warning("Не удалось разобрать ссылку на объявление: %s", exc)
        return (
            "😕 Не удалось получить номер машины по этой ссылке.\n"
            "Проверь, что это прямая ссылка на страницу объявления Encar или KB Chachacha, "
            "и попробуй ещё раз."
        )

    except Exception:
        # Сюда попадаем только при настоящей аварии — обычные сбои шагов
        # pipeline обрабатывает сам и наружу не выпускает. Поэтому метка
        # запроса в ответе не формальность: по ней сразу находится нужное
        # место в логе.
        logger.exception("Непредвиденная ошибка при обработке ссылки: %s", link)
        return (
            "⚠️ Что-то пошло не так во время проверки. "
            "Попробуй, пожалуйста, ещё раз чуть позже.\n"
            f"<i>Код обращения: {request_id}</i>"
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Глобальный обработчик ошибок — ловит исключения из любого обработчика,
    чтобы бот не завершал работу целиком из-за одной ошибки.
    """
    logger.error("Ошибка при обработке обновления: %s", context.error, exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Произошла ошибка, попробуйте ещё раз")
        except Exception:
            logger.exception("Не удалось отправить пользователю сообщение об ошибке.")
