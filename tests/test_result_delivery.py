"""Готовый результат не теряется из-за минутного обрыва связи.

ЗАЧЕМ. Реальный случай, пробив 237고2098: всё нашлось, но в эту минуту
пропал интернет на сервере. Отправка новым сообщением упала с
httpx.ConnectTimeout, запасная правка — с Timed out. Если сдаваться после
одной попытки каждого способа, человек остаётся без результата, а обрывы
на сервере длятся минуты (getaddrinfo failed).

ПОЧЕМУ НЕ ПРОСТО «ПОВТОРЯТЬ, ПОКА НЕ ВЫЙДЕТ». Повтор не должен давать
двойной ответ. Таймаут на ЧТЕНИИ ответа не значит, что сообщение не
дошло, — повтор отправки в этом случае даст дубль. Поэтому новое
сообщение повторяется только когда запрос ТОЧНО не ушёл (нет соединения,
DNS, 502), а при неясном исходе бот переходит на правку исходного
сообщения: правка того же текста дубля не создаёт, её можно повторять.
"""
import asyncio
import os
import sys
import unittest

import httpx
from telegram.error import NetworkError, RetryAfter, TimedOut

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import handlers


def _raised(exc, cause=None):
    """Исключение в том виде, в каком его бросает python-telegram-bot."""
    try:
        if cause is not None:
            raise exc from cause
        raise exc
    except Exception as e:
        return e


def connect_error():
    return _raised(NetworkError("httpx.ConnectError: [Errno 11001] getaddrinfo failed"),
                   httpx.ConnectError("getaddrinfo failed"))


def connect_timeout():
    return _raised(TimedOut(), httpx.ConnectTimeout(""))


def read_timeout():
    return _raised(TimedOut(), httpx.ReadTimeout(""))


class Script:
    """Отправка/правка, которые падают заданное число раз, потом проходят."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


class Render:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.results.pop(0) if self.results else True


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.pauses = []

    async def _sleep(self, sec):
        self.pauses.append(sec)

    def deliver(self, send, render, delays=(5, 10, 20)):
        return asyncio.run(handlers._deliver_final(send, render, delays=delays, sleep=self._sleep))

    def test_obryv_svyazi_novoe_soobshchenie_povtoryaetsya(self):
        send = Script([connect_error(), connect_timeout()])
        render = Render([])
        self.assertEqual(self.deliver(send, render), "new")
        self.assertEqual(send.calls, 3)
        self.assertEqual(render.calls, 0)
        self.assertEqual(self.pauses, [5, 10])

    def test_bad_gateway_tozhe_povtoryaetsya(self):
        send = Script([_raised(NetworkError("Bad Gateway"))])
        self.assertEqual(self.deliver(send, Render([])), "new")
        self.assertEqual(send.calls, 2)

    def test_neyasnyy_ishod_ne_shlet_dubl_a_pravit(self):
        # Ответ не прочитан — сообщение могло дойти. Второй раз не шлём.
        send = Script([read_timeout()])
        render = Render([True])
        self.assertEqual(self.deliver(send, render), "edit")
        self.assertEqual(send.calls, 1)
        self.assertEqual(render.calls, 1)

    def test_pravka_tozhe_povtoryaetsya(self):
        send = Script([read_timeout()])
        render = Render([False, False, True])
        self.assertEqual(self.deliver(send, render), "edit")
        self.assertEqual(render.calls, 3)

    def test_vse_popytki_ischerpany(self):
        send = Script([connect_error()] * 4)
        render = Render([False] * 4)
        self.assertEqual(self.deliver(send, render), "failed")
        self.assertEqual(send.calls, 4)
        self.assertEqual(render.calls, 4)

    def test_flood_control_zhdet_skolko_skazal_telegram(self):
        send = Script([_raised(RetryAfter(17))])
        self.assertEqual(self.deliver(send, Render([])), "new")
        self.assertEqual(self.pauses, [17])

    def test_chuzhaya_oshibka_ne_povtoryaetsya(self):
        # Например, HTML-разметка не понравилась Telegram: повтор не поможет.
        send = Script([_raised(ValueError("Can't parse entities"))])
        render = Render([True])
        self.assertEqual(self.deliver(send, render), "edit")
        self.assertEqual(send.calls, 1)


if __name__ == "__main__":
    unittest.main()
