"""Определение марки по началу VIN для выбора группы в каталоге PARTSNUMBER.

РЕАЛЬНЫЙ СЛУЧАЙ (метки a232d0 и 9203e6, номер 경기63나4085,
VIN WDBEA22D1RC131656). Каталог показал экран выбора группы марки,
`_select_catalog_group` не нашла префикс "WDB" в VIN_WMI_TO_GROUP и —
совершенно правильно — отказалась нажимать наугад:

    На экране, похоже, выбор группы каталога, но марку по началу VIN (WDB)
    определить не могу. Нажимать наугад не буду: откроется каталог чужой
    марки. Добавь 'WDB' в VIN_WMI_TO_GROUP.

Шаг падал по таймауту ожидания результата поиска VIN, проверка повторялась
дважды и оба раза впустую.

WDB — это Mercedes-Benz, легковые классических серий (здесь W124, E-класс
середины 90-х). В таблице уже были WDD, W1K и W1N — тоже Mercedes, просто
более поздние WMI.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import partsnumber_date as pn


# VIN из реального лога — тот самый, на котором шаг упал дважды.
VIN_IZ_LOGA = "WDBEA22D1RC131656"


class VinWmiToGroupTest(unittest.TestCase):
    def test_wdb_eto_mercedes(self):
        self.assertEqual(pn.VIN_WMI_TO_GROUP.get("WDB"), "mercedes")

    def test_vin_iz_loga_privodit_k_gruppe_mercedes(self):
        # Ровно тот путь, которым идёт _select_catalog_group: первые три
        # символа VIN -> группа каталога.
        wmi = VIN_IZ_LOGA[:3].upper()
        self.assertEqual(pn.VIN_WMI_TO_GROUP.get(wmi), "mercedes")

    def test_u_gruppy_mercedes_est_etalon_nadpisi(self):
        # Определить группу мало: без файла-эталона надписи функция тоже
        # честно сдаётся, и машина по-прежнему не пробьётся. Проверяем,
        # что для этой машины готов весь путь, а не половина.
        group = pn.VIN_WMI_TO_GROUP.get(VIN_IZ_LOGA[:3].upper())
        template = pn.CATALOG_GROUP_TEMPLATES.get(group)
        self.assertIsNotNone(template, f"нет эталона для группы {group!r}")
        self.assertTrue(os.path.exists(template), f"файл эталона не найден: {template}")

    def test_prezhnie_prefiksy_mercedes_na_meste(self):
        # Защита от правки, которая добавит WDB и заодно снесёт соседей.
        for wmi in ("WDD", "W1K", "W1N"):
            self.assertEqual(pn.VIN_WMI_TO_GROUP.get(wmi), "mercedes", wmi)

    def test_jeep_otnositsya_k_gruppe_fca(self):
        # РЕАЛЬНЫЙ СЛУЧАЙ (метка bf15f7, номер 20수4034,
        # VIN 1C4RJFCM5JC170456). Каталог показал экран выбора группы с
        # вариантами FCA и Fiat — скриншот
        # debug_screenshots/20260914_173320_search_timeout.png. Префикса
        # 1C4 в таблице не было, и шаг упал по таймауту ожидания иконки.
        self.assertEqual(pn.VIN_WMI_TO_GROUP.get("1C4"), "fca")

    def test_u_gruppy_fca_est_etalon_nadpisi(self):
        template = pn.CATALOG_GROUP_TEMPLATES.get("fca")
        self.assertIsNotNone(template, "без эталона группа не заработает")
        self.assertTrue(os.path.exists(template), template)

    def test_fiat_ne_putaetsya_s_fca(self):
        # На экране это ДВЕ РАЗНЫЕ группы. Отправить Fiat в FCA означало бы
        # открыть каталог не той марки — то есть ровно та ошибка, ради
        # которой _select_catalog_group отказывается угадывать.
        self.assertNotEqual(pn.VIN_WMI_TO_GROUP.get("ZFA"), "fca")

    def test_renault_korea_nazhimaet_renault_a_ne_nissan(self):
        # РЕАЛЬНЫЙ СЛУЧАЙ (метки 75bda2 и f47f25, VIN
        # KNME5C2B0NP087095). Каталог предложил «Nissan / Renault», префикса
        # KNM в таблице не было — две проверки упали по таймауту. Проверяем
        # весь путь на том самом экране: группа, эталон и точка клика.
        group = pn.VIN_WMI_TO_GROUP.get("KNME5C2B0NP087095"[:3])
        self.assertEqual(group, "renault")
        template = pn.CATALOG_GROUP_TEMPLATES.get(group)
        self.assertTrue(template and os.path.exists(template), template)

        screen = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tests", "fixtures", "evidence",
            "20260924_120351_knm_nissan_renault.png",
        )
        with open(screen, "rb") as f:
            coords = pn.find_icon_on_screenshot(f.read(), [template], log_details=False)
        self.assertIsNotNone(coords)
        x, y = coords
        # Слово Renault на экране: x 853..912, y 169..182. Nissan — левее 770.
        self.assertTrue(845 <= x <= 920 and 160 <= y <= 192, coords)

    def test_neizvestny_prefiks_ne_ugadyvaetsya(self):
        # Главная защита проекта: нет в таблице — значит None, а не
        # "похожая" марка. Ошибка здесь дороже отказа.
        self.assertIsNone(pn.VIN_WMI_TO_GROUP.get("ZZZ"))


if __name__ == "__main__":
    unittest.main()
