# -*- coding: utf-8 -*-
"""
Ссылки KB Chachacha.

Страницы в tests/fixtures — настоящие, скачаны по ссылкам, которые
клиенты присылали боту (метки dff854/489823/b91399 и f791e7 — мобильная
ссылка «поделиться», сайт переадресовал её на carSeq=28791250).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encar_parser  # noqa: E402
import kbchachacha_parser as kb  # noqa: E402
from encar_parser import EncarParsingError  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _page(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


DESKTOP = _page("kbchachacha_desktop_28780780.html")
MOBILE = _page("kbchachacha_mobile_28791250.html")

URL_DESKTOP = "https://www.kbchachacha.com/public/car/detail.kbc?carSeq=28780780"
URL_SHARE = ("https://m.kbchachacha.com/public/web/common/sns/car/detail.kbc"
             "?c=aBjDASyQRqzJ9TzRuv%2Fp4A%3D%3D")


def _response(text, status=200, final_url=URL_DESKTOP):
    r = mock.Mock()
    r.text = text
    r.status_code = status
    r.url = final_url
    r.encoding = "utf-8"
    return r


class ParsePageTest(unittest.TestCase):
    def test_plate_desktop(self):
        self.assertEqual(kb.parse_plate(DESKTOP), "381가7365")

    def test_plate_mobile(self):
        self.assertEqual(kb.parse_plate(MOBILE), "326오4610")

    def test_plate_from_table_when_no_meta(self):
        # Запасной путь: мета-тег пропал, номер остался в таблице.
        page = DESKTOP.replace('property="og:description"', 'property="og:x"')
        self.assertEqual(kb.parse_plate(page), "381가7365")

    def test_grade_without_year(self):
        self.assertEqual(kb.parse_grade(DESKTOP), "BMW New X4 (G02) xDrive 20d M Sport Pro")
        self.assertEqual(kb.parse_grade(MOBILE), "KG모빌리티 뷰티플 코란도 1.6 디젤 AWD C7")

    def test_no_plate_on_foreign_page(self):
        self.assertIsNone(kb.parse_plate("<html><body>Encar</body></html>"))


class UrlTest(unittest.TestCase):
    def test_recognizes_all_seen_forms(self):
        for url in (URL_DESKTOP, URL_SHARE,
                    "https://m.kbchachacha.com/public/web/car/detail.kbc?carSeq=28791250"):
            self.assertTrue(kb.is_kbchachacha_url(url), url)
        self.assertFalse(kb.is_kbchachacha_url("https://fem.encar.com/cars/detail/42201853"))
        self.assertFalse(kb.is_kbchachacha_url("https://kbchachacha.com.evil.example/x"))

    def test_find_url_in_message_with_text(self):
        self.assertEqual(kb.find_url(f"посмотри {URL_SHARE} обнови"), URL_SHARE)
        self.assertIsNone(kb.find_url("https://fem.encar.com/cars/detail/42201853"))


class EntryPointTest(unittest.TestCase):
    """Через ту же функцию, что вызывает pipeline для любой ссылки."""

    def test_desktop_link(self):
        with mock.patch.object(kb.requests, "get", return_value=_response(DESKTOP)) as get:
            plate, vin, grade = encar_parser.get_vehicle_number_vin_and_grade(URL_DESKTOP)
        self.assertEqual((plate, vin), ("381가7365", None))
        self.assertEqual(grade, "BMW New X4 (G02) xDrive 20d M Sport Pro")
        self.assertTrue(get.call_args.kwargs["allow_redirects"])

    def test_share_link_follows_redirect(self):
        resp = _response(MOBILE, final_url="https://m.kbchachacha.com/public/web/car/detail.kbc?carSeq=28791250")
        with mock.patch.object(kb.requests, "get", return_value=resp):
            plate, vin, _ = encar_parser.get_vehicle_number_vin_and_grade(URL_SHARE + " обнови")
        self.assertEqual((plate, vin), ("326오4610", None))

    def test_page_without_plate_is_parsing_error(self):
        with mock.patch.object(kb.requests, "get", return_value=_response("<html></html>")):
            with self.assertRaises(EncarParsingError):
                encar_parser.get_vehicle_number_vin_and_grade(URL_DESKTOP)

    def test_http_error_is_parsing_error(self):
        with mock.patch.object(kb.requests, "get", return_value=_response("", status=404)):
            with self.assertRaises(EncarParsingError):
                encar_parser.get_vehicle_number_vin_and_grade(URL_DESKTOP)

    def test_encar_link_does_not_go_to_kb(self):
        with mock.patch.object(kb.requests, "get") as kb_get, \
             mock.patch.object(encar_parser, "_fetch_all", return_value=("12가3456", None, None)):
            plate, _, _ = encar_parser.get_vehicle_number_vin_and_grade(
                "https://fem.encar.com/cars/detail/42201853")
        self.assertEqual(plate, "12가3456")
        kb_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
