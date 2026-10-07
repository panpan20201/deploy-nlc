# -*- coding: utf-8 -*-
"""
monitor.py
==========

Фоновый присмотр за системой.

ГЛАВНАЯ ИДЕЯ: УЗНАВАТЬ О ПОЛОМКЕ РАНЬШЕ КЛИЕНТА
------------------------------------------------

Остальной код делает систему устойчивее к сбоям. Но остаётся
класс проблем, от которых код защититься не может в принципе: планшет
отключили от USB, кто-то закрыл окно Appium, на планшете сменили
клавиатуру, пропал интернет. Система честно сообщит об этом — но только
тому, кто в этот момент прислал ссылку. То есть первым о поломке узнаёт
КЛИЕНТ, а владелец — от клиента, в лучшем случае через час.

Этот модуль переворачивает порядок: он сам, раз в несколько минут,
проверяет оборудование и пишет владельцу в Telegram, как только что-то
сломалось. И отдельным сообщением — когда починилось.

ПОЧЕМУ УВЕДОМЛЕНИЯ ТОЛЬКО ПРИ ИЗМЕНЕНИИ СОСТОЯНИЯ
--------------------------------------------------

Самая частая ошибка в таких оповещениях — слать сообщение при каждой
проверке, пока проблема не устранена. Если планшет отключили на ночь, к
утру набегает две сотни одинаковых сообщений. Их перестают читать — а
вместе с ними перестают читать и те, что действительно важны.

Поэтому здесь сообщение уходит РОВНО ДВА РАЗА на одну поломку: когда
она появилась и когда исчезла. Между ними — тишина, даже если проверок
за это время прошло сто.

БЕЗ НАСТРОЙКИ ADMIN_CHAT_ID МОДУЛЬ ПРОСТО НЕ РАБОТАЕТ
------------------------------------------------------

И это нормально: проверки продолжают идти и писаться в лог, просто
никому не отправляются. Ничего не ломается и не падает — см. start().
"""

import asyncio
import logging
import time
import os
from typing import Optional

import backup
import diagnostics
import stats
import storage
import usage

logger = logging.getLogger(__name__)

# Куда слать оповещения. Свой chat_id можно узнать, написав боту
# @userinfobot в Telegram.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

CHECK_INTERVAL_SEC = 300.0  # раз в 5 минут. Чаще незачем: проверка лезет
# к adb и по сети, а поломки такого рода не требуют реакции за секунды.
# Реже — и есть риск узнать о проблеме позже клиента, то есть потерять
# весь смысл модуля.

# Час, в который уходит дневная сводка. Вечер, а не утро: к этому времени
# день отработан, и сводка описывает его целиком.
DAILY_SUMMARY_HOUR = 21

FIRST_CHECK_DELAY_SEC = 60.0  # первая проверка через минуту после старта:
# при запуске preflight уже всё проверил, дублировать сразу не нужно.


class Monitor:
    """
    Периодически проверяет оборудование и оповещает владельца об
    изменениях.

    Состояние каждой проверки запоминается между обходами — именно
    сравнение с прошлым разом и позволяет отличить "сломалось прямо
    сейчас" от "давно сломано и владелец уже знает".
    """

    def __init__(self, bot, admin_chat_id: Optional[str]):
        self.bot = bot
        self.admin_chat_id = admin_chat_id
        self._previous_state: dict[str, bool] = {}
        self._task: Optional[asyncio.Task] = None
        # За какой день сводка уже отправлена. Хранится в памяти: после
        # перезапуска бота сводка за сегодня уйдёт повторно, и это лучше,
        # чем не уйти вовсе.
        self._summary_sent_for: Optional[str] = None

    # -- отправка ---------------------------------------------------------

    async def notify(self, text: str) -> None:
        """
        Отправить сообщение владельцу.

        Ошибку отправки только логируем: если Telegram недоступен, это не
        повод ронять фоновую задачу — она нужна как раз для того, чтобы
        пережить проблемы, а не умереть от первой же.
        """
        if not self.admin_chat_id:
            return
        try:
            await self.bot.send_message(chat_id=self.admin_chat_id, text=text)
        except Exception as exc:
            logger.warning("Не удалось отправить оповещение владельцу: %s", exc)

    # -- один обход -------------------------------------------------------

    async def check_once(self) -> None:
        """
        Один обход проверок. Оповещает только о том, что ИЗМЕНИЛОСЬ с
        прошлого раза.
        """
        # Резервная копия базы — здесь же, потому что это единственный
        # регулярный пульс в боте, а заводить ради одной копии в сутки
        # отдельный планировщик не за чем. Проверка «копия за сегодня уже
        # есть» стоит одного обращения к файловой системе, поэтому вызов
        # раз в пять минут ничего не стоит. Ошибки внутри не поднимаются
        # наружу: страховка не имеет права ронять основную работу.
        await asyncio.to_thread(backup.backup_if_due, storage.DB_PATH)

        # Проверки лезут к adb и по сети (секунды блокировки), поэтому в
        # отдельном потоке — иначе на это время замирает весь бот.
        checks = await asyncio.to_thread(diagnostics.run_all_checks)

        broke = []   # только что сломалось
        fixed = []   # только что починилось

        for check in checks:
            was_ok = self._previous_state.get(check.name)
            self._previous_state[check.name] = check.ok

            if was_ok is None:
                # Первый обход: состояние просто запоминаем. Оповещать
                # сейчас не о чем — то, что было сломано ещё до запуска,
                # уже показал preflight при старте.
                continue
            if was_ok and not check.ok:
                broke.append(check)
            elif not was_ok and check.ok:
                fixed.append(check)

        for check in broke:
            logger.error("МОНИТОРИНГ: сломалось — %s: %s", check.name, check.detail)
            await self.notify(
                f"🔴 Сломалось: {check.name}\n"
                f"{check.detail}\n\n"
                f"Что делать:\n{check.fix}"
            )

        for check in fixed:
            logger.info("МОНИТОРИНГ: снова работает — %s", check.name)
            await self.notify(f"🟢 Снова работает: {check.name}\n{check.detail}")

        # Отдельно — дневной лимит Carmoodo. Это не поломка оборудования,
        # а исчерпание ресурса, но узнать о нём заранее не менее важно:
        # без VIN отваливается ещё и PARTSNUMBER, то есть две трети
        # ответа.
        if usage.should_warn():
            logger.warning("МОНИТОРИНГ: %s", usage.status_line())
            await self.notify(
                f"⚠️ {usage.status_line()}\n\n"
                "Когда лимит закончится, VIN получить не выйдет, а без него "
                "не будет и раздела с деталями. Лимит обнулится в полночь."
            )

        await self._send_daily_summary()

    async def _send_daily_summary(self) -> None:
        """
        Сводка за день — один раз в сутки, вечером.

        ЗАЧЕМ. Всё остальное в этом модуле следит за ЖЕЛЕЗОМ и сообщает
        только о поломках. Сводка показывает, как работает само дело —
        какая доля проверок даёт полный ответ, чего чаще всего не
        хватает, — чтобы владелец узнавал о проблемах не только тогда,
        когда они случаются у него на глазах.

        Вечер, а не утро: к этому часу день уже отработан, и сводка
        описывает его целиком, а не половину.
        """
        segodnya = time.strftime("%Y-%m-%d")
        if self._summary_sent_for == segodnya:
            return
        if time.localtime().tm_hour < DAILY_SUMMARY_HOUR:
            return

        # Отметка живёт в БАЗЕ, а не только в памяти: при перезапуске бота
        # после 21:00 память обнуляется, и сводка ушла бы второй раз.
        # Watchdog перезапускает бота и сам, после любого падения, так что
        # без этой отметки дубли — вопрос времени.
        if await asyncio.to_thread(stats.summary_already_sent, segodnya):
            self._summary_sent_for = segodnya
            return

        self._summary_sent_for = segodnya
        await asyncio.to_thread(stats.mark_summary_sent, segodnya)
        text = await asyncio.to_thread(stats.summary_for)
        logger.info("МОНИТОРИНГ: %s", text.replace("\n", " | "))
        await self.notify(text)


    # -- цикл -------------------------------------------------------------

    async def run_forever(self) -> None:
        await asyncio.sleep(FIRST_CHECK_DELAY_SEC)
        logger.info(
            "Фоновый мониторинг запущен: проверка каждые %.0f сек, оповещения %s.",
            CHECK_INTERVAL_SEC,
            f"в чат {self.admin_chat_id}" if self.admin_chat_id else "отключены (нет ADMIN_CHAT_ID)",
        )
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Фоновая задача не должна умирать НИКОГДА: если она тихо
                # свалится, мониторинг просто исчезнет, и никто этого не
                # заметит — то есть мы вернёмся ровно к тому состоянию,
                # ради ухода от которого модуль и написан.
                logger.exception("Ошибка в цикле мониторинга — продолжаю работу.")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


_monitor: Optional[Monitor] = None


def start(bot) -> Optional[Monitor]:
    """
    Запускает мониторинг. Вызывается из bot.py после старта приложения.

    Без ADMIN_CHAT_ID мониторинг всё равно работает — просто пишет в лог,
    не отправляя сообщений. Это осознанно: проверки полезны сами по себе,
    а требовать настройку ради запуска было бы лишним препятствием.
    """
    global _monitor
    if not ADMIN_CHAT_ID:
        logger.info(
            "ADMIN_CHAT_ID не задан в .env — оповещения о поломках в Telegram "
            "отключены (проверки продолжат писаться в лог). Чтобы включить: "
            "узнай свой chat_id у бота @userinfobot и добавь строку "
            "ADMIN_CHAT_ID=... в .env."
        )
    _monitor = Monitor(bot, ADMIN_CHAT_ID)
    _monitor.start()
    return _monitor


async def stop() -> None:
    if _monitor is not None:
        await _monitor.stop()


async def notify_admin(text: str) -> None:
    """Разовое оповещение владельца из другого места программы."""
    if _monitor is not None:
        await _monitor.notify(text)
