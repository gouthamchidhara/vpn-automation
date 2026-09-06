"""Tests for creds.py (mocked keyring)."""
import pytest
from unittest.mock import patch, MagicMock
from src import creds


class TestCreds:
    @patch("src.creds.keyring")
    def test_get_password_found(self, mock_kr):
        mock_kr.get_password.return_value = "hunter2"
        result = creds.get_password("alice")
        assert result == "hunter2"
        mock_kr.get_password.assert_called_once_with("vpn-auto-login", "alice")

    @patch("src.creds.keyring")
    def test_get_password_not_found(self, mock_kr):
        mock_kr.get_password.return_value = None
        result = creds.get_password("nobody")
        assert result is None

    @patch("src.creds.keyring")
    def test_store_password(self, mock_kr):
        creds.store_password("alice", "hunter2")
        mock_kr.set_password.assert_called_once_with("vpn-auto-login", "alice", "hunter2")


class TestResolve:
    @patch("src.creds.keyring")
    def test_uses_given_username_without_prompting(self, mock_kr):
        mock_kr.get_password.return_value = "existing_pw"
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            user, pw = creds.resolve("alice")
        assert (user, pw) == ("alice", "existing_pw")

    @patch("src.creds.keyring")
    def test_remembered_username_never_prompts(self, mock_kr):
        """The reported bug: a stored login must not ask for a username again."""
        mock_kr.get_password.return_value = "stored_pw"
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             patch("getpass.getpass", side_effect=AssertionError("must not prompt")):
            user, pw = creds.resolve(None, last_username="868977")
        assert (user, pw) == ("868977", "stored_pw")
        mock_kr.get_password.assert_called_once_with("vpn-auto-login", "868977")

    @patch("src.creds.keyring")
    @patch("getpass.getpass", return_value="newpw")
    def test_asks_once_when_password_missing_then_stores(self, mock_pass, mock_kr):
        mock_kr.get_password.return_value = None
        with patch("builtins.input", side_effect=AssertionError("must not ask for username")):
            user, pw = creds.resolve(None, last_username="868977")
        assert (user, pw) == ("868977", "newpw")
        mock_kr.set_password.assert_called_once_with("vpn-auto-login", "868977", "newpw")

    @patch("src.creds.keyring")
    @patch("builtins.input", return_value="bob")
    @patch("getpass.getpass", return_value="newpw")
    def test_prompts_for_username_only_when_nothing_known(self, mock_pass, mock_input, mock_kr):
        mock_kr.get_password.return_value = None
        user, pw = creds.resolve(None, last_username="")
        assert (user, pw) == ("bob", "newpw")

    @patch("src.creds.keyring")
    def test_never_asks_about_overwriting_on_a_normal_run(self, mock_kr):
        """No 'Credentials already exist. Overwrite?' outside of --setup."""
        mock_kr.get_password.return_value = "stored_pw"
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            creds.resolve("868977")
        mock_kr.set_password.assert_not_called()

    @patch("src.creds.keyring")
    def test_get_or_setup_alias(self, mock_kr):
        mock_kr.get_password.return_value = "existing_pw"
        assert creds.get_or_setup("alice") == ("alice", "existing_pw")


class TestSetup:
    @patch("src.creds.keyring")
    @patch("builtins.input", side_effect=["alice", "n"])
    def test_setup_keeps_existing_on_no(self, mock_input, mock_kr):
        mock_kr.get_password.return_value = "old_pw"
        user, pw = creds.setup()
        assert (user, pw) == ("alice", "old_pw")
        mock_kr.set_password.assert_not_called()

    @patch("src.creds.keyring")
    @patch("builtins.input", side_effect=["alice", "y"])
    @patch("getpass.getpass", return_value="fresh")
    def test_setup_overwrites_on_yes(self, mock_pass, mock_input, mock_kr):
        mock_kr.get_password.return_value = "old_pw"
        user, pw = creds.setup()
        assert (user, pw) == ("alice", "fresh")
        mock_kr.set_password.assert_called_once_with("vpn-auto-login", "alice", "fresh")
