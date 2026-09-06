"""Tests for saml.py — mock Playwright Page for form fill + callback wait."""
import pytest
from unittest.mock import MagicMock, patch
from src.config import Selectors
from src.duo import DuoOutcome, DuoResult
from src.saml import fill_username, fill_password, login_error, wait_for_callback, saml_login


def _make_page(url: str = "https://auth.pingone.com/sso", password_visible: bool = False) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.is_closed.return_value = False
    locator = MagicMock()
    page.locator.return_value.first = locator
    if not password_visible:
        # locator.wait_for() raising means "not on screen"
        from playwright.sync_api import TimeoutError as PT
        locator.wait_for.side_effect = PT("not visible")
    return page


def _make_selectors() -> Selectors:
    return Selectors(
        username_input="#user",
        password_input="#pass",
        submit_btn="button[type='submit']",
        duo_iframe="iframe#duo_iframe",
    )


class TestFillUsername:
    def test_fills_and_advances_two_step_form(self):
        """Password not on screen yet → click through to the next step."""
        page = _make_page()
        el = MagicMock()
        page.wait_for_selector.return_value = el
        fill_username(page, "alice", _make_selectors(), timeout=5)
        el.fill.assert_called_once_with("alice")
        # submit falls back to Enter because the button locator is not visible
        page.keyboard.press.assert_called_once_with("Enter")

    def test_does_not_submit_single_page_form(self):
        """Both fields on one screen → don't submit before the password is typed."""
        page = _make_page(password_visible=True)
        el = MagicMock()
        page.wait_for_selector.return_value = el
        fill_username(page, "alice", _make_selectors(), timeout=5)
        el.fill.assert_called_once_with("alice")
        page.keyboard.press.assert_not_called()
        page.locator.return_value.first.click.assert_not_called()

    def test_raises_on_missing_field(self):
        page = _make_page()
        page.wait_for_selector.return_value = None
        with pytest.raises(RuntimeError, match="Username field not found"):
            fill_username(page, "alice", _make_selectors(), timeout=1)


class TestFillPassword:
    def test_fills_and_submits(self):
        page = _make_page(password_visible=True)
        el = MagicMock()
        page.wait_for_selector.return_value = el
        fill_password(page, "hunter2", _make_selectors(), timeout=5)
        el.fill.assert_called_once_with("hunter2")
        page.locator.return_value.first.click.assert_called_once()

    def test_raises_on_missing_field(self):
        page = _make_page()
        page.wait_for_selector.return_value = None
        with pytest.raises(RuntimeError, match="Password field not found"):
            fill_password(page, "hunter2", _make_selectors(), timeout=1)


class TestLoginError:
    def test_returns_error_text(self):
        page = _make_page(password_visible=True)
        page.locator.return_value.first.inner_text.return_value = "  Invalid password  "
        assert login_error(page, _make_selectors()) == "Invalid password"

    def test_returns_none_when_clean(self):
        page = _make_page()  # locator.wait_for raises → no error shown
        assert login_error(page, _make_selectors()) is None


class TestWaitForCallback:
    @patch("src.saml.time")
    def test_callback_detected(self, mock_time):
        page = _make_page("https://127.0.0.1:4431/authcomplete")
        mock_time.monotonic.side_effect = [0, 0]
        assert wait_for_callback(page, timeout=5) is True

    @patch("src.saml.time")
    def test_callback_timeout(self, mock_time):
        page = _make_page("https://auth.pingone.com/duo")
        mock_time.monotonic.side_effect = [0, 100]
        assert wait_for_callback(page, timeout=1) is False

    @patch("src.saml.time")
    def test_page_closed_means_success(self, mock_time):
        page = _make_page()
        page.is_closed.return_value = True
        mock_time.monotonic.side_effect = [0, 0]
        assert wait_for_callback(page, timeout=5) is True

    @patch("src.saml.time")
    def test_idp_url_is_not_a_callback(self, mock_time):
        """A page still on the IdP must not count as the AnyConnect callback."""
        page = _make_page("https://auth.pingone.com/sso/next")
        mock_time.monotonic.side_effect = [0, 0, 100]
        assert wait_for_callback(page, timeout=1) is False


class TestSamlLogin:
    @patch("src.saml.wait_for_callback", return_value=True)
    @patch("src.saml.handle_duo", return_value=DuoOutcome(DuoResult.APPROVED))
    @patch("src.saml.login_error", return_value=None)
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_full_success(self, mock_fu, mock_fp, mock_err, mock_duo, mock_cb):
        outcome = saml_login(_make_page(), "alice", "hunter2", _make_selectors(),
                             duo_timeout=10, callback_timeout=5)
        assert outcome.ok
        assert outcome.result is DuoResult.APPROVED
        mock_fu.assert_called_once()
        mock_fp.assert_called_once()

    @patch("src.saml.wait_for_callback")
    @patch("src.saml.handle_duo", return_value=DuoOutcome(DuoResult.DENIED, "denied on device"))
    @patch("src.saml.login_error", return_value=None)
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_duo_denied_stops_before_callback(self, mock_fu, mock_fp, mock_err, mock_duo, mock_cb):
        outcome = saml_login(_make_page(), "alice", "hunter2", _make_selectors(), duo_timeout=1)
        assert not outcome.ok
        assert outcome.result is DuoResult.DENIED
        mock_cb.assert_not_called()

    @patch("src.saml.handle_duo")
    @patch("src.saml.login_error", return_value="Invalid username or password")
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_bad_password_fails_before_duo(self, mock_fu, mock_fp, mock_err, mock_duo):
        outcome = saml_login(_make_page(), "alice", "wrong", _make_selectors())
        assert outcome.result is DuoResult.ERROR
        assert "Invalid username" in outcome.detail
        mock_duo.assert_not_called()

    @patch("src.saml.wait_for_callback", return_value=False)
    @patch("src.saml.handle_duo", return_value=DuoOutcome(DuoResult.APPROVED))
    @patch("src.saml.login_error", return_value=None)
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_missing_callback_is_a_failure(self, mock_fu, mock_fp, mock_err, mock_duo, mock_cb):
        outcome = saml_login(_make_page(), "alice", "hunter2", _make_selectors())
        assert not outcome.ok
        assert outcome.result is DuoResult.ERROR

    @patch("src.saml.wait_for_callback")
    @patch("src.saml.handle_duo", return_value=DuoOutcome(DuoResult.NOT_REQUIRED))
    @patch("src.saml.login_error", side_effect=[None, "Password expired"])
    @patch("src.saml.fill_password")
    @patch("src.saml.fill_username")
    def test_late_idp_error_without_duo(self, mock_fu, mock_fp, mock_err, mock_duo, mock_cb):
        """No Duo prompt can mean the password was rejected, not that MFA passed."""
        outcome = saml_login(_make_page(), "alice", "old", _make_selectors())
        assert outcome.result is DuoResult.ERROR
        assert outcome.detail == "Password expired"
        mock_cb.assert_not_called()
