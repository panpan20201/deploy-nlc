"""Резервные копии базы проверенных машин.

ЗАЧЕМ. Каждая запись в базе — это запрос, который больше не придётся
тратить из дефицитных суточных лимитов (10 у HeyDealer, 30 у Carmoodo).
Терять базу нельзя, её нужно копировать отдельно.

ПОЧЕМУ НЕ ПРОСТО СКОПИРОВАТЬ ФАЙЛ. База работает в режиме WAL: часть
свежих данных лежит не в vehicles.db, а в vehicles.db-wal. Копия,
снятая обычным copy во время записи, может оказаться битой или неполной.
Поэтому копируем встроенным механизмом SQLite, который согласован с
пишущими и даёт целостный снимок.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "vehicles.db")
        self.dir = os.path.join(self.tmp, "backups")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE vehicle_data (plate TEXT, value TEXT)")
        conn.execute("INSERT INTO vehicle_data VALUES ('11가1111', 'WBA11BH00NCJ84943')")
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kopiya_sozdayotsya_i_chitaetsya(self):
        path = backup.make_backup(self.db, self.dir)
        self.assertTrue(os.path.exists(path))
        # Копия должна быть РАБОЧЕЙ базой, а не просто файлом нужного размера.
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT plate, value FROM vehicle_data").fetchall()
        conn.close()
        self.assertEqual(rows, [("11가1111", "WBA11BH00NCJ84943")])

    def test_kopiya_prohodit_proverku_celostnosti(self):
        path = backup.make_backup(self.db, self.dir)
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_za_sutki_delaetsya_odna_kopiya(self):
        first = backup.backup_if_due(self.db, self.dir)
        second = backup.backup_if_due(self.db, self.dir)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "вторая копия за те же сутки не нужна")

    def test_starye_pokoleniya_udalyayutsya(self):
        # Кладём больше копий, чем разрешено хранить.
        os.makedirs(self.dir, exist_ok=True)
        for day in range(1, backup.KEEP_COPIES + 4):
            name = os.path.join(self.dir, f"vehicles-2026-08-{day:02d}.db")
            shutil.copyfile(self.db, name)
        backup.cleanup_old(self.dir)
        left = [f for f in os.listdir(self.dir) if f.endswith(".db")]
        self.assertEqual(len(left), backup.KEEP_COPIES)
        # Удаляться должны САМЫЕ СТАРЫЕ, а не первые попавшиеся.
        self.assertNotIn("vehicles-2026-08-01.db", left)
        self.assertIn(f"vehicles-2026-08-{backup.KEEP_COPIES + 3:02d}.db", left)

    def test_otsutstvie_bazy_ne_ronyaet_bota(self):
        # Резервное копирование не должно быть причиной падения: если базы
        # нет, это повод для предупреждения, а не для остановки проверок.
        result = backup.backup_if_due(os.path.join(self.tmp, "нет.db"), self.dir)
        self.assertIsNone(result)

    def test_polomka_pri_kopirovanii_ne_ronyaet_bota(self):
        # Каталог копий занят файлом — записать не выйдет.
        zanyato = os.path.join(self.tmp, "занято")
        with open(zanyato, "w", encoding="utf-8") as f:
            f.write("не каталог")
        self.assertIsNone(backup.backup_if_due(self.db, zanyato))


if __name__ == "__main__":
    unittest.main()
