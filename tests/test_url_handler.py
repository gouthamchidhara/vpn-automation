"""Tests for url_handler.py — keeping the https association healthy."""
from unittest.mock import patch
from src.url_handler import association_status, repair_browser_association




class TestAssociationRepair:
    """The override could leave HKCU\\Software\\Classes\\<ProgId> present but
    empty, which shadows the machine-wide registration and stops Windows
    resolving https at all — AnyConnect then reports "problem navigating to
    the single sign-on URL" on every run until it is removed."""

    @patch("src.url_handler.IS_WINDOWS", False)
    def test_repair_is_a_no_op_off_windows(self):
        assert repair_browser_association() == []

    @patch("src.url_handler.IS_WINDOWS", False)
    def test_status_is_none_off_windows(self):
        assert association_status("ChromeHTML") == ("none", "")

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status")
    def test_removes_an_empty_shadowing_key(self, mock_status, mock_read,
                                            mock_restore, mock_delete):
        mock_status.side_effect = lambda pid: (("broken", "") if pid == "ChromeHTML"
                                               else ("none", ""))
        assert repair_browser_association() == ["ChromeHTML"]
        mock_delete.assert_called_once_with("ChromeHTML")

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status")
    def test_removes_a_command_still_pointing_at_our_profile(self, mock_status, mock_read,
                                                             mock_restore, mock_delete):
        ours = (r'"chrome.exe" --user-data-dir="C:\Temp\vpn-auto-login-x" "%1"')
        mock_status.side_effect = lambda pid: (("ours", ours) if pid == "MSEdgeHTM"
                                               else ("none", ""))
        assert repair_browser_association() == ["MSEdgeHTM"]
        mock_delete.assert_called_once_with("MSEdgeHTM")

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status", return_value=("user", r'"chrome.exe" -- "%1"'))
    def test_leaves_a_real_per_user_registration_alone(self, mock_status, mock_read,
                                                       mock_restore, mock_delete):
        assert repair_browser_association() == []
        mock_delete.assert_not_called()

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status", return_value=("none", ""))
    def test_untouched_machine_registration_needs_no_repair(self, mock_status, mock_read,
                                                            mock_restore, mock_delete):
        assert repair_browser_association() == []
        mock_delete.assert_not_called()
