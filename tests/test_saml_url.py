"""Tests for saml_url.py — recovering the SAML URL from a browser command line."""
import pytest
from src.config import Config
from src.saml_url import extract_saml_url

PATTERN = Config().saml_url_pattern()


class TestExtractSamlUrl:
    def test_finds_url_in_shellexecute_command_line(self):
        lines = [
            r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
            r'--single-argument https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123',
        ]
        url = extract_saml_url(lines, PATTERN)
        assert url == "https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123"

    def test_strips_trailing_quote(self):
        lines = ['chrome.exe "https://vpn.example.com/+CSCOE+/saml/sp/login"']
        assert extract_saml_url(lines, PATTERN) == "https://vpn.example.com/+CSCOE+/saml/sp/login"

    def test_ignores_unrelated_tabs(self):
        lines = [
            "chrome.exe https://news.example.com/story",
            "msedge.exe --type=renderer",
            "chrome.exe https://login.example.com/as/authorization.oauth2?x=1",
        ]
        assert extract_saml_url(lines, PATTERN) == "https://login.example.com/as/authorization.oauth2?x=1"

    def test_returns_none_when_absent(self):
        assert extract_saml_url(["chrome.exe --type=gpu-process"], PATTERN) is None

    def test_returns_none_for_empty_input(self):
        assert extract_saml_url([], PATTERN) is None
