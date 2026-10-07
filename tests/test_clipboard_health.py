"""Состояние канала буфера Horizon.

Канал может отказывать массово (наблюдалось 88 отказов буфера за день,
11 провалов PARTSNUMBER из 20), и без сигнала владелец об этом не узнаёт.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipboard_health


class ClipboardHealthTest(unittest.TestCase):
    def setUp(self):
        clipboard_health.reset_for_tests()

    def test_po_umolchaniyu_kanal_zhiv(self):
        self.assertTrue(clipboard_health.is_alive())
        self.assertIsNone(clipboard_health.last_change())

    def test_mark_dead_menyaet_sostoyanie(self):
        clipboard_health.mark_dead()
        self.assertFalse(clipboard_health.is_alive())
        self.assertIsNotNone(clipboard_health.last_change())

    def test_mark_alive_vozvrashaet_sostoyanie(self):
        clipboard_health.mark_dead()
        clipboard_health.mark_alive()
        self.assertTrue(clipboard_health.is_alive())

    def test_povtorny_mark_dead_ne_sdvigaet_vremya(self):
        # Иначе "сломалось в 14:05" превращалось бы в "сломалось только
        # что" при каждом следующем отказе, и понять, сколько это длится,
        # было бы нельзя.
        clipboard_health.mark_dead()
        first = clipboard_health.last_change()
        clipboard_health.mark_dead()
        self.assertEqual(clipboard_health.last_change(), first)


if __name__ == "__main__":
    unittest.main()
