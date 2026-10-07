"""Проверка «набрался ли VIN» не должна зависеть от координат окна.

РЕАЛЬНЫЙ СЛУЧАЙ (метка dd7a88, VIN W1K6G8CB9RA301820,
Mercedes-AMG S 63). Бот отказался от результата, которого уже достиг:
скриншот 20260914_210739_vin_field_stayed_empty.png показывает, что
машина НАЙДЕНА (в верхней панели «Mercedes-AMG S 63 E PERFORMANCE sedan»
с иконкой ⓘ, за списком открыто дерево каталога), а VIN в поле набран.

Сбой дала проверка, а не действие. Она считает тёмные пиксели в области,
заданной ЖЁСТКИМИ координатами VIN_FIELD_TEXT_REGION = (390, 36, 675, 62).
Окно Horizon между сеансами сместилось и отмасштабировалось, и эта
область попала правее текста:

    область из кода (390-675)        41 тёмный пиксель  -> «пусто»
    где текст на самом деле (262-400)  500 тёмных       -> «заполнено»

Это типичная ловушка — привязка к координатам вместо содержимого.

РЕШЕНИЕ — ОТНОСИТЕЛЬНЫЙ ЗАМЕР. Полоса берётся с запасом, чтобы поле
попало в неё при любой раскладке, и сравнивается САМА С СОБОЙ до и после
набора. Посторонние элементы внутри полосы (логотип марки, соседние
надписи) одинаковы на обоих снимках и в разнице взаимно уничтожаются.
Сдвиг и масштаб окна перестают что-либо значить.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug_screenshots"
)

import partsnumber_date as pn


SDVINUTAYA = "20260914_210739_vin_field_stayed_empty.png"   # окно сместилось
OBYCHNAYA = "20260914_172806_search_timeout.png"            # обычная раскладка


def shot(name):
    with open(os.path.join(SHOTS, name), "rb") as f:
        return f.read()


class DarkPixelsInBandTest(unittest.TestCase):
    def setUp(self):
        for name in (SDVINUTAYA, OBYCHNAYA):
            if not os.path.exists(os.path.join(SHOTS, name)):
                self.skipTest(f"скриншот {name} убран штатной чисткой")

    def test_polosa_lovit_tekst_pri_sdvinutom_okne(self):
        # Главный тест: именно здесь проверка по жёстким координатам видела 41 пиксель.
        v_polose = pn.dark_pixels_in_band(shot(SDVINUTAYA), pn.VIN_FIELD_BAND)
        self.assertGreater(
            v_polose, pn.VIN_FIELD_MIN_DELTA,
            "в поле набран VIN, полоса обязана его увидеть",
        )

    def test_polosa_lovit_tekst_pri_prezhney_raskladke(self):
        # Полоса не должна чинить один случай ценой другого.
        self.assertGreater(
            pn.dark_pixels_in_band(shot(OBYCHNAYA), pn.VIN_FIELD_BAND),
            pn.VIN_FIELD_MIN_DELTA,
        )

    def test_staraya_oblast_na_sdvinutom_okne_deystvitelno_slepa(self):
        # Фиксируем причину сбоя числом, чтобы правку нельзя было
        # «упростить» обратно к жёстким координатам, не заметив этого.
        x, y, x2, y2 = pn.VIN_FIELD_TEXT_REGION
        slepaya = pn.dark_pixels_in_band(shot(SDVINUTAYA), (x, y, x2, y2))
        self.assertLess(slepaya, pn.VIN_FIELD_FILLED_MIN_DARK)

    def test_polosa_shire_staroy_oblasti(self):
        # Смысл полосы в запасе по краям: поле должно попадать в неё и
        # тогда, когда окно съедет ещё раз.
        bx, by, bx2, by2 = pn.VIN_FIELD_BAND
        ox, oy, ox2, oy2 = pn.VIN_FIELD_TEXT_REGION
        self.assertLess(bx, ox)
        self.assertGreater(bx2, ox2)


if __name__ == "__main__":
    unittest.main()
