# -*- coding: utf-8 -*-
"""
Форма входа Carmoodo, которую Appium не видит (метка 893baf).

Обе страницы — настоящие деревья элементов с планшета:
  blind — дамп бота в момент сбоя (контейнер login-form пуст);
  ok    — то же приложение после перезапуска (2 поля и «로그인»).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carmoodo_vin as cv  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class _Driver:
    def __init__(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            self.page_source = f.read()


BLIND = _Driver("carmoodo_login_form_blind_20261001.xml")
OK = _Driver("carmoodo_login_form_ok_20261001.xml")


class BlindLoginFormTest(unittest.TestCase):
    def test_blind_form_is_detected(self):
        self.assertTrue(cv.screen_is_blind_login_form(BLIND))

    def test_normal_login_form_is_not_blind(self):
        # Нормальную форму лечит обычный автовход, перезапуск ей не нужен.
        self.assertFalse(cv.screen_is_blind_login_form(OK))

    def test_spinner_check_does_not_catch_it(self):
        # Ровно поэтому понадобилась отдельная проверка.
        self.assertFalse(cv.screen_is_only_spinner(BLIND))

    def test_driver_error_is_not_blind(self):
        class Broken:
            @property
            def page_source(self):
                raise RuntimeError("сессия умерла")
        self.assertFalse(cv.screen_is_blind_login_form(Broken()))


if __name__ == "__main__":
    unittest.main()
