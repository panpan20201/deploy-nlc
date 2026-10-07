"""Сессия, упавшая при создании, не должна оставлять за собой браузер.

ЗАЧЕМ. Без этой уборки под bot.py накапливались лишние деревья
chrome-headless-shell (три вместо одного, ~1,1 ГБ). Лог такого сбоя:

    21:35:31 PartsNumberSessionError: ... Page.goto: Timeout 120000ms exceeded.
    21:35:31 Выбрасываю поток PARTSNUMBER — следующая сессия начнётся в свежем.

PartsNumberSession.__enter__ успел запустить Playwright и браузер, затем
упал на входе. Сессия так и не попала в globals(), поэтому _close_session
закрывать было нечего — он только выбросил поток. Браузер остался жить до
перезапуска бота. Каждый сбой входа = ещё один осиротевший браузер.

То же касается Appium-сессий: драйвер создан, приложение не поднялось —
сессия на сервере Appium висит, пока он сам её не оборвёт.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


class FailingSession:
    def __init__(self):
        self.zakryto = 0

    def __enter__(self):
        raise RuntimeError("Page.goto: Timeout 120000ms exceeded.")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.zakryto += 1
        return False


class ExitAlsoFails(FailingSession):
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.zakryto += 1
        raise RuntimeError("browser has been closed")


class FailedCreateCleanupTest(unittest.TestCase):
    def _create(self, session):
        factories = {"_partsnumber_session": lambda: session}
        with mock.patch.dict(pipeline._SESSION_FACTORIES, factories):
            return pipeline._create_session_blocking("_partsnumber_session")

    def test_upavshiy_vhod_zakryvaet_brauzer(self):
        session = FailingSession()
        with self.assertRaises(RuntimeError):
            self._create(session)
        self.assertEqual(session.zakryto, 1)

    def test_naruzhu_idet_ishodnaya_oshibka_a_ne_oshibka_zakrytiya(self):
        session = ExitAlsoFails()
        with self.assertRaisesRegex(RuntimeError, "Page.goto"):
            self._create(session)
        self.assertEqual(session.zakryto, 1)


if __name__ == "__main__":
    unittest.main()
