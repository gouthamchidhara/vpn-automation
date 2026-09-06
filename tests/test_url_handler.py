"""Tests for url_handler.py — the command we register for the default browser."""
from unittest.mock import patch
from src.url_handler import BrowserUrlHijack


def _hijack() -> BrowserUrlHijack:
    return BrowserUrlHijack(
        browser_exe=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        user_data_dir=r"C:\Users\868977\AppData\Local\Temp\vpn-auto-login-abc",
        cdp_port=9222,
    )


class TestCommand:
    def test_points_at_our_profile_and_debug_port(self):
        """Same profile dir as our instance ⇒ the URL opens as a tab we control."""
        command = _hijack().command
        assert command.startswith('"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"')
        assert '--user-data-dir="C:\\Users\\868977\\AppData\\Local\\Temp\\vpn-auto-login-abc"' in command
        assert "--remote-debugging-port=9222" in command
        assert command.endswith('"%1"')

    def test_passes_the_url_placeholder(self):
        assert "%1" in _hijack().command


class TestLifecycle:
    @patch("src.url_handler.IS_WINDOWS", False)
    def test_install_is_a_no_op_off_windows(self):
        assert _hijack().install() is False

    @patch("src.url_handler.IS_WINDOWS", False)
    def test_context_manager_is_safe_off_windows(self):
        with _hijack() as hijack:
            assert hijack.command
        # remove() must not raise either

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler.cleanup_stale")
    @patch("src.url_handler.get_prog_id", return_value=None)
    def test_install_reports_failure_without_a_prog_id(self, mock_pid, mock_cleanup):
        assert _hijack().install() is False

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._restore")
    def test_remove_restores_every_installed_prog_id(self, mock_restore):
        hijack = _hijack()
        hijack._installed = ["ChromeHTML", "MSEdgeHTM"]
        hijack.remove()
        assert mock_restore.call_count == 2
        assert hijack._installed == []


class TestAssociationRepair:
    """The override could leave HKCU\\Software\\Classes\\<ProgId> present but
    empty, which shadows the machine-wide registration and stops Windows
    resolving https at all — AnyConnect then reports "problem navigating to
    the single sign-on URL" on every run until it is removed."""

    @patch("src.url_handler.IS_WINDOWS", False)
    def test_repair_is_a_no_op_off_windows(self):
        from src.url_handler import repair_browser_association
        assert repair_browser_association() == []

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status")
    def test_removes_an_empty_shadowing_key(self, mock_status, mock_read,
                                            mock_restore, mock_delete):
        from src.url_handler import repair_browser_association
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
        from src.url_handler import repair_browser_association
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
        from src.url_handler import repair_browser_association
        assert repair_browser_association() == []
        mock_delete.assert_not_called()

    @patch("src.url_handler.IS_WINDOWS", True)
    @patch("src.url_handler._delete_tree")
    @patch("src.url_handler._restore")
    @patch("src.url_handler._read_command", return_value=(None, False))
    @patch("src.url_handler.association_status", return_value=("none", ""))
    def test_untouched_machine_registration_needs_no_repair(self, mock_status, mock_read,
                                                            mock_restore, mock_delete):
        from src.url_handler import repair_browser_association
        assert repair_browser_association() == []
        mock_delete.assert_not_called()
