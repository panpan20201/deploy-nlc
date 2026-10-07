"""Проверка VIN, пришедшего с Encar, без обращения к планшету.

ЗАЧЕМ. Carmoodo стоит около 45 секунд на каждой проверке и расходует
дневной лимит, а сверка Encar с Carmoodo на реальных объявлениях дала
78 совпадений из 78 полных VIN. Все отклонения Encar — отсутствие VIN
(4 случая) и обрезок в 8 символов (1 случай), то есть ровно то, что
ловится формально. Поэтому VIN с Encar принимается, если прошёл
проверки; Carmoodo остаётся запасным путём.

ЧТО ИМЕННО ПРОВЕРЯЕМ И ПОЧЕМУ ТАК.

Замер по боевой базе (115 VIN) показал чёткое разделение:

    контрольный разряд сошёлся   — 53 из 53 у BMW, Mercedes, Audi, MINI,
                                   Jeep, Toyota, VW, Porsche, Land Rover;
    не сошёлся                   — 61 из 61 у Kia, Hyundai, GM Korea,
                                   Genesis и прочих корейских.

То есть у машин внутреннего корейского рынка девятая позиция VIN не
является контрольным разрядом. Требовать его от них означало бы гнать в
Carmoodo каждую корейскую машину.

Поэтому правило: контрольный разряд ОБЯЗАН сойтись, кроме VIN, начинающихся
на "K" (корейский домашний рынок). Не сошёлся у импортной машины — отказ
в безопасную сторону, то есть в Carmoodo.

ЧЕГО ЭТА ПРОВЕРКА НЕ ЛОВИТ. Перебор всех 27136 одиночных опечаток в 53
VIN с рабочим разрядом: 92.8% ловится контрольным разрядом. Остальные
7.2% — замены букв внутри одной группы кодирования (A и J обе дают 1,
B/K/S дают 2 и так далее), они не меняют контрольную сумму в принципе.
Это свойство стандарта, а не недоработка проверки.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vin_check


# Настоящие VIN из боевой базы.
BMW = "WBA11BH00NCJ84943"          # контрольный разряд сходится
MERCEDES = "WDBEA22D1RC131656"     # W124, сходится
KIA_KOREAN = "KNAKN814DLA208115"   # корейский, разряда нет
HYUNDAI_KOREAN = "KMHYF811DNU032537"
JEEP = "1C4RJFCM5JC170456"


class UsableVinTest(unittest.TestCase):
    def test_importnyy_vin_s_vernym_razryadom_prinimaetsya(self):
        for vin in (BMW, MERCEDES, JEEP):
            ok, reason = vin_check.is_usable(vin)
            self.assertTrue(ok, f"{vin}: {reason}")

    def test_koreyskiy_vin_prinimaetsya_bez_razryada(self):
        # У корейских машин девятая позиция — не контрольный разряд.
        # Требовать его означало бы гнать в Carmoodo каждую корейскую
        # машину, то есть больше половины потока.
        for vin in (KIA_KOREAN, HYUNDAI_KOREAN):
            ok, reason = vin_check.is_usable(vin)
            self.assertTrue(ok, f"{vin}: {reason}")

    def test_importnyy_vin_so_slomannym_razryadom_otvergaetsya(self):
        # Меняем символ так, чтобы сумма изменилась: 1 -> 2 в серийной
        # части. Это ровно тот случай, ради которого разряд и проверяем.
        bityy = BMW[:12] + ("5" if BMW[12] != "5" else "6") + BMW[13:]
        ok, reason = vin_check.is_usable(bityy)
        self.assertFalse(ok)
        self.assertIn("контрольн", reason.lower())

    def test_obrezok_otvergaetsya(self):
        # Реальный случай из сверки: Encar показал 8 символов вместо 17.
        ok, reason = vin_check.is_usable("KMHE241C")
        self.assertFalse(ok)
        self.assertIn("17", reason)

    def test_otsutstvie_vin_otvergaetsya(self):
        for pusto in (None, "", "   "):
            ok, reason = vin_check.is_usable(pusto)
            self.assertFalse(ok)
            self.assertTrue(reason)

    def test_zapreshchennye_bukvy_otvergayutsya(self):
        # I, O и Q в VIN не используются: их путают с 1 и 0. Их появление
        # означает, что строку набирали руками.
        for bukva in ("I", "O", "Q"):
            bityy = KIA_KOREAN[:5] + bukva + KIA_KOREAN[6:]
            ok, reason = vin_check.is_usable(bityy)
            self.assertFalse(ok, f"буква {bukva} должна отвергаться")
            self.assertIn(bukva, reason)

    def test_probely_i_registr_ne_meshayut(self):
        ok, _ = vin_check.is_usable("  " + BMW.lower() + " ")
        self.assertTrue(ok)

    def test_normalizaciya_vozvrashaet_verhniy_registr_bez_probelov(self):
        self.assertEqual(vin_check.normalize("  " + BMW.lower() + " "), BMW)
        self.assertIsNone(vin_check.normalize(None))


class CheckDigitTest(unittest.TestCase):
    """Сам контрольный разряд — отдельно, чтобы ошибку было видно сразу."""

    def test_schitaet_verno_na_realnyh_vin(self):
        for vin in (BMW, MERCEDES, JEEP):
            self.assertTrue(vin_check.check_digit_ok(vin), vin)

    def test_koreyskie_ne_prohodyat_i_eto_ozhidaemo(self):
        for vin in (KIA_KOREAN, HYUNDAI_KOREAN):
            self.assertFalse(vin_check.check_digit_ok(vin), vin)

    def test_lyubaya_odinochnaya_opechatka_menyayushchaya_summu_lovitsya(self):
        # Не утверждение про 100%: замены внутри одной группы кодирования
        # (A/J, B/K/S и т.д.) сумму не меняют и пройдут — это свойство
        # стандарта. Проверяем те, что сумму меняют.
        pojmano = propushcheno = 0
        for i in range(17):
            if i == 8:
                continue
            for ch in "0123456789ABCDEFGHJKLMNPRSTUVWXYZ":
                if ch == BMW[i]:
                    continue
                kandidat = BMW[:i] + ch + BMW[i + 1:]
                if vin_check.check_digit_ok(kandidat):
                    propushcheno += 1
                else:
                    pojmano += 1
        # Цифры из замера по всей базе: ловится около 93%.
        dolya = pojmano / (pojmano + propushcheno)
        self.assertGreater(dolya, 0.90, f"ловится лишь {dolya:.1%}")


if __name__ == "__main__":
    unittest.main()
