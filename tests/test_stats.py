"""Сводка по результатам работы, а не по состоянию железа.

ЗАЧЕМ. monitor.py следит за планшетом, Appium, клавиатурой и буфером — то
есть за оборудованием. Без сводки за делом не следит никто: доля полных
ответов, частота отказов по каждой части, расход суточных лимитов нигде
не видны. О систематических сбоях (например, PARTSNUMBER падает на
машинах Jeep, а HeyDealer иногда не дожидается страницы) владелец узнаёт,
только когда они случаются у него на глазах, а неполные ответы не
вызывают ни одного сигнала.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stats
import storage


DAY = 86400.0


class StatsTest(unittest.TestCase):
    def setUp(self):
        self._conn = storage._conn
        self._path = storage.DB_PATH
        self.tmp = tempfile.mkdtemp()
        storage._conn = None
        storage.DB_PATH = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self._conn
        storage.DB_PATH = self._path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_polnaya_proverka_schitaetsya_polnoy(self):
        stats.record("11가1111", vin="ok", repair="ok", parts="ok", seconds=90)
        svodka = stats.summary_for()
        self.assertIn("1", svodka)
        self.assertIn("полных", svodka)

    def test_nepolnye_razbirayutsya_po_prichinam(self):
        stats.record("11가1111", vin="ok", repair="ok", parts="error", seconds=120)
        stats.record("22나2222", vin="ok", repair="error", parts="ok", seconds=150)
        svodka = stats.summary_for()
        self.assertIn("PARTSNUMBER", svodka)
        self.assertIn("HeyDealer", svodka)

    def test_pustoy_den_govorit_chto_proverok_ne_bylo(self):
        svodka = stats.summary_for()
        self.assertIn("не было", svodka.lower())

    def test_vcherashnie_proverki_ne_popadayut_v_segodnyashnyuyu_svodku(self):
        stats.record("11가1111", vin="ok", repair="ok", parts="ok",
                     seconds=90, at=time.time() - DAY)
        self.assertIn("не было", stats.summary_for().lower())

    def test_schitaet_srednee_vremya(self):
        stats.record("11가1111", vin="ok", repair="ok", parts="ok", seconds=100)
        stats.record("22나2222", vin="ok", repair="ok", parts="ok", seconds=200)
        svodka = stats.summary_for()
        self.assertIn("150", svodka)

    def test_empty_eto_otvet_a_ne_sboy(self):
        # "empty" — это ответ по существу ("истории нет"), и считать его
        # неудачей означало бы пугать владельца исправной работой.
        stats.record("11가1111", vin="ok", repair="empty", parts="ok", seconds=80)
        svodka = stats.summary_for()
        self.assertIn("полных: 1", svodka)

    def test_zapis_ne_ronyaet_proverku(self):
        # Статистика — побочная вещь. Сломанная база не имеет права
        # отменять ответ человеку.
        storage.DB_PATH = os.path.join(self.tmp, "нет", "такого", "пути.db")
        storage._conn = None
        try:
            stats.record("11가1111", vin="ok", repair="ok", parts="ok", seconds=1)
        except Exception as exc:
            self.fail(f"запись статистики уронила проверку: {exc}")


if __name__ == "__main__":
    unittest.main()


class SummarySentMarkTest(unittest.TestCase):
    """Сводка за день должна уходить один раз, даже если бота перезапускали.

    Отметка «за сегодня отправлено» в памяти монитора сбрасывалась бы при
    каждом перезапуске бота (на живой работе сводка так ушла дважды, в
    21:21 и в 21:31). В день с несколькими перезапусками владелец получал
    бы её столько же раз.
    """

    def setUp(self):
        self._conn = storage._conn
        self._path = storage.DB_PATH
        self.tmp = tempfile.mkdtemp()
        storage._conn = None
        storage.DB_PATH = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self._conn
        storage.DB_PATH = self._path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snachala_ne_otpravleno(self):
        self.assertFalse(stats.summary_already_sent("2026-09-14"))

    def test_posle_otmetki_schitaetsya_otpravlennoy(self):
        stats.mark_summary_sent("2026-09-14")
        self.assertTrue(stats.summary_already_sent("2026-09-14"))

    def test_otmetka_perezhivaet_perezapusk(self):
        # Перезапуск бота = новое соединение с той же базой.
        stats.mark_summary_sent("2026-09-14")
        storage._conn.close()
        storage._conn = None
        self.assertTrue(stats.summary_already_sent("2026-09-14"))

    def test_drugoy_den_otpravlyaetsya_zanovo(self):
        stats.mark_summary_sent("2026-09-14")
        self.assertFalse(stats.summary_already_sent("2026-09-15"))

    def test_povtornaya_otmetka_ne_padaet(self):
        stats.mark_summary_sent("2026-09-14")
        stats.mark_summary_sent("2026-09-14")
        self.assertTrue(stats.summary_already_sent("2026-09-14"))
