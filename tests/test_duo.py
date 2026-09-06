"""Tests for duo.py — push trigger, verification number, approval result."""
import re
import pytest
from unittest.mock import MagicMock, patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import DuoSelectors
from src.duo import (
    DuoResult,
    duo_scope,
    handle_duo,
    is_duo_page,
    read_verification_code,
    send_push,
    wait_for_approval,
)

DUO_RE = re.compile(r"duosecurity\.com", re.IGNORECASE)


def _scope(body_text: str = "", clickable: set[str] | None = None, code_text: str = ""):
    """A stand-in for a Page/Frame: only the locators in `clickable` are visible."""
    clickable = clickable or set()
    scope = MagicMock()
    cache: dict[str, MagicMock] = {}

    def locator(selector):
        if selector in cache:
            return cache[selector]
        result = MagicMock()
        first = MagicMock()
        result.first = first
        cache[selector] = result
        if selector == "body":
            first.inner_text.return_value = body_text
            return result
        if selector in clickable:
            first.inner_text.return_value = code_text
            return result
        first.wait_for.side_effect = PlaywrightTimeout("not visible")
        first.inner_text.return_value = ""
        return result

    scope.locator.side_effect = locator
    return scope


def _page(url: str, closed: bool = False):
    page = MagicMock()
    page.url = url
    page.is_closed.return_value = closed
    return page


class TestDetection:
    def test_is_duo_page(self):
        assert is_duo_page(_page("https://api-1234.duosecurity.com/frame"), DUO_RE)
        assert not is_duo_page(_page("https://auth.pingone.com/sso"), DUO_RE)

    def test_duo_scope_finds_universal_prompt(self):
        page = _page("https://api-1234.duosecurity.com/frame/v4/auth")
        assert duo_scope(page, DuoSelectors(), DUO_RE, timeout=1) is page

    def test_duo_scope_finds_legacy_iframe(self):
        page = _page("https://auth.pingone.com/sso")
        frame = MagicMock()
        frame.url = "https://api-1234.duosecurity.com/frame/prompt"
        page.frames = [frame]
        assert duo_scope(page, DuoSelectors(), DUO_RE, timeout=1) is frame

    @patch("src.duo.time.sleep")
    def test_duo_scope_none_when_absent(self, _sleep):
        page = _page("https://auth.pingone.com/sso")
        page.frames = []
        assert duo_scope(page, DuoSelectors(), DUO_RE, timeout=1) is None


class TestSendPush:
    def test_clicks_push_button(self):
        sel = DuoSelectors()
        scope = _scope(clickable={sel.push_btn})
        assert send_push(scope, sel, timeout=4) is True

    def test_auto_sent_push_needs_no_click(self):
        scope = _scope(body_text="Check for a Duo Push\nVerify it's you")
        assert send_push(scope, DuoSelectors(), timeout=4) is True

    def test_falls_back_to_other_options(self):
        sel = DuoSelectors()
        scope = _scope(clickable={sel.other_options, sel.push_option})
        assert send_push(scope, sel, timeout=4) is True

    def test_reports_when_no_push_available(self):
        assert send_push(_scope(body_text="Enter a passcode"), DuoSelectors(), timeout=4) is False


class TestVerificationCode:
    def test_reads_displayed_number(self):
        sel = DuoSelectors()
        scope = _scope(clickable={sel.verification_code}, code_text="42")
        assert read_verification_code(scope, sel, timeout=2) == "42"

    @patch("src.duo.time.sleep")
    def test_no_code_shown(self, _sleep):
        assert read_verification_code(_scope(), DuoSelectors(), timeout=1) is None


class TestWaitForApproval:
    def test_approved_when_page_leaves_duo(self):
        page = _page("https://127.0.0.1:29786/?token=abc")
        outcome = wait_for_approval(page, _scope(), DuoSelectors(), DUO_RE, timeout=5)
        assert outcome.result is DuoResult.APPROVED

    def test_approved_when_tab_closes(self):
        page = _page("https://api-1234.duosecurity.com/frame", closed=True)
        outcome = wait_for_approval(page, _scope(), DuoSelectors(), DUO_RE, timeout=5)
        assert outcome.result is DuoResult.APPROVED

    @patch("src.duo.time.sleep")
    def test_denied_on_phone(self, _sleep):
        page = _page("https://api-1234.duosecurity.com/frame")
        scope = _scope(body_text="Login request denied. Try again.")
        outcome = wait_for_approval(page, scope, DuoSelectors(), DUO_RE, timeout=5)
        assert outcome.result is DuoResult.DENIED

    @patch("src.duo.time.sleep")
    def test_push_expired(self, _sleep):
        page = _page("https://api-1234.duosecurity.com/frame")
        scope = _scope(body_text="Login request timed out")
        outcome = wait_for_approval(page, scope, DuoSelectors(), DUO_RE, timeout=5)
        assert outcome.result is DuoResult.TIMEOUT

    @patch("src.duo.time.sleep")
    @patch("src.duo.time.monotonic", side_effect=[0, 100])
    def test_timeout_without_approval(self, _mono, _sleep):
        page = _page("https://api-1234.duosecurity.com/frame")
        outcome = wait_for_approval(page, _scope(), DuoSelectors(), DUO_RE, timeout=1)
        assert outcome.result is DuoResult.TIMEOUT

    def test_clicks_trust_this_device(self):
        sel = DuoSelectors()
        page = _page("https://api-1234.duosecurity.com/frame")
        scope = _scope(clickable={sel.trust_browser_btn})
        with patch("src.duo.time.sleep"), \
             patch("src.duo.time.monotonic", side_effect=[0, 10, 100]):
            wait_for_approval(page, scope, sel, DUO_RE, timeout=60, trust_browser=True)
        scope.locator(sel.trust_browser_btn).first.click.assert_called()


class TestHandleDuo:
    @patch("src.duo.time.sleep")
    def test_not_required_when_no_prompt(self, _sleep):
        page = _page("https://auth.pingone.com/sso")
        page.frames = []
        outcome = handle_duo(page, DuoSelectors(), DUO_RE, detect_timeout=1)
        assert outcome.result is DuoResult.NOT_REQUIRED
        assert outcome.ok

    @patch("src.duo.wait_for_approval", return_value=MagicMock())
    @patch("src.duo.read_verification_code", return_value="27")
    @patch("src.duo.send_push", return_value=True)
    def test_announces_code_then_waits(self, mock_push, mock_code, mock_wait, capsys):
        page = _page("https://api-1234.duosecurity.com/frame")
        handle_duo(page, DuoSelectors(), DUO_RE, detect_timeout=1, approval_timeout=5)
        mock_push.assert_called_once()
        mock_wait.assert_called_once()
        assert "27" in capsys.readouterr().out
