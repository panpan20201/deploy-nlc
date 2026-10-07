"""Страховка от зависания Appium: она должна РЕАЛЬНО вставать.

Предупреждение "_client_config" в логе означает, что защита от
зависания не встала. Тест проверяет, что она действительно ставится.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from selenium.webdriver.remote.remote_connection import RemoteConnection

import heydealer_history


class FakeClientConfig:
    def __init__(self, timeout):
        self.timeout = timeout


class FakeCommandExecutor:
    def __init__(self, client_config):
        self._client_config = client_config


class FakeDriver:
    """driver ровно в той части, которая нас здесь интересует."""

    def __init__(self, timeout):
        self.command_executor = FakeCommandExecutor(FakeClientConfig(timeout))


class ApplyAppiumCommandTimeoutTest(unittest.TestCase):
    def test_ustanavlivaet_i_podtverzhdaet_taimaut(self):
        # Соединение существует — так же, как в момент вызова после
        # создания драйвера. Сетевых обращений конструктор не делает.
        RemoteConnection("http://127.0.0.1:4723")

        self.assertTrue(heydealer_history.apply_appium_command_timeout(120))
        self.assertEqual(RemoteConnection.get_timeout(), 120)

    def test_vozvrashaet_false_esli_podtverdit_ne_udalos(self):
        # Успех считается по проверке результата, а не по
        # отсутствию исключения. Если чтение вернуло не то, что ставили,
        # это неудача, а не успех.
        RemoteConnection("http://127.0.0.1:4723")
        original = RemoteConnection.set_timeout
        try:
            RemoteConnection.set_timeout = classmethod(lambda cls, t: None)
            self.assertFalse(heydealer_history.apply_appium_command_timeout(77))
        finally:
            RemoteConnection.set_timeout = original

    def test_sveryaet_soedinenie_imenno_etogo_drayvera(self):
        # Проверять надо ТОТ объект, который
        # защищаем. RemoteConnection.get_timeout() читает классовый
        # атрибут — сегодня это то же самое соединение, но завтра
        # достаточно второй сессии, чтобы проверка стала врать.
        RemoteConnection("http://127.0.0.1:4723")
        driver = FakeDriver(timeout=1)
        self.assertFalse(
            heydealer_history.apply_appium_command_timeout(120, driver=driver))

        driver_s_taimautom = FakeDriver(timeout=120)
        self.assertTrue(
            heydealer_history.apply_appium_command_timeout(
                120, driver=driver_s_taimautom))

    def test_otsutstvie_atributa_ne_ronyaet_bota(self):
        # Приватный атрибут может исчезнуть в новой версии selenium.
        # Тогда мы обязаны работать без страховки и с предупреждением,
        # а не падать при старте сессии.
        RemoteConnection("http://127.0.0.1:4723")

        class DriverBezConfiga:
            command_executor = object()

        self.assertFalse(heydealer_history.apply_appium_command_timeout(
            120, driver=DriverBezConfiga()))


if __name__ == "__main__":
    unittest.main()
