"""Разбор экранов HeyDealer на РЕАЛЬНЫХ дампах.

Дампы лежат в tests/fixtures/evidence/ и скопированы туда
намеренно: штатная чистка удаляет их из корня через 14 дней.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EVIDENCE = os.path.join(ROOT, "tests", "fixtures", "evidence")

import heydealer_history as hh


def dump(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as f:
        return f.read()


PROGRESS_DUMP = "heydealer_debug_step5_no_search_result_20260904_175210.xml"
OTHER_DUMP = "heydealer_debug_step5_no_search_result_20260903_200040.xml"
# Staria 106호4741: машина на прокат, HeyDealer спрашивает
# название арендной компании. Шесть одинаковых дампов за вечер.
RENTAL_DUMP = "heydealer_debug_step5_no_search_result_20260919_191420.xml"


class LookupInProgressTest(unittest.TestCase):
    def test_uznayot_ekran_iduschey_proverki(self):
        self.assertTrue(hh.looks_like_lookup_in_progress(dump(PROGRESS_DUMP)))

    def test_ne_putaet_s_drugim_ekranom(self):
        # Дамп 03.09: список машин, никакого прогресса. Если предикат
        # сработает здесь, бот будет ждать 120 секунд впустую.
        self.assertFalse(hh.looks_like_lookup_in_progress(dump(OTHER_DUMP)))

    def test_pustaya_stroka_ne_padaet(self):
        self.assertFalse(hh.looks_like_lookup_in_progress(""))
        self.assertFalse(hh.looks_like_lookup_in_progress(None))

    def test_nerazryvnye_probely_ne_meshayut(self):
        # В тексте экрана \xa0, а не обычные пробелы. Проверяем
        # оба варианта — сравнение не должно зависеть от разделителя.
        self.assertTrue(hh.looks_like_lookup_in_progress(
            "<node text='А\xa0'/>완료되면\xa0알림을\xa0보내드릴게요"))
        self.assertTrue(hh.looks_like_lookup_in_progress(
            "완료되면 알림을 보내드릴게요"))


class ReadLookupPercentTest(unittest.TestCase):
    def test_chitaet_procent_iz_dampa(self):
        self.assertEqual(hh.read_lookup_percent(dump(PROGRESS_DUMP)), 90)

    def test_bez_procenta_vozvrashaet_none(self):
        self.assertIsNone(hh.read_lookup_percent(dump(OTHER_DUMP)))
        self.assertIsNone(hh.read_lookup_percent(""))

    def test_postoronniy_procent_vyshe_po_derevu_ne_schitaetsya(self):
        # Поиск "первого числа с процентом" во всём XML на имеющемся дампе
        # даёт верные 90 — но любой рекламный "50%" выше по дереву дал бы
        # мусор молча. Процент берётся только из узла tv_percentage.
        page = ('<node text="скидка 50%" resource-id="kr.perfectree.heydealer:id/banner"/>'
                '<node text="90%" resource-id="kr.perfectree.heydealer:id/tv_percentage"/>')
        self.assertEqual(hh.read_lookup_percent(page), 90)

    def test_poryadok_atributov_ne_vazhen(self):
        # Порядок атрибутов uiautomator не гарантирует: в дампе 04.09 text
        # стоит ПЕРЕД resource-id, но полагаться на это нельзя.
        page = ('<node resource-id="kr.perfectree.heydealer:id/tv_percentage" '
                'text="42%" bounds="[53,898][98,933]"/>')
        self.assertEqual(hh.read_lookup_percent(page), 42)

    def test_bez_uzla_tv_percentage_vozvrashaet_none(self):
        self.assertIsNone(hh.read_lookup_percent('<node text="скидка 50%"/>'))


class ClassifyStep5ScreenTest(unittest.TestCase):
    def test_idushaya_proverka_uznayotsya(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(PROGRESS_DUMP)), "in_progress")

    def test_neizvestny_ekran_tak_i_nazyvaetsya(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(OTHER_DUMP)), "unknown")

    def test_dnevnoy_limit_uznayotsya(self):
        self.assertEqual(
            hh.classify_step5_screen("하루 최대 10대까지 조회 가능해요"),
            "daily_limit")

    def test_imya_vladeltsa_uznayotsya(self):
        self.assertEqual(
            hh.classify_step5_screen("소유자명을 알려주세요"),
            "owner_name_required")

    def test_sboy_servisa_uznayotsya(self):
        self.assertEqual(
            hh.classify_step5_screen("이력 조회 중 오류가 발생했어요"),
            "service_error")

    def test_arendnaya_kompaniya_uznayotsya_po_realnomu_dampu(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(RENTAL_DUMP)),
            "rental_company_required")

    def test_arendnaya_kompaniya_ne_putaetsya_s_fio(self):
        # На экране проката нет маркеров ФИО, и наоборот: у каждого
        # экрана своя формулировка для человека.
        self.assertFalse(hh.looks_like_owner_name_required(dump(RENTAL_DUMP)))
        self.assertFalse(hh.looks_like_rental_company_required(
            "소유자명을 알려주세요"))

    def test_arendnaya_kompaniya_ne_srabatyvaet_na_drugih_dampah(self):
        # Слово "렌터카" одно может встретиться в рекламе — признак
        # обязан молчать на всех остальных реальных экранах.
        for name in sorted(os.listdir(EVIDENCE)):
            if name == RENTAL_DUMP or not name.endswith(".xml"):
                continue
            with self.subTest(dump=name):
                self.assertFalse(
                    hh.looks_like_rental_company_required(dump(name)))

    def test_idushaya_proverka_vazhnee_ostalnogo(self):
        # Пока проверка идёт, остальных признаков на экране физически
        # нет. Но если они когда-нибудь совпадут, приоритет у прогресса:
        # ошибочный вывод "лимит исчерпан" стоит дороже лишнего ожидания.
        smeshannyy = dump(PROGRESS_DUMP) + "하루 최대 10대까지 조회 가능해요"
        self.assertEqual(hh.classify_step5_screen(smeshannyy), "in_progress")

    def test_pustaya_stroka_neizvestna(self):
        self.assertEqual(hh.classify_step5_screen(""), "unknown")


class SummarizeScreenTest(unittest.TestCase):
    def test_nazyvaet_soderzhimoe_neopoznannogo_ekrana(self):
        summary = hh.summarize_screen(dump(OTHER_DUMP))
        self.assertIn("닫기", summary)
        self.assertIn("car_number_plate_view", summary)

    def test_pustoy_ekran_ne_padaet(self):
        self.assertIn("не удалось", hh.summarize_screen(""))


class FakeLookupDriver:
    """
    Поддельный driver: отдаёт заранее заданную последовательность
    экранов из page_source и запоминает вызовы terminate_app/activate_app.

    Последний экран из списка отдаётся сколько угодно раз подряд — это
    имитирует "проверка стоит на месте", а не заканчивается сама.

    Экран, заданный как None, означает "прочитать не удалось": на его
    месте page_source поднимает исключение, как настоящий driver на
    отвалившемся устройстве.
    """

    def __init__(self, screens):
        self._screens = list(screens)
        self.terminate_calls = []
        self.activate_calls = []

    @property
    def page_source(self):
        if len(self._screens) > 1:
            screen = self._screens.pop(0)
        else:
            screen = self._screens[0]
        if screen is None:
            raise RuntimeError("устройство не ответило")
        return screen

    def terminate_app(self, package):
        self.terminate_calls.append(package)

    def activate_app(self, package):
        self.activate_calls.append(package)


class PollUntilLookupFinishedTest(unittest.TestCase):
    """
    Детектор зависания внутри poll_until_lookup_finished — единственный
    предохранитель от вечного ожидания. Настоящий сценарий (процент не
    двигается) на живом железе случался 4 раза за две недели, то есть
    проверить его в бою практически нельзя — только на поддельном driver.
    """

    def test_procent_dvizhetsya_percent_moved_true(self):
        progress_page = dump(PROGRESS_DUMP)
        # Один и тот же дамп даёт 90%, поэтому подменяем процент прямыми
        # строками с разным значением, чтобы получить настоящее движение.
        screens = [
            progress_page.replace('text="90%"', 'text="10%"'),
            progress_page.replace('text="90%"', 'text="50%"'),
            progress_page,  # третий экран — те же 90%, что и в исходном дампе
        ]
        driver = FakeLookupDriver(screens)
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            status, last_percent, percent_moved = (
                hh.poll_until_lookup_finished(driver, timeout_sec=0.05))
        self.assertTrue(percent_moved)

    def test_procent_stoit_percent_moved_false(self):
        progress_page = dump(PROGRESS_DUMP)
        driver = FakeLookupDriver([progress_page])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            status, last_percent, percent_moved = (
                hh.poll_until_lookup_finished(driver, timeout_sec=0.05))
        self.assertEqual(status, hh.LOOKUP_POLL_RUNNING)
        self.assertFalse(percent_moved)

    def test_ekran_progressa_ushyol_status_finished(self):
        progress_page = dump(PROGRESS_DUMP)
        other_page = dump(OTHER_DUMP)
        driver = FakeLookupDriver([progress_page, other_page])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            status, last_percent, percent_moved = (
                hh.poll_until_lookup_finished(driver, timeout_sec=5))
        self.assertEqual(status, hh.LOOKUP_POLL_FINISHED)

    def test_nechitaemy_ekran_ne_vydayotsya_za_ushedshiy_progress(self):
        # ГЛАВНОЕ РАЗЛИЧЕНИЕ. Нечитаемый экран нельзя возвращать как
        # finished=True: тогда шаг 5 уходит в ожидание с прокруткой — то
        # есть свайпает 30 секунд по экрану, который, весьма вероятно, всё
        # ещё прогресс. Отсутствие прокрутки здесь — требование.
        driver = FakeLookupDriver([dump(PROGRESS_DUMP), None])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            status, last_percent, percent_moved = (
                hh.poll_until_lookup_finished(driver, timeout_sec=5))
        self.assertEqual(status, hh.LOOKUP_POLL_UNREADABLE)

    def test_procent_popadaet_v_debug_na_kazhdom_oprose(self):
        # Каденция обновления процента не измерена, а порог "сколько
        # окон без движения считать зависанием" без неё — догадка. Эти
        # строки в логе и дают материал для такого замера.
        driver = FakeLookupDriver([dump(PROGRESS_DUMP)])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            with self.assertLogs("heydealer_history", level="DEBUG") as logs:
                hh.poll_until_lookup_finished(driver, timeout_sec=0.05)
        self.assertTrue(any("90" in line for line in logs.output),
                        f"процента нет в DEBUG-строках: {logs.output}")


class SafePageSourceTest(unittest.TestCase):
    def test_vozvrashaet_none_kogda_page_source_padaet(self):
        class BrokenDriver:
            @property
            def page_source(self):
                raise RuntimeError("устройство отвалилось")

        self.assertIsNone(hh._safe_page_source(BrokenDriver()))


class WaitOutLeftoverLookupTest(unittest.TestCase):
    def test_chistyy_ekran_vozvrashaet_true_bez_perezapuska(self):
        driver = FakeLookupDriver([dump(OTHER_DUMP)])
        # LEFTOVER_LOOKUP_WAIT_SEC подменять не нужно: на чистом экране
        # wait_out_leftover_lookup возвращает True до всякого ожидания.
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            result = hh.wait_out_leftover_lookup(driver)
        self.assertTrue(result)
        self.assertEqual(driver.terminate_calls, [])
        self.assertEqual(driver.activate_calls, [])

    def test_doschitavshayasya_proverka_govorit_pro_povtorny_vvod(self):
        # Расход квоты HeyDealer считается по ВВЕДЁННЫМ номерам, а не по
        # попыткам. Раз мы дождались чужой проверки и сейчас введём номер
        # заново, в логе это должно быть видно — иначе нечем будет
        # ответить, стоит ли ожидание своей квоты.
        driver = FakeLookupDriver([dump(PROGRESS_DUMP), dump(OTHER_DUMP)])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0), \
             mock.patch.object(hh, "log") as fake_log:
            self.assertTrue(hh.wait_out_leftover_lookup(driver))
        stroki = " ".join(str(c) for c in fake_log.call_args_list)
        self.assertIn("заново", stroki)
        self.assertEqual(driver.terminate_calls, [])

    def test_nechitaemy_ekran_ne_vedyot_k_perezapusku(self):
        # Экран перестал читаться — про устройство мы не знаем ничего.
        # Перезапускать приложение на этом основании нельзя: шаг 2 всё
        # равно проверит навигацию и скажет правду.
        driver = FakeLookupDriver([dump(PROGRESS_DUMP), None])
        with mock.patch.object(hh, "LOOKUP_POLL_SEC", 0):
            result = hh.wait_out_leftover_lookup(driver)
        self.assertTrue(result)
        self.assertEqual(driver.terminate_calls, [])
        self.assertEqual(driver.activate_calls, [])

    def test_ne_dozhdavshis_perezapuskaet_prilozhenie(self):
        driver = FakeLookupDriver([dump(PROGRESS_DUMP)])
        with mock.patch.object(hh, "LEFTOVER_LOOKUP_WAIT_SEC", 0.05), \
             mock.patch.object(hh, "LOOKUP_POLL_SEC", 0), \
             mock.patch.object(hh, "dump_screen_for_debug"):
            result = hh.wait_out_leftover_lookup(driver)
        self.assertFalse(result)
        self.assertEqual(len(driver.terminate_calls), 1)
        self.assertEqual(len(driver.activate_calls), 1)


if __name__ == "__main__":
    unittest.main()
