"""Отчёт должен называть, на какой момент проверены данные.

ЗАЧЕМ. По отчёту покупают машины. История ремонта хранится в базе до
REPAIR_HISTORY_TTL_DAYS дней, и замер по боевой базе показал,
что типичная запись отдаётся в возрасте 22 дней (медиана; максимум 24 из
разрешённых 30). То есть повторная проверка обычно возвращает историю
трёхнедельной давности, а человек об этом не знает ниоткуда.

Сама по себе давность не страшна: после выкладки на Encar машина не
эксплуатируется, новых ремонтов не появляется. Но государственные и
страховые системы публикуют события с задержкой, поэтому запись МОЖЕТ
дописаться уже после выкладки. Дата в отчёте закрывает этот случай и не
стоит ни одного запроса из суточной квоты.

VIN и детали датой не помечаются: VIN выбит на кузове, детали выводятся из
него — измениться не могут в принципе.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage
import pipeline


DAY = 86400.0


class StorageAgeTest(unittest.TestCase):
    """storage должен отдавать не только значение, но и его возраст."""

    def setUp(self):
        # Боевую базу не трогаем ни при каких условиях: подменяем путь и
        # кэшированное соединение, в tearDown возвращаем как было.
        self._saved_conn = storage._conn
        self._saved_path = storage.DB_PATH
        self._tmp = tempfile.mkdtemp()
        storage._conn = None
        storage.DB_PATH = os.path.join(self._tmp, "test_vehicles.db")

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self._saved_conn
        storage.DB_PATH = self._saved_path
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_otdayot_znachenie_i_vremya_polucheniya(self):
        storage.put("11가1111", "repair", [{"date": "2025년"}])
        got = storage.get_with_age("11가1111", "repair")
        self.assertIsNotNone(got)
        value, obtained_at = got
        self.assertEqual(value, [{"date": "2025년"}])
        self.assertLess(abs(time.time() - obtained_at), 5)

    def test_net_zapisi_znachit_none(self):
        self.assertIsNone(storage.get_with_age("22나2222", "repair"))

    def test_ustarevshaya_zapis_ne_otdayotsya(self):
        # Та же политика сроков, что и у обычного get: возраст выдаётся
        # ради честности отчёта, а не ради обхода срока годности.
        storage.put("33다3333", "repair", ["что-то"])
        ttl = storage.SECTION_TTL["repair"]
        with storage._lock:
            storage._connect().execute(
                "UPDATE vehicle_data SET created_at=? WHERE plate_number=?",
                (time.time() - ttl - DAY, "33다3333"),
            )
            storage._connect().commit()
        self.assertIsNone(storage.get_with_age("33다3333", "repair"))

    def test_bessrochnyy_razdel_otdayotsya_v_lyubom_vozraste(self):
        # У vin срока нет: он выбит на кузове и измениться не может.
        storage.put("44라4444", "vin", "WDBEA22D1RC131656")
        with storage._lock:
            storage._connect().execute(
                "UPDATE vehicle_data SET created_at=? WHERE plate_number=?",
                (time.time() - 400 * DAY, "44라4444"),
            )
            storage._connect().commit()
        got = storage.get_with_age("44라4444", "vin")
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "WDBEA22D1RC131656")


class FormatDataAgeTest(unittest.TestCase):
    """Как возраст данных выглядит в отчёте."""

    NOW = time.mktime((2026, 9, 14, 12, 0, 0, 0, 0, -1))

    def test_svezhie_dannye_bez_daty(self):
        # Только что полученные данные не должны шуметь цифрами.
        self.assertEqual(pipeline.format_data_age(None, self.NOW), "проверено сейчас")

    def test_segodnya(self):
        self.assertEqual(
            pipeline.format_data_age(self.NOW - 3600, self.NOW), "проверено сегодня"
        )

    def test_vchera(self):
        self.assertEqual(
            pipeline.format_data_age(self.NOW - 1 * DAY, self.NOW),
            "проверено 13.09, вчера",
        )

    def test_neskolko_dney_nazad(self):
        self.assertEqual(
            pipeline.format_data_age(self.NOW - 23 * DAY, self.NOW),
            "проверено 22.08, 23 дня назад",
        )

    def test_sklonenie_dney(self):
        # Отчёт читает человек, принимающий решение о покупке. Кривое
        # склонение в таком документе выглядит как небрежность ко всему
        # остальному.
        cases = {2: "2 дня назад", 5: "5 дней назад", 11: "11 дней назад",
                 21: "21 день назад", 22: "22 дня назад", 25: "25 дней назад"}
        for days, expected_tail in cases.items():
            got = pipeline.format_data_age(self.NOW - days * DAY, self.NOW)
            self.assertTrue(got.endswith(expected_tail),
                            f"{days} дн.: получили {got!r}, ждали хвост {expected_tail!r}")


class ReportRenderingTest(unittest.TestCase):
    """Как дата попадает в сам отчёт."""

    def test_istoriya_iz_kesha_pokazyvaet_datu(self):
        res = pipeline.StepResult(
            status="ok", value=["есть"], obtained_at=time.time() - 23 * DAY
        )
        text = pipeline._format_repair(res, repair_text="запись о ремонте")
        self.assertIn("проверено", text)
        self.assertIn("23 дня назад", text)
        self.assertIn("запись о ремонте", text)

    def test_svezhaya_istoriya_pomechena_kak_seychas(self):
        res = pipeline.StepResult(status="ok", value=["есть"], obtained_at=None)
        text = pipeline._format_repair(res, repair_text="запись о ремонте")
        self.assertIn("проверено сейчас", text)

    def test_arendnaya_mashina_poluchaet_chestnuyu_prichinu(self):
        # Реальный случай: менеджер трижды получил "техническая ошибка" на машину,
        # оформленную на прокат. Причина у сессии есть — в отчёт должна
        # попасть она, а не общая формулировка "не найдена".
        class Session:
            last_reason = "rental_company_required"
        res = pipeline._interpret_heydealer(Session(), [])
        self.assertEqual(res.status, "empty")
        self.assertEqual(res.reason, "rental_company_required")
        text = pipeline._format_repair(res)
        self.assertIn("арендной компании", text)
        self.assertIn("Повторная проверка не поможет", text)

    def test_snoski_o_bessrochnosti_v_otchyote_net(self):
        # Сноски о бессрочности VIN в отчёте нет намеренно: отчёт читают люди,
        # которые и так знают, что VIN не меняется, а лишняя строка в
        # каждом сообщении мешает. Дата у истории ремонта при этом
        # остаётся — это единственный раздел, который может измениться.
        vin = pipeline.StepResult(status="ok", value="WDBEA22D1RC131656")
        report = pipeline._assemble("11가1111", vin, None, None, None)
        self.assertNotIn("не устаревают", report)
        self.assertNotIn("ℹ️", report)


if __name__ == "__main__":
    unittest.main()
