"""Tests for banner.py — mock pywinauto Desktop."""
import pytest
from unittest.mock import patch, MagicMock
from src.banner import accept_banner


class TestAcceptBanner:
    @patch("src.banner.time")
    def test_no_desktop_module(self, mock_time):
        """If pywinauto not installed, returns False gracefully."""
        mock_time.monotonic.side_effect = [0, 100]
        with patch.dict("sys.modules", {"pywinauto": None}):
            # ImportError on import → returns False
            result = accept_banner(timeout=1)
            assert result is False

    @patch("src.banner.time")
    @patch("pywinauto.Desktop")
    def test_finds_and_clicks_accept(self, mock_desktop_cls, mock_time):
        mock_btn = MagicMock()
        mock_btn.window_text.return_value = "Accept"
        mock_win = MagicMock()
        mock_win.window_text.return_value = "AnyConnect Banner"
        mock_win.descendants.return_value = [mock_btn]
        mock_desktop_cls.return_value.windows.return_value = [mock_win]
        mock_time.monotonic.side_effect = [0, 0]

        result = accept_banner(timeout=5)
        assert result is True
        mock_btn.click.assert_called_once()

    @patch("src.banner.time")
    @patch("pywinauto.Desktop")
    def test_no_banner_dialog(self, mock_desktop_cls, mock_time):
        """No matching window → returns False after timeout."""
        mock_win = MagicMock()
        mock_win.window_text.return_value = "Some Other Window"
        mock_desktop_cls.return_value.windows.return_value = [mock_win]
        mock_time.monotonic.side_effect = [0, 100]

        result = accept_banner(timeout=1)
        assert result is False
