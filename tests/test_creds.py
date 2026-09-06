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

    @patch("src.creds.keyring")
    def test_get_or_setup_existing(self, mock_kr):
        mock_kr.get_password.return_value = "existing_pw"
        user, pw = creds.get_or_setup("alice")
        assert user == "alice"
        assert pw == "existing_pw"

    @patch("src.creds.keyring")
    @patch("builtins.input", return_value="bob")
    @patch("getpass.getpass", return_value="newpw")
    def test_get_or_setup_creates_new(self, mock_pass, mock_input, mock_kr):
        mock_kr.get_password.return_value = None
        user, pw = creds.get_or_setup("bob")
        assert user == "bob"
        assert pw == "newpw"
        mock_kr.set_password.assert_called_once_with("vpn-auto-login", "bob", "newpw")
