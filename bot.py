# -*- coding: utf-8 -*-
"""
bot.py
======

Точка входа. Порядок действий при старте:

  1. Настроить логирование — САМЫМ ПЕРВЫМ делом, до всего остального.
  2. Проверить настройки .env и сразу сказать, чего не хватает.
  3. Создать приложение Telegram и зарегистрировать обработчики.
  4. Запустить опрос серверов Telegram (polling).

ПОЧЕМУ ЛОГИРОВАНИЕ НАСТРАИВАЕТСЯ ДО ИМПОРТА ОСТАЛЬНЫХ МОДУЛЕЙ
--------------------------------------------------------------
Некоторые модули пишут в лог уже на этапе импорта (например, pipeline.py
сообщает, сколько записей загрузил из кэша, а config.py — чего не хватает
в .env). Если настроить логирование позже, эти самые первые и часто самые
важные строки уйдут в никуда — а именно они объясняют, почему бот потом
ведёт себя не так, как ожидалось. Отсюда и импорты не в начале файла:
это сознательное отступление от обычного стиля, а не небрежность.
"""

import logging

import nlc_logging

# Логирование — до всего остального (см. пояснение в шапке файла).
LOG_PATH = nlc_logging.setup_logging(level=logging.INFO)

from telegram.ext import Application, CommandHandler, MessageHandler, filters  # noqa: E402

import config  # noqa: E402
import diagnostics  # noqa: E402
import monitor  # noqa: E402
import pipeline  # noqa: E402
import storage  # noqa: E402
from handlers import (  # noqa: E402
    start_command,
    diag_command,
    resetads_command,
    help_command,
    status_command,
    handle_text_message,
    error_handler,
)

logger = logging.getLogger(__name__)


async def _on_startup(application: Application) -> None:
    """
    Вызывается после запуска приложения Telegram.

    Здесь стартует фоновый присмотр за оборудованием (monitor.py). Именно
    здесь, а не в main(): для создания фоновой задачи нужен уже
    работающий цикл событий, а в main() его ещё нет.
    """
    monitor.start(application.bot)


async def _on_shutdown(application: Application) -> None:
    """
    Вызывается библиотекой python-telegram-bot при остановке бота, перед
    самым выходом. Аккуратно закрывает Appium- и Horizon-сессии, если они
    были открыты, и сохраняет кэш на диск.
    """
    logger.info("Остановка бота: закрываю сессии и сохраняю кэш...")
    await monitor.stop()
    await pipeline.shutdown()


def main() -> None:
    logger.info("=" * 70)
    logger.info("Запуск бота NLC. Лог пишется в: %s", LOG_PATH)
    logger.info("=" * 70)

    # База пробитых машин: открыть и, если остался старый JSON-кэш,
    # перенести его записи. Каждая перенесённая машина — это сохранённый
    # запрос из дневного лимита.
    storage.init()

    # Проверка оборудования при старте: планшет, Appium, приложения,
    # клавиатура, эталоны картинок. Бот запускается в любом случае — но
    # если что-то не так, об этом будет написано прямо сейчас и с
    # указанием, что делать, а не через час в виде непонятного сбоя у
    # живого клиента. Настройки .env проверяются внутри (забытая строка
    # в .env всплывает здесь же), поэтому отдельный вызов
    # config.check_settings() не нужен.
    diagnostics.run_preflight()

    # Уборка старых дампов экранов и скриншотов: они копятся с каждым
    # разобранным сбоем и за месяцы способны занять всю папку.
    diagnostics.cleanup_old_debug_files()

    # .concurrent_updates(True) — критично для нашего сценария: без него
    # библиотека обрабатывает сообщения строго по одному, и пока для
    # одного человека идёт проверка (минуту-две), бот не может даже
    # ПРИНЯТЬ сообщение от другого. С этой настройкой приём сообщений
    # идёт параллельно, а доступ к планшету всё равно остаётся строго
    # последовательным — за это отвечает лок внутри pipeline.py. Ровно то
    # разделение, которое нужно: "принимать можно параллельно, трогать
    # устройство — только по одному".
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("diag", diag_command))
    application.add_handler(CommandHandler("resetads", resetads_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )
    application.add_error_handler(error_handler)

    logger.info("Бот запущен и готов принимать сообщения (режим polling).")
    application.run_polling()


if __name__ == "__main__":
    main()
