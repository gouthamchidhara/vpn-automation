"""Tests for vpn.py state parser."""
import pytest
from src.vpn import parse_state, VpnState


class TestParseState:
    def test_connected(self):
        assert parse_state(">> state: Connected") == VpnState.CONNECTED

    def test_disconnected(self):
        assert parse_state(">> state: Disconnected") == VpnState.DISCONNECTED

    def test_connecting(self):
        assert parse_state(">> state: Connecting") == VpnState.CONNECTING

    def test_case_insensitive(self):
        assert parse_state("STATE: CONNECTED") == VpnState.CONNECTED
        assert parse_state("State: Disconnected") == VpnState.DISCONNECTED

    def test_unknown(self):
        assert parse_state("") == VpnState.UNKNOWN
        assert parse_state("some random output") == VpnState.UNKNOWN

    def test_connected_heuristic(self):
        """'connected' without 'disconnected' should be Connected."""
        assert parse_state("VPN is connected now") == VpnState.CONNECTED

    def test_disconnected_heuristic(self):
        assert parse_state("You are disconnected") == VpnState.DISCONNECTED
