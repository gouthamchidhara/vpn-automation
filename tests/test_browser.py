"""Tests for browser.py — mock subprocess/Popen and Playwright."""
import pytest
from unittest.mock import MagicMock, patch, call
from src.browser import DebugBrowser


class TestDebugBrowser:
    def test_init_defaults(self, tmp_path):
        b = DebugBrowser(
            browser_exe=r"C:\msedge.exe",
            cdp_port=9222,
            user_data_dir=str(tmp_path / "ud"),
        )
        assert b.cdp_port == 9222
        assert b.cdp_url == "http://127.0.0.1:9222"
        assert b.user_data_dir == str(tmp_path / "ud")

    def test_init_default_tempdir(self):
        """user_data_dir auto-creates a temp dir for reliable CDP binding."""
        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        assert b.user_data_dir
        assert "vpn-auto-login-" in b.user_data_dir

    @patch("src.browser.subprocess.run")
    @patch("src.browser.subprocess.Popen")
    @patch("src.browser.DebugBrowser._wait_for_port", return_value=True)
    def test_launch(self, mock_wait, mock_popen, mock_run):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        b = DebugBrowser(browser_exe=r"C:\msedge.exe", cdp_port=9222)
        b.launch()
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "--remote-debugging-port=9222" in cmd
        assert b._process == mock_proc

    @patch("src.browser.sync_playwright")
    def test_connect(self, mock_pw_mod):
        mock_pw = MagicMock()
        mock_pw_mod.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser
        mock_ctx = MagicMock()
        mock_browser.contexts = [mock_ctx]

        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        ctx = b.connect()

        mock_pw.chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:9222")
        assert ctx == mock_ctx

    @patch("src.browser.sync_playwright")
    def test_find_page_by_url(self, mock_pw_mod):
        mock_pw = MagicMock()
        mock_pw_mod.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser
        mock_ctx = MagicMock()
        mock_browser.contexts = [mock_ctx]

        mock_page = MagicMock()
        mock_page.url = "https://auth.pingone.com/sso"
        mock_ctx.pages = [mock_page]

        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        b.connect()
        result = b.find_page_by_url(r"pingone\.com", timeout=1)
        assert result == mock_page

    @patch("src.browser.sync_playwright")
    def test_find_page_by_url_no_match(self, mock_pw_mod):
        mock_pw = MagicMock()
        mock_pw_mod.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser
        mock_ctx = MagicMock()
        mock_browser.contexts = [mock_ctx]
        mock_page = MagicMock()
        mock_page.url = "https://google.com"
        mock_ctx.pages = [mock_page]

        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        b.connect()
        result = b.find_page_by_url(r"pingone\.com", timeout=1)
        assert result is None

    @patch("src.browser.subprocess.run")
    @patch("src.browser.subprocess.Popen")
    @patch("src.browser.DebugBrowser._wait_for_port", return_value=True)
    def test_close_terminates_process(self, mock_wait, mock_popen, mock_run):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        b.launch()
        b.close()
        mock_proc.terminate.assert_called_once()

    def test_close_safe_without_launch(self):
        """close() should not raise even if launch() was never called."""
        b = DebugBrowser(browser_exe=r"C:\msedge.exe")
        b.close()  # should not raise
