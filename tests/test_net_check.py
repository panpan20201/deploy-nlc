"""Отсутствие связи должно обнаруживаться за доли секунды, а не за минуты.

РЕАЛЬНЫЙ СЛУЧАЙ, метка 8af520. На момент проверки пропал
интернет. История ремонта и VIN взялись мгновенно (кэш и Encar), а дальше:

    21:22:18  Шаг PARTSNUMBER: попытка 1/2
    21:24:29  попытка 1 не удалась — «Логотип PARTSNUMBER не появился
              за 60.0с после входа в Horizon-сессию»
    21:24:32  попытка 2/2

То есть 131 секунда на первую попытку и столько же на вторую. Система не
зависла — она честно отработала свои таймауты, — но чтобы УЗНАТЬ об
отсутствии связи, ей понадобилось больше четырёх минут. Для человека,
который ждёт ответа, это неотличимо от зависания.

При этом факт «до каталога не достучаться» устанавливается попыткой
соединения за доли секунды.

ПОЧЕМУ ПРОВЕРКА АСИММЕТРИЧНА. Успешное соединение НЕ обещает, что шаг
пройдёт: бывало, что TCP до того же хоста открывался, а страница грузилась
со скоростью 18 КБ/с и всё равно не успевала. Поэтому «связь есть» ничего
не гарантирует и ход работы не меняет, а вот «связи нет» — надёжный
признак того, что тратить две минуты незачем.
"""
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import net_check


class HostReachableTest(unittest.TestCase):
    def test_otkrytoe_soedinenie_znachit_svyaz_est(self):
        with mock.patch.object(net_check.socket, "create_connection") as conn:
            self.assertTrue(net_check.host_reachable("example.com"))
            conn.assert_called_once()

    def test_otkaz_soedineniya_znachit_svyazi_net(self):
        with mock.patch.object(net_check.socket, "create_connection",
                               side_effect=OSError("сеть недоступна")):
            self.assertFalse(net_check.host_reachable("example.com"))

    def test_imya_ne_razreshaetsya_znachit_svyazi_net(self):
        # Пропавший интернет чаще всего выглядит именно так: DNS молчит.
        with mock.patch.object(net_check.socket, "create_connection",
                               side_effect=socket.gaierror("getaddrinfo failed")):
            self.assertFalse(net_check.host_reachable("example.com"))

    def test_proverka_ne_zhdyot_dolgo(self):
        # Смысл всей затеи — узнать быстро. Таймаут по умолчанию обязан
        # быть заметно меньше таймаутов самих шагов (60 секунд у
        # PARTSNUMBER), иначе проверка сама станет тем, от чего спасает.
        self.assertLessEqual(net_check.PROBE_TIMEOUT_SEC, 5)

    def test_host_beryotsya_iz_adresa_kataloga(self):
        # Проверять надо тот хост, к которому пойдёт шаг, а не «интернет
        # вообще»: бывало, что интернет работал, а путь до каталога — нет.
        self.assertIn("partsnumber", net_check.catalog_host().lower())


if __name__ == "__main__":
    unittest.main()
