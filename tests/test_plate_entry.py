"""Ввод номера в HeyDealer должен подтверждаться, а не считаться удачным.

РЕАЛЬНЫЙ СЛУЧАЙ (метка bf15f7, попытка 1, номер 20수4034).
Шаг 4 отрапортовал «Ввод номера через ADB_INPUT_TEXT broadcast», шаг 5
прождал 33 секунды и сохранил дамп. В дампе номера НЕТ НИГДЕ: broadcast
прошёл без ошибки, а текст в поле не появился.

Код рядом с этим местом сам предупреждает о причине:

    time.sleep(0.35)  # даём системной клавиатуре открыться.
    # Единственная пауза, которую нельзя просто убрать: дальше нет явного
    # ожидания появления клавиатуры/фокуса, а ADB_INPUT_TEXT не сработает
    # без него. Если начнёт изредка не срабатывать ввод — увеличивай
    # именно это число.

То есть успех ввода считался по отсутствию ошибки, а считать его надо
только по проверке результата. Проверка результата стоит одного чтения экрана, а её
отсутствие стоило двух попыток, минуты времени и одной проверки из
суточных десяти.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heydealer_history as hh


PLATE = "20수4034"


class PlateVisibleTest(unittest.TestCase):
    def test_nomer_na_ekrane_viden(self):
        page = f'<node text="{PLATE}"/>'
        self.assertTrue(hh.plate_is_on_screen(page, PLATE))

    def test_nomera_net_znachit_ne_viden(self):
        page = '<node text="구매할 중고차"/><node text="경기63나4085"/>'
        self.assertFalse(hh.plate_is_on_screen(page, PLATE))

    def test_nevidimye_simvoly_ne_meshayut(self):
        # Приложение разделяет символы номера ​ и \xa0 — именно
        # так номер и выглядит на экране результата (дамп 03.09: "133",
        # "다", "4272" отдельными узлами).
        razorvannyy = '<node text="20​수​4034"/>'
        self.assertTrue(hh.plate_is_on_screen(razorvannyy, PLATE))

    def test_pustoy_ekran_i_pustoy_nomer(self):
        self.assertFalse(hh.plate_is_on_screen("", PLATE))
        self.assertFalse(hh.plate_is_on_screen(None, PLATE))
        self.assertFalse(hh.plate_is_on_screen("<node/>", ""))


class FakeInputDriver:
    """driver, у которого ввод срабатывает только с N-го раза."""

    def __init__(self, srabotaet_s_popytki):
        self.srabotaet_s = srabotaet_s_popytki
        self.naborov = 0
        self.plate = PLATE

    def zapisat_nabor(self):
        self.naborov += 1

    @property
    def page_source(self):
        if self.naborov >= self.srabotaet_s:
            return f'<node text="{self.plate}"/>'
        return '<node text="구매할 중고차"/>'

    def find_elements(self, *a, **k):
        return []


class EnterPlateTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(hh, "PLATE_ENTRY_SETTLE_SEC", 0)
        p.start()
        self.addCleanup(p.stop)

    def run_entry(self, driver):
        def nabor(text):
            driver.zapisat_nabor()

        with mock.patch.multiple(
            hh,
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(side_effect=nabor),
        ):
            return hh.enter_plate_confirmed(driver, PLATE)

    def test_s_pervogo_raza_bez_povtorov(self):
        driver = FakeInputDriver(srabotaet_s_popytki=1)
        self.assertTrue(self.run_entry(driver))
        self.assertEqual(driver.naborov, 1, "лишний набор не нужен")

    def test_ne_popalo_s_pervogo_raza_perenabiraem(self):
        driver = FakeInputDriver(srabotaet_s_popytki=2)
        self.assertTrue(self.run_entry(driver))
        self.assertEqual(driver.naborov, 2)

    def test_sovsem_ne_popadaet_chestno_sdayomsya(self):
        driver = FakeInputDriver(srabotaet_s_popytki=99)
        self.assertFalse(self.run_entry(driver))
        self.assertEqual(driver.naborov, hh.PLATE_ENTRY_ATTEMPTS)

    def test_nechitaemy_ekran_ne_schitaetsya_uspehom(self):
        class Broken(FakeInputDriver):
            @property
            def page_source(self):
                raise RuntimeError("экран не читается")

        driver = Broken(srabotaet_s_popytki=1)
        self.assertFalse(self.run_entry(driver))


if __name__ == "__main__":
    unittest.main()
