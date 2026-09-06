"""Tests for main.py — finding the SAML tab and reacting to the Duo result."""
from unittest.mock import MagicMock, patch

from src.config import Config
from src.duo import DuoOutcome, DuoResult
from src.main import _acquire_saml_page, _finish_connect, _report_auth_failure, _wait_for_new_tab
from src.vpn import VpnState


def _browser(pages):
    browser = MagicMock()
    browser.pages.return_value = pages
    return browser


def _page(url: str, has_form: bool = False):
    page = MagicMock()
    page.url = url
    first = MagicMock()
    page.locator.return_value.first = first
    if not has_form:
        from playwright.sync_api import TimeoutError as PT
        first.wait_for.side_effect = PT("no form")
    return page


class TestWaitForNewTab:
    def test_picks_the_idp_tab(self):
        cfg = Config(ping_host_regex=r"auth\.example\.com")
        blank = _page("about:blank")
        saml = _page("https://auth.example.com/idp/SSO.saml2?SAMLRequest=x")
        page = _wait_for_new_tab(_browser([blank, saml]), cfg, {"about:blank"}, timeout=2)
        assert page is saml

    def test_picks_the_duo_tab(self):
        """Duo counts too — the login can land straight on the Duo prompt."""
        cfg = Config()
        duo = _page("https://api-1234.duosecurity.com/frame/v4/auth")
        assert _wait_for_new_tab(_browser([duo]), cfg, set(), timeout=2) is duo

    def test_accepts_an_unknown_host_showing_a_login_form(self):
        """A stale ping_host_regex must not stall the login."""
        cfg = Config(ping_host_regex=r"auth\.company\.com")
        unknown = _page("https://sso.internal.example/login", has_form=True)
        assert _wait_for_new_tab(_browser([unknown]), cfg, set(), timeout=2) is unknown

    @patch("src.main.time.sleep")
    def test_ignores_tabs_that_were_already_open(self, _sleep):
        cfg = Config()
        existing = _page("https://intranet.example.com/home")
        assert _wait_for_new_tab(
            _browser([existing]), cfg, {"https://intranet.example.com/home"}, timeout=1
        ) is None

    @patch("src.main.time.sleep")
    def test_ignores_browser_chrome_pages(self, _sleep):
        cfg = Config()
        pages = [_page("about:blank"), _page("edge://newtab"), _page("chrome://new-tab-page")]
        assert _wait_for_new_tab(_browser(pages), cfg, set(), timeout=1) is None

    @patch("src.main.time.sleep")
    def test_falls_back_to_the_only_new_tab(self, _sleep):
        """Unrecognised host with no visible form yet is still better than nothing."""
        cfg = Config()
        odd = _page("https://vpn.example.com/+CSCOE+/saml/sp/login")
        assert _wait_for_new_tab(_browser([odd]), cfg, set(), timeout=1) is odd


class TestAcquireSamlPage:
    @patch("src.main.capture_saml_url")
    def test_uses_the_tab_when_it_lands_in_our_browser(self, mock_capture):
        cfg = Config(vpn_host="vpn.example.com", ping_host_regex=r"auth\.example\.com")
        saml = _page("https://auth.example.com/idp/SSO.saml2")
        page = _acquire_saml_page(_browser([saml]), cfg, set())
        assert page is saml
        mock_capture.assert_not_called()

    @patch("src.main.time.sleep")
    @patch("src.main.capture_saml_url", return_value="https://auth.example.com/idp/SSO.saml2?x=1")
    def test_recovers_the_url_from_another_browser(self, mock_capture, _sleep):
        """The reported failure: the SAML page opened in a browser we don't drive."""
        cfg = Config(vpn_host="vpn.example.com", ping_page_timeout=1)
        browser = _browser([])
        recovered = _page("https://auth.example.com/idp/SSO.saml2?x=1")
        browser.open_url.return_value = recovered
        assert _acquire_saml_page(browser, cfg, set()) is recovered
        browser.open_url.assert_called_once_with("https://auth.example.com/idp/SSO.saml2?x=1")

    @patch("src.main.time.sleep")
    @patch("src.main.capture_saml_url", return_value=None)
    def test_falls_back_to_the_gateway_url(self, mock_capture, _sleep):
        cfg = Config(vpn_host="vpn.example.com", ping_page_timeout=1)
        browser = _browser([])
        gateway = _page("https://vpn.example.com/", has_form=True)
        browser.open_url.return_value = gateway
        assert _acquire_saml_page(browser, cfg, set()) is gateway
        browser.open_url.assert_called_once_with("https://vpn.example.com/")

    @patch("src.main.time.sleep")
    @patch("src.main.capture_saml_url", return_value=None)
    def test_returns_none_when_nothing_can_be_opened(self, mock_capture, _sleep):
        cfg = Config(vpn_host="vpn.example.com", ping_page_timeout=1)
        browser = _browser([])
        browser.open_url.side_effect = RuntimeError("no browser")
        assert _acquire_saml_page(browser, cfg, set()) is None


class TestFinishConnect:
    @patch("src.main.click_connect")
    def test_does_not_reclick_while_connecting(self, mock_click):
        vpn = MagicMock()
        vpn.state.return_value = VpnState.CONNECTING
        _finish_connect(vpn, Config())
        mock_click.assert_not_called()

    @patch("src.main.click_connect")
    def test_does_not_reclick_when_connected(self, mock_click):
        vpn = MagicMock()
        vpn.state.return_value = VpnState.CONNECTED
        _finish_connect(vpn, Config())
        mock_click.assert_not_called()

    @patch("src.main.click_connect")
    def test_clicks_connect_when_anyconnect_went_idle(self, mock_click):
        """Duo approved but the GUI dropped back to 'Ready to connect'."""
        vpn = MagicMock()
        vpn.state.return_value = VpnState.DISCONNECTED
        _finish_connect(vpn, Config())
        mock_click.assert_called_once()


class TestReportAuthFailure:
    def test_denied_message(self, capsys):
        _report_auth_failure(DuoOutcome(DuoResult.DENIED, "denied on device"))
        assert "denied" in capsys.readouterr().err.lower()

    def test_timeout_message(self, capsys):
        _report_auth_failure(DuoOutcome(DuoResult.TIMEOUT, "no approval within 120s"))
        assert "not approved in time" in capsys.readouterr().err
