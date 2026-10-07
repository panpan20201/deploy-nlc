# -*- coding: utf-8 -*-
"""
Тексты, которые видит клиент. Требования владельца: детали не
«пропущены», пока VIN ещё ищется; без «проверяю» после «продолжаю
искать».
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
from pipeline import StepResult  # noqa: E402


class PartsLineTest(unittest.TestCase):
    def test_waiting_for_vin_is_not_skipped(self):
        text = pipeline._assemble("12가3456", None, None, None, None)
        self.assertIn("🔧 Детали и даты (PARTSNUMBER): ждёт VIN... ⏳", text)
        self.assertNotIn("пропущено", text)

    def test_skipped_only_when_vin_failed(self):
        vin = StepResult("empty", reason="not_found", message="машина не найдена в базе")
        text = pipeline._assemble("12가3456", vin, None, None, None)
        self.assertIn("🔧 Детали и даты (PARTSNUMBER): пропущено — нет VIN", text)

    def test_waiting_for_parts_after_vin(self):
        vin = StepResult("ok", value="KMHJE81BGNU146930")
        text = pipeline._assemble("12가3456", vin, None, None, None)
        self.assertIn("🔧 Детали и даты (PARTSNUMBER): проверяю... ⏳", text)

    def test_partial_repair_line_has_no_double_waiting(self):
        records = [{"date": "2025년 10월 24일", "mileage": "111,274km",
                    "repair_details": "✓️ 엔진\n엔진커버(타이밍커버)\nㄴ 관련 부품 교체(신품 1건)"}]
        text = pipeline._assemble("12가3456", None, None, None, None, partial_records=records)
        self.assertIn("найдено записей: 1, продолжаю искать... ⏳", text)
        self.assertNotIn("продолжаю искать проверяю", text)


if __name__ == "__main__":
    unittest.main()
