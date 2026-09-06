"""Tests for saml.py — mock Playwright Page for form fill + Duo wait."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from src.config import Selectors
from src.saml import fill_username, fill_password, wait_for_duo, wait_for_callback, saml_login


def _make_page(url: str = "https://auth.pingone.com/sso") -> MagicMock:
    page = MagicMock()
    page.url = url
    page.is_closed.return_value = False
    return page


def _make_selectors() -> Selectors:
    return Selectors(
        username_input="#user",
        password_input="#pass",
        submit_btn="button[type='submit']",
        duo_iframe="iframe#duo_iframe",
    )


class TestFillUsername:
    def test_fills_and_submits(self):
        page = _make_page()
        el = MagicMock()
        submit = MagicMock()
        page.wait_for_selector.side_effect = [el, submit]
        fill_username(page, "alice", _make_selectors(), timeout=5)
        el.fill.assert_called_once_with("alice")
        submit.click.assert_called_once()

    def test_raises_on_missing_field(self):
        page = _make_page()
        page.wait_for_selector.return_value = None
        with pytest.raises(RuntimeError, match="Username field not found"):
            fill_username(page, "alice", _make_selectors(), timeout=1)


class TestFillPassword:
    def test_fills_and_submits(self):
        page = _make_page()
        el = MagicMock()
        submit = MagicMock()
        page.wait_for_selector.side_effect = [el, submit]
        fill_password(page, "hunter2", _make_selectors(), timeout=5)
        el.fill.assert_called_once_with("hunter2")
        submit.click.assert_called_once()

    def test_raises_on_missing_field(self):
        page = _make_page()
        page.wait_for_selector.return_value = None
        with pytest.raises(RuntimeError, match="Password field not found"):
            fill_password(page, "hunter2", _make_selectors(), timeout=1)


class TestWaitForDuo:
    @patch("src.saml.time")
    def test_no_duo_iframe_returns_true(self, mock_time):
        """No Duo iframe detected → assumes no MFA required."""
        from playwright.sync_api import TimeoutError as PT
        page = _make_page()
        page.wait_for_selector.side_effect = PT("timeout")
        result = wait_for_duo(page, _make_selectors(), timeout=5)
        assert result is True

    @patch("src.saml.time")
    def test_duo_approved_via_url_change(self, mock_time):
        """Duo iframe found, then page redirects to callback."""
        page = _make_page("https://auth.pingone.com/duo")
        duo_el = MagicMock()
        page.wait_for_selector.return_value = duo_el
        mock_time.monotonic.side_effect = [0, 0, 2, 4]  # before deadline
        page.url = "https://127.0.0.1:4431/authcomplete"
        result = wait_for_duo(page, _make_selectors(), timeout=10)
        assert result is True

    @patch("src.saml.time")
    def test_duo_timeout(self, mock_time):
        """Duo not approved in time → returns False."""
        page = _make_page("https://auth.pingone.com/duo")
        duo_el = MagicMock()
        page.wait_for_selector.return_value = duo_el
        mock_time.monotonic.side_effect = [0, 100, 200]  # past deadline
        result = wait_for_duo(page, _make_selectors(), timeout=1)
        assert result is False


class TestWaitForCallback:
    @patch("src.saml.time")
    def test_callback_detected(self, mock_time):
        page = _make_page("https://127.0.0.1:4431/authcomplete")
        mock_time.monotonic.side_effect = [0, 0]
        result = wait_for_callback(page, timeout=5)
        assert result is True

    @patch("src.saml.time")
    def test_callback_timeout(self, mock_time):
        page = _make_page("https://auth.pingone.com/duo")
        mock_time.monotonic.side_effect = [0, 100]
        result = wait_for_callback(page, timeout=1)
        assert result is False

    @patch("src.saml.time")
    def test_page_closed_means_success(self, mock_time):
        page = _make_page()
        page.is_closed.return_value = True
        mock_time.monotonic.side_effect = [0, 0]
        result = wait_for_callback(page, timeout=5)
        assert result is True


class TestSamlLogin:
    @patch("src.saml.wait_for_callback", return_value=True)
    @patch("src.saml.wait_for_duo", return_value=True)
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_full_success(self, mock_fu, mock_fp, mock_duo, mock_cb):
        page = _make_page()
        sel = _make_selectors()
        result = saml_login(page, "alice", "hunter2", sel, duo_timeout=10, callback_timeout=5)
        assert result is True
        mock_fu.assert_called_once()
        mock_fp.assert_called_once()

    @patch("src.saml.wait_for_duo", return_value=False)
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_duo_failure(self, mock_fu, mock_fp, mock_duo):
        page = _make_page()
        sel = _make_selectors()
        result = saml_login(page, "alice", "hunter2", sel, duo_timeout=1)
        assert result is False
