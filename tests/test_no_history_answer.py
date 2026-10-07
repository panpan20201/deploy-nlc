"""Машина без записей истории должна получать ОТВЕТ, а не «техническую ошибку».

РЕАЛЬНЫЙ СЛУЧАЙ (метка bf15f7, номер 20수4034,
VIN 1C4RJFCM5JC170456 — Jeep Grand Cherokee). Поиск отработал, приложение
показало карточку машины, в списке недавних она помечена «이력 0». Но
заголовка «이력 요약» на экране нет — для машин без записей приложение этот
блок просто не рисует. Бот прождал 33 секунды и отдал «техническая ошибка».

Цена такой подмены: человек повторяет проверку и жжёт одну из десяти
суточных, чтобы получить то, что уже было получено.

⚠️ ГЛАВНАЯ ОПАСНОСТЬ ЗДЕСЬ — ОБРАТНАЯ. Если объявить «истории нет»
у машины, у которой блок истории просто ещё не догрузился, человек купит
битую машину с чистым отчётом. По этому отчёту покупают машины, то есть
цена такой ошибки — десятки тысяч долларов.
Поэтому вывод делается только после ДВУХ взглядов на экран с паузой между
ними — по тому же правилу двух подтверждений, что и у дневного лимита
Carmoodo (см. usage.mark_limit_hit).

Отрицательный образец настоящий: дамп 03.09 снят в момент, когда результат
ещё не догрузился, а со второй попытки у ТОЙ ЖЕ машины нашлось 34 записи.
Если признак сработает на нём — распознавание недопустимо.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "evidence",
)

from selenium.common.exceptions import TimeoutException

import heydealer_history as hh


NO_HISTORY_DUMP = "heydealer_debug_step5_no_search_result_20260914_173306.xml"
MID_LOAD_DUMP = "heydealer_debug_step5_no_search_result_20260903_200040.xml"


def dump(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as f:
        return f.read()


class LooksLikeCarSummaryTest(unittest.TestCase):
    def test_uznayot_kartochku_mashiny_bez_istorii(self):
        self.assertTrue(hh.looks_like_car_summary(dump(NO_HISTORY_DUMP)))

    def test_ne_srabatyvaet_na_nedogruzhennom_ekrane(self):
        # Машина с 34 записями, поймана в момент загрузки. Срабатывание
        # здесь означало бы «история чистая» у машины с историей.
        self.assertFalse(hh.looks_like_car_summary(dump(MID_LOAD_DUMP)))

    def test_pustoy_ekran_ne_srabatyvaet(self):
        self.assertFalse(hh.looks_like_car_summary(""))
        self.assertFalse(hh.looks_like_car_summary(None))


class SequenceDriver:
    """driver, отдающий экраны по очереди: последний повторяется."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.reads = 0

    @property
    def page_source(self):
        self.reads += 1
        page = self._pages[0] if len(self._pages) == 1 else self._pages.pop(0)
        return page

    def find_elements(self, *args, **kwargs):
        return []

    def back(self):
        pass


class ConfirmNoHistoryTest(unittest.TestCase):
    """Подтверждение вторым взглядом."""

    def setUp(self):
        self._patch = mock.patch.object(hh, "NO_HISTORY_CONFIRM_PAUSE_SEC", 0)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_dva_vzglyada_na_kartochku_dayut_podtverzhdenie(self):
        page = dump(NO_HISTORY_DUMP)
        driver = SequenceDriver([page])
        self.assertTrue(hh.confirm_no_history(driver, page))

    def test_esli_ko_vtoromu_vzglyadu_poyavilas_istoriya_to_ne_podtverzhdeno(self):
        # Ровно та гонка, ради которой второй взгляд и нужен: сводка
        # отрисовалась раньше блока истории.
        first = dump(NO_HISTORY_DUMP)
        second = first + '<node text="이력 요약"/>'
        # Первый взгляд передаётся аргументом, второй берётся с драйвера.
        driver = SequenceDriver([second])
        self.assertFalse(hh.confirm_no_history(driver, first))
        self.assertEqual(driver.reads, 1, "второй взгляд обязан состояться")

    def test_ekran_zagruzki_ne_podtverzhdaetsya(self):
        # Пока идёт проверка, выводов не делаем вовсе.
        progress = dump("heydealer_debug_step5_no_search_result_20260904_175210.xml")
        driver = SequenceDriver([progress])
        self.assertFalse(hh.confirm_no_history(driver, progress))

    def test_nechitaemy_ekran_ne_podtverzhdaetsya(self):
        class Broken(SequenceDriver):
            @property
            def page_source(self):
                raise RuntimeError("экран не читается")

        self.assertFalse(
            hh.confirm_no_history(Broken([""]), dump(NO_HISTORY_DUMP))
        )


class Step5NoHistoryTest(unittest.TestCase):
    """Шаг 5 целиком: карточка без истории -> ответ, а не исключение."""

    def run_step5(self, pages):
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = SequenceDriver(pages)
        session.last_reason = None

        with mock.patch.multiple(
            hh,
            WebDriverWait=mock.MagicMock(),
            # Шаг 4 проверяется отдельно (tests/test_plate_entry.py);
            # здесь он заглушён, чтобы тест говорил про шаг 5.
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=mock.Mock(
                side_effect=TimeoutException("нет результата")),
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0,
            SEARCH_RESULT_TIMEOUT=0,
            LOOKUP_PROGRESS_MAX_TOTAL_SEC=0,
        ):
            return session, session.get_repair_history("20수4034")

    def test_kartochka_bez_istorii_daet_otvet_no_records(self):
        session, records = self.run_step5([dump(NO_HISTORY_DUMP)])
        self.assertEqual(records, [])
        self.assertEqual(session.last_reason, "no_records")

    def test_nedogruzhennyy_ekran_po_prezhnemu_tehnicheskaya_neudacha(self):
        with self.assertRaises(TimeoutException):
            self.run_step5([dump(MID_LOAD_DUMP)])


if __name__ == "__main__":
    unittest.main()
