"""Цикл ожидания шага 5: сколько ждём, что считаем зависанием и когда
НЕ уходим в ожидание с прокруткой.

Проверить это на живом железе нельзя: нужный сценарий (проверка стоит на
одном проценте) случался считаные разы за две недели, и повторить его по
заказу невозможно. Поэтому цикл прогоняется на поддельном driver.

Сессия создаётся через __new__ намеренно: __enter__ поднимает настоящий
Appium, а нам нужен ровно шаг 5 и ничего больше.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EVIDENCE = os.path.join(ROOT, "tests", "fixtures", "evidence")

from selenium.common.exceptions import TimeoutException

import heydealer_history as hh

PROGRESS_DUMP = "heydealer_debug_step5_no_search_result_20260904_175210.xml"


def dump(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as f:
        return f.read()


class FakeStep5Driver:
    """driver, который всегда показывает экран идущей проверки."""

    def __init__(self, page):
        self._page = page
        self.back_calls = 0

    @property
    def page_source(self):
        return self._page

    def find_elements(self, *args, **kwargs):
        return []

    def back(self):
        self.back_calls += 1


class Step5WaitTest(unittest.TestCase):
    def run_step5(self, poll_results, **patches):
        """
        Прогоняет шаг 5 до отказа и возвращает (исключение, счётчики).

        poll_results — что по очереди возвращает poll_until_lookup_finished;
        последний элемент повторяется столько раз, сколько понадобится.
        """
        session = hh.HeyDealerSession.__new__(hh.HeyDealerSession)
        session.driver = FakeStep5Driver(dump(PROGRESS_DUMP))
        session.last_reason = None

        ostatok = list(poll_results)

        def fake_poll(driver, timeout_sec):
            return ostatok.pop(0) if len(ostatok) > 1 else ostatok[0]

        poll = mock.Mock(side_effect=fake_poll)
        scroll_wait = mock.Mock(side_effect=TimeoutException("нет результата"))

        obshie = dict(
            WebDriverWait=mock.MagicMock(),
            # Шаг 4 проверяется отдельно (tests/test_plate_entry.py);
            # здесь он заглушён, чтобы тест говорил про шаг 5.
            enter_plate_confirmed=mock.Mock(return_value=True),
            adb_clear_text=mock.Mock(),
            adb_input_text=mock.Mock(),
            wait_for_element_with_scroll=scroll_wait,
            poll_until_lookup_finished=poll,
            dismiss_promo_screen=mock.Mock(return_value=None),
            dump_screen_for_debug=mock.Mock(),
            clear_plate_input=mock.Mock(),
            dismiss_exit_confirm_dialog=mock.Mock(),
        )
        obshie.update(patches)

        with mock.patch.multiple(hh, **obshie):
            with self.assertRaises(TimeoutException) as ctx:
                session.get_repair_history("04조5666")
        return ctx.exception, poll, scroll_wait

    def test_odnogo_okna_bez_dvizheniya_procenta_malo(self):
        # Единственный дамп-свидетель показывает 90% — последний блок
        # (страховая история). Сколько он считается, НЕ МЕРЕНО, и отказ
        # после одного окна срубал бы ровно тот случай, ради которого
        # продление и делалось. Требуем два окна подряд.
        exc, poll, _ = self.run_step5([(hh.LOOKUP_POLL_RUNNING, 90, False)])
        self.assertIn("progress_frozen", str(exc))
        self.assertEqual(poll.call_count, hh.LOOKUP_PROGRESS_FROZEN_WINDOWS)
        self.assertEqual(hh.LOOKUP_PROGRESS_FROZEN_WINDOWS, 2)

    def test_dvinuvshiysya_procent_obnulyaet_schyotchik(self):
        exc, poll, _ = self.run_step5([
            (hh.LOOKUP_POLL_RUNNING, 90, False),   # окно 1: стоит
            (hh.LOOKUP_POLL_RUNNING, 92, True),    # окно 2: сдвинулся — сброс
            (hh.LOOKUP_POLL_RUNNING, 92, False),   # окно 3: стоит
            (hh.LOOKUP_POLL_RUNNING, 92, False),   # окно 4: стоит второй раз
        ])
        self.assertIn("progress_frozen", str(exc))
        self.assertEqual(poll.call_count, 4)

    def test_nechitaemy_ekran_ne_uvodit_v_ozhidanie_s_prokrutkoy(self):
        # Во время продления экран НЕ прокручивается, и это требование, а
        # не оптимизация. Нечитаемый экран нельзя выдавать за "прогресс
        # ушёл": это увело бы в wait_for_element_with_scroll, который
        # 30 секунд свайпал бы по неизученному экрану.
        exc, poll, scroll_wait = self.run_step5([
            (hh.LOOKUP_POLL_UNREADABLE, 90, False),
            (hh.LOOKUP_POLL_UNREADABLE, 90, False),
            (hh.LOOKUP_POLL_RUNNING, 90, False),
            (hh.LOOKUP_POLL_RUNNING, 90, False),
        ])
        # Ожидание с прокруткой — ровно одно, самое первое (базовые 30
        # секунд). Два нечитаемых окна новых не добавили.
        self.assertEqual(scroll_wait.call_count, 1)
        self.assertEqual(poll.call_count, 4)

    def test_ushedshiy_progress_vozvrashaet_ozhidanie_s_prokrutkoy(self):
        # Обратная сторона того же различения: как только прогресс ушёл,
        # прокрутка снова нужна — блок вкладок бывает ниже сгиба.
        exc, poll, scroll_wait = self.run_step5([
            (hh.LOOKUP_POLL_FINISHED, 90, True),
            (hh.LOOKUP_POLL_RUNNING, 90, False),
            (hh.LOOKUP_POLL_RUNNING, 90, False),
        ])
        self.assertEqual(scroll_wait.call_count, 2)

    def test_v_tekste_oshibki_realnoe_vremya_a_ne_summa_nominalov(self):
        # Счётчик считает фактическое время, а не номиналы (+30 за каждое
        # продление независимо от того, сколько оно шло на самом деле) —
        # иначе текст ошибки завышал бы ожидание почти вдвое. Здесь продления мгновенные,
        # значит и в тексте должны быть единицы секунд, а не 60.
        exc, _, _ = self.run_step5([(hh.LOOKUP_POLL_RUNNING, 90, False)])
        chisla = [int(s) for s in str(exc).replace("(", " ").replace(")", " ")
                  .split() if s.isdigit()]
        self.assertTrue(chisla, f"в тексте нет числа секунд: {exc}")
        self.assertLess(min(chisla), 10, f"время завышено: {exc}")

    def test_potolok_prodleniy_ostanavlivaet_ozhidanie(self):
        # Потолок — это бюджет ПРОДЛЕНИЙ поверх базового порога. Обнулив
        # оба, получаем исчерпание сразу: ни одного продления быть не
        # должно.
        with mock.patch.object(hh, "SEARCH_RESULT_TIMEOUT", 0), \
             mock.patch.object(hh, "LOOKUP_PROGRESS_MAX_TOTAL_SEC", 0):
            exc, poll, _ = self.run_step5([(hh.LOOKUP_POLL_RUNNING, 90, False)])
        self.assertIn("progress_over_budget", str(exc))
        self.assertEqual(poll.call_count, 0)


if __name__ == "__main__":
    unittest.main()

