"""Tests for gui.py — Connect button matching (must never hit Disconnect)."""
import pytest
from src.gui import is_connect_text


class TestIsConnectText:
    @pytest.mark.parametrize("label", ["Connect", "&Connect", " connect ", "Connect to VPN"])
    def test_matches_connect_labels(self, label):
        assert is_connect_text(label) is True

    @pytest.mark.parametrize("label", ["Disconnect", "&Disconnect", "Reconnect", "Connection", ""])
    def test_rejects_everything_else(self, label):
        assert is_connect_text(label) is False
