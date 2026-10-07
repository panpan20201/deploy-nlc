"""Окончательный ответ не должен ждать полного таймаута.

НАЙДЕНО НА ЖИВОЙ РАБОТЕ: «долго проверялось, когда машина была
недоступна на HeyDealer».

Лог подтверждает: метки c5fa31 и ee1824, оба раза
«результат empty (owner_name_required), заняло 45.8 и 44.6 сек».

Ловушка: если цикл шага 5 сначала ждёт заголовок результата все
SEARCH_RESULT_TIMEOUT секунд и ТОЛЬКО ПОТОМ смотрит на экран, ответ
опаздывает. А экран с
требованием ФИО владельца приложение показывает сразу — как и экран
дневного лимита, и экран собственного сбоя. Все три — окончательные
ответы, и ждать ради них полминуты незачем.

Ранний взгляд стоит одного чтения экрана (примерно полторы секунды) на
обычной проверке и экономит около двадцати секунд на каждой машине,
которую HeyDealer не отдаёт.

⚠️ НА РАННЕМ ВЗГЛЯДЕ НЕЛЬЗЯ СДАВАТЬСЯ. Неопознанный экран через восемь
секунд — это чаще всего просто недогруженная страница (реальный случай:
заголовок пришёл на 18-й секунде). Ранний взгляд имеет право только
СОКРАТИТЬ путь к окончательному ответу, но не объявить неудачу.
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


def dump(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as f:
        return f.read()


MID_LOAD_DUMP = "heydealer_debug_step5_no_search_result_20260903_200040.xml"
OWNER_SCREEN = "<node text='소유자명을 알려주세요'/>"
RENTAL_SCREEN = ("<node text='어떤 렌터카 회사 차량인가요?&#10;"
                 "(자동차등록증 소유자명)'/>")


class SequenceDriver:
    def __init__(self, pages):
        self._pages = list(pages)

    @property
    def page_source(self):
        return self._pages[0] if len(self._pages) == 1 else self._pages.pop(0)

    def find_elements(self, *a, **k):
        return []

    def back(self):
        pass


class EarlyVerdictTest(unittest.TestCase):
    def run_step5(self, pages, **extra):
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = SequenceDriver(pages)
        session.last_reason = None
        scroll = mock.Mock(side_effect=TimeoutException("нет заголовка"))

        patches = dict(
            WebDriverWait=mock.MagicMock(),
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll,
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dismiss_notice_screen=mock.Mock(),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0,
            RESULT_RENDERING_PAUSE_SEC=0,
        )
        patches.update(extra)
        with mock.patch.multiple(hh, **patches):
            return session, session.get_repair_history("20수4034"), scroll

    def test_pervyy_vzglyad_korotkiy(self):
        # Окончательный ответ обязан обнаружиться на коротком окне, а не
        # после полного SEARCH_RESULT_TIMEOUT.
        self.assertLess(hh.EARLY_VERDICT_PROBE_SEC, hh.SEARCH_RESULT_TIMEOUT)

    def test_trebovanie_fio_uznayotsya_na_pervom_vzglyade(self):
        session, records, scroll = self.run_step5([OWNER_SCREEN])
        self.assertEqual(session.last_reason, "owner_name_required")
        self.assertEqual(records, [])
        # Ждали ОДНО короткое окно, а не полный таймаут.
        self.assertEqual(scroll.call_count, 1)
        self.assertEqual(
            scroll.call_args[0][2], hh.EARLY_VERDICT_PROBE_SEC,
            "первое ожидание должно быть коротким",
        )

    def test_arendnaya_kompaniya_uznayotsya_na_pervom_vzglyade(self):
        # Тот же тупик, что и ФИО, — и ответ обязан прийти так же быстро,
        # а не после 42 секунд и повтора (реальный случай, Staria).
        session, records, scroll = self.run_step5([RENTAL_SCREEN])
        self.assertEqual(session.last_reason, "rental_company_required")
        self.assertEqual(records, [])
        self.assertEqual(scroll.call_count, 1)

    def test_vtoroe_ozhidanie_uzhe_polnoy_dliny(self):
        # Короткое окно — только первое. Иначе обычная проверка начала бы
        # дёргать экран каждые восемь секунд без всякой пользы.
        session, _, scroll = self.run_step5(
            [dump(MID_LOAD_DUMP)],
            SEARCH_RESULT_TIMEOUT=30,
            LOOKUP_PROGRESS_MAX_TOTAL_SEC=0,
        ) if False else (None, None, None)
        # (сценарий проверяется ниже, через прямой прогон)
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = SequenceDriver([dump(MID_LOAD_DUMP)])
        session.last_reason = None
        scroll = mock.Mock(side_effect=TimeoutException("нет заголовка"))
        with mock.patch.multiple(
            hh,
            WebDriverWait=mock.MagicMock(),
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(), adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll,
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dismiss_notice_screen=mock.Mock(),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0, RESULT_RENDERING_PAUSE_SEC=0,
        ):
            with self.assertRaises(TimeoutException):
                session.get_repair_history("133다4272")

        okna = [c[0][2] for c in scroll.call_args_list]
        self.assertEqual(okna[0], hh.EARLY_VERDICT_PROBE_SEC)
        self.assertTrue(all(o == hh.SEARCH_RESULT_TIMEOUT for o in okna[1:]),
                        f"после первого окна должны идти полные: {okna}")

    def test_na_rannem_vzglyade_neopoznanny_ekran_ne_konchaet_proverku(self):
        # Реальный случай: заголовок пришёл на 18-й секунде. Сдаться на
        # восьмой означало бы объявить неудачу исправной машине.
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = SequenceDriver([dump(MID_LOAD_DUMP)])
        session.last_reason = None
        scroll = mock.Mock(side_effect=TimeoutException("нет заголовка"))
        with mock.patch.multiple(
            hh,
            WebDriverWait=mock.MagicMock(),
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(), adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll,
            poll_until_lookup_finished=mock.Mock(
                return_value=(hh.LOOKUP_POLL_FINISHED, None, False)),
            dismiss_promo_screen=mock.Mock(return_value=None),
            dismiss_notice_screen=mock.Mock(),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
            NO_HISTORY_CONFIRM_PAUSE_SEC=0, RESULT_RENDERING_PAUSE_SEC=0,
        ):
            with self.assertRaises(TimeoutException):
                session.get_repair_history("133다4272")
        self.assertGreater(scroll.call_count, 1,
                           "после раннего взгляда ожидание обязано продолжиться")


if __name__ == "__main__":
    unittest.main()
