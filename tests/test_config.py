"""Tests for config.py — load/save, defaults, JSON override."""
import json
import pytest
from pathlib import Path
from src.config import Config, Selectors, load_config, save_config


class TestSelectors:
    def test_defaults(self):
        s = Selectors()
        assert "identifier" in s.username_input
        assert "password" in s.password_input
        assert "submit" in s.submit_btn
        assert "duo" in s.duo_iframe


class TestConfig:
    def test_defaults(self):
        cfg = Config()
        assert cfg.cdp_port == 9222
        assert cfg.duo_wait_timeout == 120
        assert cfg.connected_timeout == 60
        assert "Cisco" in cfg.vpncli_path
        assert "Cisco" in cfg.vpnui_path
        assert "Edge" in cfg.browser_exe or "msedge" in cfg.browser_exe

    def test_ping_host_pattern(self):
        cfg = Config(ping_host_regex=r"pingone\.com|auth\.example\.com")
        pat = cfg.ping_host_pattern()
        assert pat.search("https://login.pingone.com/sso")
        assert pat.search("https://auth.example.com/saml")
        assert not pat.search("https://google.com")

    def test_ping_host_pattern_case_insensitive(self):
        cfg = Config(ping_host_regex=r"pingone\.com")
        pat = cfg.ping_host_pattern()
        assert pat.search("https://PINGONE.COM/sso")


class TestLoadConfig:
    def test_load_nonexistent_returns_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nope.json")
        assert cfg.vpn_host == ""
        assert cfg.cdp_port == 9222

    def test_load_existing(self, tmp_path):
        data = {
            "vpn_host": "vpn.test.com",
            "cdp_port": 9333,
            "ping_host_regex": "auth\\.test\\.com",
            "selectors": {
                "username_input": "#user",
                "password_input": "#pass",
            },
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cfg = load_config(p)
        assert cfg.vpn_host == "vpn.test.com"
        assert cfg.cdp_port == 9333
        assert cfg.ping_host_regex == "auth\\.test\\.com"
        assert cfg.selectors.username_input == "#user"
        assert cfg.selectors.password_input == "#pass"
        # unspecified fields keep defaults
        assert cfg.duo_wait_timeout == 120


class TestSaveConfig:
    def test_save_and_reload(self, tmp_path):
        cfg = Config(vpn_host="vpn.save.test", cdp_port=9444)
        p = tmp_path / "saved.json"
        save_config(cfg, p)
        assert p.exists()
        loaded = load_config(p)
        assert loaded.vpn_host == "vpn.save.test"
        assert loaded.cdp_port == 9444
