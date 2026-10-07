"""Диалог Horizon «Your session has expired» лечится переподключением.

РЕАЛЬНЫЙ СЛУЧАЙ (метка c2ffc5; скриншот
20260911_214717_unexpected_screen_after_login.png). Страница логина
грузилась 47 секунд, форма отправилась — а вместо рабочего стола портал
Horizon показал модальное окно:

    Error
    Your session has expired. Please re-connect the server.
                                                     [ OK ]

Бот 60 секунд ждал логотип каталога, которого на портале не бывает, и
сдался. Повтор упёрся в потолок навигации. Карточки менеджер не получил.

Здесь проверяется, что ожидание рабочего стола узнаёт этот диалог по
тексту, переподключается ОДИН раз и после этого дожидается каталога.
"""
import itertools
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import partsnumber_date as pn


class FakeLocator:
    def __init__(self, visible, on_click=None):
        self._visible = visible
        self._on_click = on_click
        self.clicks = 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def click(self, timeout=None):
        self.clicks += 1
        if self._on_click:
            self._on_click()


class FakePortalPage:
    """Портал Horizon с диалогом истёкшей сессии; после «OK» диалог исчезает."""

    def __init__(self, dialog=True, body_text="Error\nYour session has expired."):
        self.dialog = dialog
        self.url = "https://login.partsnumber.com/portal/webclient/index.html#/launchitems"
        self._body_text = body_text
        self.ok_button = FakeLocator(True, on_click=self._dismiss)
        self.waits = 0

    def _dismiss(self):
        self.dialog = False

    def get_by_text(self, text):
        return FakeLocator(self.dialog and text in "your session has expired")

    def get_by_role(self, role, name=None):
        assert role == "button"
        return self.ok_button

    def inner_text(self, selector, timeout=None):
        return self._body_text

    def wait_for_timeout(self, ms):
        self.waits += 1


def bare_session(page):
    session = object.__new__(pn.PartsNumberSession)
    session._page = page
    session._clipboard_failures_in_row = 0
    return session


class SessionExpiredDialogTest(unittest.TestCase):
    def test_dialog_uznayotsya_po_tekstu(self):
        self.assertTrue(
            bare_session(FakePortalPage())._session_expired_dialog_visible(FakePortalPage()))

    def test_bez_dialoga_otvet_net(self):
        page = FakePortalPage(dialog=False)
        self.assertFalse(bare_session(page)._session_expired_dialog_visible(page))

    def test_slomannyy_dom_ne_ronyaet_proverku(self):
        class BrokenPage:
            def get_by_text(self, text):
                raise RuntimeError("Execution context was destroyed")
        self.assertFalse(bare_session(BrokenPage())._session_expired_dialog_visible(BrokenPage()))

    def test_perepodklyuchenie_zhmyot_ok_i_vhodit_zanovo(self):
        page = FakePortalPage()
        session = bare_session(page)
        with mock.patch.object(session, "_login") as login:
            session._reconnect_after_session_expired(page)
        self.assertEqual(page.ok_button.clicks, 1)
        self.assertFalse(page.dialog)
        login.assert_called_once_with()

    def test_bez_knopki_vsyo_ravno_vhodit_zanovo(self):
        # Кнопка не нашлась — повторная навигация всё равно уводит со
        # страницы вместе с диалогом; главное — не упасть до неё.
        page = FakePortalPage()
        page.ok_button = FakeLocator(True, on_click=lambda: (_ for _ in ()).throw(RuntimeError("нет кнопки")))
        session = bare_session(page)
        with mock.patch.object(session, "_login") as login:
            session._reconnect_after_session_expired(page)
        login.assert_called_once_with()


class WaitForDesktopReadyTest(unittest.TestCase):
    """Ожидание рабочего стола с диалогом истёкшей сессии на пути."""

    def run_wait(self, page, logo_answers):
        session = bare_session(page)
        # Список ответов «виден ли логотип»; последний ответ повторяется.
        answers = itertools.chain(logo_answers, itertools.repeat(logo_answers[-1]))
        patches = dict(
            _is_on_catalogs_screen=mock.Mock(side_effect=lambda *a, **k: next(answers)),
            _recover_from_open_vin_screen=mock.Mock(return_value=False),
            _desktop_responds_to_input=mock.Mock(return_value=False),
            _login=mock.Mock(),
            _dump_debug_screenshot=mock.Mock(),
        )
        with mock.patch.multiple(session, **patches), \
                mock.patch.object(pn, "POLL_INTERVAL_SEC", 0), \
                mock.patch.object(pn, "DESKTOP_READY_TIMEOUT_SEC", 1.0):
            try:
                session._wait_for_desktop_ready()
                error = None
            except pn.PartsNumberSessionError as exc:
                error = exc
        # Заглушки снимаются на выходе из with — отдаём их сами.
        return patches, error

    def test_dialog_lechitsya_i_stol_dozhidaetsya(self):
        page = FakePortalPage()
        # Логотипа нет (портал), после переподключения — есть.
        mocks, error = self.run_wait(page, [False, True])
        self.assertIsNone(error)
        mocks["_login"].assert_called_once_with()
        self.assertEqual(page.ok_button.clicks, 1)

    def test_perepodklyuchenie_tolko_odin_raz(self):
        # Диалог возвращается и после переподключения: крутиться нельзя,
        # причина потеряется. Второй раз не переподключаемся — ждём и
        # сдаёмся с понятной ошибкой.
        page = FakePortalPage()
        page._dismiss = lambda: None          # «OK» не помогает
        page.ok_button = FakeLocator(True, on_click=page._dismiss)
        mocks, error = self.run_wait(page, [False])
        self.assertIsNotNone(error)
        mocks["_login"].assert_called_once_with()

    def test_bez_dialoga_login_ne_trogaetsya(self):
        page = FakePortalPage(dialog=False)
        mocks, error = self.run_wait(page, [False, False, True])
        self.assertIsNone(error)
        mocks["_login"].assert_not_called()

    def test_pri_otkaze_v_log_uhodit_tekst_stranitsy(self):
        page = FakePortalPage(dialog=False, body_text="Connecting...\n\nPlease wait")
        with self.assertLogs(pn.logger, level="WARNING") as captured:
            _, error = self.run_wait(page, [False])
        self.assertIsNotNone(error)
        self.assertTrue(any("Connecting... | Please wait" in line for line in captured.output),
                        captured.output)


class DescribePageTest(unittest.TestCase):
    def test_dlinnyy_tekst_obrezaetsya(self):
        page = FakePortalPage(body_text="x" * 1000)
        text = bare_session(page)._describe_page_for_log(page)
        self.assertLess(len(text), 520)
        self.assertTrue(text.endswith("…"))

    def test_pustaya_stranitsa_tak_i_nazyvaetsya(self):
        page = FakePortalPage(body_text="")
        self.assertIn("<пусто>", bare_session(page)._describe_page_for_log(page))


if __name__ == "__main__":
    unittest.main()
