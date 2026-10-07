# -*- coding: utf-8 -*-
"""
«Access to the catalog is closed» после выбора группы каталога.

РЕАЛЬНЫЙ СЛУЧАЙ (метка 074ce6, VIN KNMK5B2RMNP032028, Renault XM3).
Бот верно выбрал группу Renault, но каталог показал красную плашку
«Access to the catalog is closed» (скриншот
tests/fixtures/evidence/20261001_182734_renault_catalog_closed.png).
Плашка того же цвета и в том же месте, что «Incorrect VIN», поэтому её
легко принять за сбой ввода: без отдельного распознавания это две
попытки по ~70 с и «техническая ошибка» вместо честного ответа.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import partsnumber_date as pn  # noqa: E402
import pipeline  # noqa: E402

VIN = "KNMK5B2RMNP032028"


def _session():
    s = object.__new__(pn.PartsNumberSession)
    s.last_reason = None
    return s


class RunSearchBannerTest(unittest.TestCase):
    def _run(self, group_retry):
        s = _session()
        page = mock.MagicMock()
        with mock.patch.object(s, "_region_is_error_banner", return_value=True):
            return s._run_search(page, expected_vin=VIN, _group_retry=group_retry)

    def test_banner_after_group_choice_is_catalog_closed(self):
        self.assertEqual(self._run(group_retry=True), "catalog_closed")

    def test_banner_without_group_choice_stays_invalid_format(self):
        # Обычный случай (метка 49b3d4) — его не трогаем.
        self.assertEqual(self._run(group_retry=False), "invalid_format")


class GetVehicleCardTest(unittest.TestCase):
    def test_catalog_closed_is_answer_not_error(self):
        s = _session()
        with mock.patch.object(s, "_require_page", return_value=mock.MagicMock()), \
             mock.patch.object(s, "_ensure_desktop_alive"), \
             mock.patch.object(s, "_ensure_catalog_window"), \
             mock.patch.object(s, "_enter_vin"), \
             mock.patch.object(s, "_dump_debug_screenshot"), \
             mock.patch.object(s, "_run_search", return_value="catalog_closed"):
            # Не бросает исключение — значит, pipeline не станет повторять.
            self.assertIsNone(s.get_vehicle_card(VIN))
        self.assertEqual(s.last_reason, "catalog_closed")


class PipelineTextTest(unittest.TestCase):
    def test_honest_message(self):
        s = _session()
        s.last_reason = "catalog_closed"
        res = pipeline._interpret_partsnumber(s, None)
        self.assertEqual(res.reason, "catalog_closed")
        self.assertFalse(res.ok)
        self.assertIn("не входит в наш доступ", res.message)
        self.assertIn("не входит в наш доступ", pipeline._format_parts(res, pipeline.StepResult("ok", value=VIN)))

    def test_plain_not_found_unchanged(self):
        res = pipeline._interpret_partsnumber(_session(), None)
        self.assertEqual(res.reason, "not_found")


if __name__ == "__main__":
    unittest.main()
