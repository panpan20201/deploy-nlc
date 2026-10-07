"""Каталог, ответивший таблицей из нескольких строк, надо называть верно.

РЕАЛЬНЫЙ СЛУЧАЙ (метка 9f5548, VIN WDBEA22D1RC131656, Mercedes W124).
Бот дошёл до каталога,
выбрал группу Mercedes и запустил поиск. Каталог машину НАШЁЛ и выдал две
записи с ОДИНАКОВЫМ VIN — разные разделы каталога на одну машину, а не
разные машины:

    WDBEA22D1RC131656 | 15Y AB 09/89 for JP     | PERSONENWAGEN LIMOUSINE
    WDBEA22D1RC131656 | 44Q 200-320 AB 10/92... | ЛЕГКОВ.А/М СЕДАН

Иконки ⓘ в таком виде нет, и без отдельного распознавания шаг падает с сообщением «страница так и не
дошла до состояния, где видна иконка ⓘ. Скорее всего, каталог грузился
дольше обычного» — то есть с НЕВЕРНОЙ причиной. Человек по такому
сообщению идёт проверять сеть, с которой всё в порядке.

Выбрать нужную строку автоматически нечем: у Horizon нет структуры
страницы, только картинка, а привязываться к координатам строк нельзя —
высота строки зависит от длины описания в колонке note. Поэтому случай
распознаётся и называется своим именем.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug_screenshots"
)

import partsnumber_date as pn


TABLE_SHOT = "20260914_172806_search_timeout.png"     # каталог вернул 2 строки
GROUP_SHOT = "20260914_173320_search_timeout.png"     # экран выбора группы FCA


def shot(name):
    with open(os.path.join(SHOTS, name), "rb") as f:
        return f.read()


class MultirowTemplateTest(unittest.TestCase):
    def setUp(self):
        for name in (TABLE_SHOT, GROUP_SHOT):
            if not os.path.exists(os.path.join(SHOTS, name)):
                self.skipTest(f"скриншот {name} убран штатной чисткой")

    def test_etalon_shapki_na_meste(self):
        self.assertTrue(os.path.exists(pn.VIN_RESULT_TABLE_TEMPLATE))

    def test_nahoditsya_na_ekrane_s_tablitsey(self):
        self.assertIsNotNone(
            pn.find_icon_on_screenshot(shot(TABLE_SHOT),
                                       [pn.VIN_RESULT_TABLE_TEMPLATE])
        )

    def test_ne_nahoditsya_na_ekrane_vybora_gruppy(self):
        # Если бы находился — бот называл бы «несколько записей» экран, где
        # на самом деле надо выбрать марку, и правка FCA осталась бы
        # незамеченной.
        self.assertIsNone(
            pn.find_icon_on_screenshot(shot(GROUP_SHOT),
                                       [pn.VIN_RESULT_TABLE_TEMPLATE])
        )


if __name__ == "__main__":
    unittest.main()
