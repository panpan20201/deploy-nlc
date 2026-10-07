"""Недогруженный экран результата должен ЖДАТЬ, а не объявлять «истории нет».

НАЙДЕНО ПО ЖИВОМУ ПРОГОНУ.

Машина 20수4034 (Jeep Grand Cherokee):
  17:32 — заголовок «이력 요약» не появился и за 34 секунды;
  18:13 — у ТОЙ ЖЕ машины появился за 18 секунд, и шаг 7 нашёл
          карточки обслуживания.

То есть первый экран был просто недогружен. Подтверждение двумя
взглядами с паузой 5 секунд такой случай НЕ ловит: за пять секунд
медленная страница не становится быстрой.

Корень в другом месте: экран «карточка машины есть, блока истории ещё
нет» нельзя относить к ветке «непонятный экран» — она ОБРЫВАЕТ цикл на
базовых 30 секундах, не тронув собственный бюджет продлений в 120.
Правильное поведение — продлевать ожидание, как и для экрана загрузки, а вывод
«записей нет» делать только когда весь бюджет выбран.

Цена ошибки в эту сторону: чистый отчёт на битую машину. По этому отчёту
покупают машины.
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


SUMMARY_DUMP = "heydealer_debug_step5_no_search_result_20260914_173306.xml"
PROGRESS_DUMP = "heydealer_debug_step5_no_search_result_20260904_175210.xml"
MID_LOAD_DUMP = "heydealer_debug_step5_no_search_result_20260903_200040.xml"


def dump(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as f:
        return f.read()


class ClassifyResultRenderingTest(unittest.TestCase):
    def test_kartochka_bez_zagolovka_eto_otrisovka_a_ne_nepoznanny_ekran(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(SUMMARY_DUMP)), "result_rendering"
        )

    def test_ekran_zagruzki_po_prezhnemu_in_progress(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(PROGRESS_DUMP)), "in_progress"
        )

    def test_chuzhoy_ekran_po_prezhnemu_neizvesten(self):
        self.assertEqual(
            hh.classify_step5_screen(dump(MID_LOAD_DUMP)), "unknown"
        )


class SequenceDriver:
    def __init__(self, pages):
        self._pages = list(pages)
        self.reads = 0

    @property
    def page_source(self):
        self.reads += 1
        return self._pages[0] if len(self._pages) == 1 else self._pages.pop(0)

    def find_elements(self, *args, **kwargs):
        return []

    def back(self):
        pass


class Step5RenderingTest(unittest.TestCase):
    """Поведение шага 5 на недогруженном экране результата."""

    def build(self, pages, scroll_side_effect):
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = SequenceDriver(pages)
        session.last_reason = None
        return session, mock.Mock(side_effect=scroll_side_effect)

    def run_step5(self, pages, scroll_side_effect, **extra):
        session, scroll = self.build(pages, scroll_side_effect)
        patches = dict(
            WebDriverWait=mock.MagicMock(),
            # Шаг 4 проверяется отдельно (tests/test_plate_entry.py);
            # здесь он заглушён, чтобы тест говорил про шаг 5.
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll,
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0,
            RESULT_RENDERING_PAUSE_SEC=0,
        )
        patches.update(extra)
        with mock.patch.multiple(hh, **patches):
            return session, session.get_repair_history("20수4034"), scroll

    def test_nedogruzhennyy_ekran_ne_obryvaet_ozhidanie_srazu(self):
        # Главный тест. Экран показывает карточку без блока истории; цикл
        # не должен обрываться на первом же таймауте базового ожидания —
        # он обязан выбрать весь бюджет.
        session, records, scroll = self.run_step5(
            [dump(SUMMARY_DUMP)],
            TimeoutException("заголовка нет"),
            SEARCH_RESULT_TIMEOUT=1,
            LOOKUP_PROGRESS_MAX_TOTAL_SEC=3,
        )
        self.assertGreater(
            scroll.call_count, 1,
            "ожидание обязано продлеваться, а не обрываться на первом таймауте",
        )
        self.assertEqual(records, [])
        self.assertEqual(session.last_reason, "no_records")

    def test_zagolovok_poyavilsya_pozdno_znachit_istoriya_est(self):
        # Реальный случай: страница просто грузилась долго. Объявить
        # "истории нет" здесь означало бы отдать чистый отчёт на машину,
        # у которой записи есть.
        popytki = [TimeoutException("ещё не пришёл"), None]

        def scroll_effect(*args, **kwargs):
            item = popytki.pop(0) if len(popytki) > 1 else popytki[0]
            if isinstance(item, Exception):
                raise item
            return mock.Mock()

        # Дальше шага 5 на поддельном driver'е сценарий не уедет, и это
        # нормально: утверждение теста — про вывод шага 5, а не про
        # успешность всей проверки.
        session, scroll = self.build([dump(SUMMARY_DUMP)], scroll_effect)
        with mock.patch.multiple(
            hh,
            WebDriverWait=mock.MagicMock(),
            # Шаг 4 проверяется отдельно (tests/test_plate_entry.py);
            # здесь он заглушён, чтобы тест говорил про шаг 5.
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll,
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0,
            RESULT_RENDERING_PAUSE_SEC=0,
            SEARCH_RESULT_TIMEOUT=1,
            LOOKUP_PROGRESS_MAX_TOTAL_SEC=30,
        ):
            try:
                session.get_repair_history("20수4034")
            except Exception:
                pass

        self.assertNotEqual(
            session.last_reason, "no_records",
            "поздно пришедший заголовок не должен превращаться в 'истории нет'",
        )
        self.assertGreaterEqual(
            scroll.call_count, 2, "второе ожидание обязано было состояться"
        )


if __name__ == "__main__":
    unittest.main()
