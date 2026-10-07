"""Переиспользование сессии планшета между запросами.

ЗАЧЕМ. Замер по логам: шаг 1 (создание сессии Appium и запуск приложения)
— 13 секунд медианы, шаг 2 (навигация) — 4 секунды, и платится это на
КАЖДУЮ попытку. Вместе это 35% времени шага HeyDealer, потраченных на то,
чтобы заново поднять то же самое на том же устройстве.

ПОЧЕМУ ЭТО ОПАСНО И ЧТО ЗДЕСЬ ЗАЩИЩЕНО. Два подводных камня:

  1. Если ссылка на сессию сбрасывается только на пути успеха, то после
     сбоя следующий запрос переиспользует МЁРТВУЮ сессию, и дальше
     InvalidSessionIdException по кругу.
  2. Две сессии на одном udid невозможны: создание второй МОЛЧА
     обрывает первую.

Поэтому: живость проверяется настоящей командой к устройству, а не
наличием объекта; при создании сессии для одного приложения сессия
другого закрывается принудительно.

И отдельно: подготовка приложения (prepare_app) при переиспользовании НЕ
пропускается. Она закрывает оставшийся диалог, пережидает чужую проверку
и возвращает на нужную вкладку. Без неё ввод номера начался бы на экране
предыдущей машины.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


class FakeDriver:
    def __init__(self, zhivoy=True):
        self.zhivoy = zhivoy
        self.oprosov = 0

    @property
    def current_package(self):
        self.oprosov += 1
        if not self.zhivoy:
            raise RuntimeError("InvalidSessionIdException")
        return "kr.perfectree.heydealer"


class FakeSession:
    def __init__(self, zhivoy=True):
        self.driver = FakeDriver(zhivoy)
        self.podgotovok = 0

    def prepare_app(self):
        self.podgotovok += 1


class SessionAliveTest(unittest.TestCase):
    def test_zhivaya_sessiya_otvechaet(self):
        session = FakeSession(zhivoy=True)
        self.assertTrue(pipeline._session_alive(session))
        self.assertEqual(session.driver.oprosov, 1,
                         "живость проверяется настоящей командой, а не полем")

    def test_myortvaya_sessiya_ne_schitaetsya_zhivoy(self):
        # Объект на месте, а сессии уже нет. Без проверки живости это дало
        # бы InvalidSessionIdException по кругу на каждом следующем запросе.
        self.assertFalse(pipeline._session_alive(FakeSession(zhivoy=False)))

    def test_sessiya_bez_drayvera_ne_zhivaya(self):
        class Bez:
            driver = None
        self.assertFalse(pipeline._session_alive(Bez()))
        self.assertFalse(pipeline._session_alive(None))


class OtherDeviceSessionTest(unittest.TestCase):
    """На устройстве может жить только одна сессия."""

    def test_u_heydealer_sosed_eto_carmoodo(self):
        self.assertEqual(
            pipeline._other_device_session("_heydealer_session"),
            "_carmoodo_session",
        )

    def test_u_carmoodo_sosed_eto_heydealer(self):
        self.assertEqual(
            pipeline._other_device_session("_carmoodo_session"),
            "_heydealer_session",
        )

    def test_u_partsnumber_soseda_net(self):
        # PARTSNUMBER живёт в браузере и планшета не касается: закрывать
        # ради него сессию устройства было бы потерей времени на ровном
        # месте.
        self.assertIsNone(pipeline._other_device_session("_partsnumber_session"))


if __name__ == "__main__":
    unittest.main()
