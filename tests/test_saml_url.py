"""Tests for saml_url.py — recovering the SAML URL from a browser command line."""
from unittest.mock import patch

from src.config import Config
from src.saml_url import SamlUrlWatcher, extract_saml_url

PATTERN = Config().saml_url_pattern()

SHELLEXEC_LINE = (
    r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
    r'--single-argument https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123'
)


class TestExtractSamlUrl:
    def test_finds_url_in_shellexecute_command_line(self):
        url = extract_saml_url([SHELLEXEC_LINE], PATTERN)
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

    def test_skips_urls_in_the_ignore_set(self):
        """A URL left over from an earlier attempt is not this run's."""
        stale = "https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123"
        assert extract_saml_url([SHELLEXEC_LINE], PATTERN, ignore={stale}) is None

    def test_returns_none_when_absent(self):
        assert extract_saml_url(["chrome.exe --type=gpu-process"], PATTERN) is None

    def test_returns_none_for_empty_input(self):
        assert extract_saml_url([], PATTERN) is None


class TestSamlUrlWatcher:
    @patch("src.saml_url._process_command_lines", return_value=[SHELLEXEC_LINE])
    def test_baseline_hides_urls_open_before_connect(self, mock_scan):
        """The failed previous attempt's tab must not be captured as this one."""
        watcher = SamlUrlWatcher(PATTERN)
        watcher.snapshot_baseline()
        assert watcher.poll_once() is None

    @patch("src.saml_url._process_command_lines", return_value=[SHELLEXEC_LINE])
    def test_poll_finds_a_new_url(self, mock_scan):
        watcher = SamlUrlWatcher(PATTERN)
        assert watcher.poll_once() == "https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123"
        assert watcher.url == watcher.poll_once()

    def test_reader_thread_captures_the_first_match(self):
        watcher = SamlUrlWatcher(PATTERN)
        watcher._process = _FakeProcess([
            "chrome.exe --type=renderer\n",
            SHELLEXEC_LINE + "\n",
            "chrome.exe https://other.example.com/idp/second\n",
        ])
        watcher._read_output()
        assert watcher.url == "https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123"

    @patch("src.saml_url.IS_WINDOWS", False)
    def test_start_is_a_no_op_off_windows(self):
        assert SamlUrlWatcher(PATTERN).start() is False

    def test_stop_is_safe_without_start(self):
        SamlUrlWatcher(PATTERN).stop()


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)

    def poll(self):
        return 0


class TestFallbackUrl:
    def test_any_new_url_when_the_pattern_misses(self):
        """An unrecognised SSO URL shape still beats guessing at the gateway."""
        watcher = SamlUrlWatcher(PATTERN)
        watcher._record(["chrome.exe --single-argument https://vpn.corp.example/auth?x=1"])
        assert watcher.url is None
        assert watcher.fallback_url == "https://vpn.corp.example/auth?x=1"

    def test_start_pages_are_not_treated_as_the_sso_url(self):
        watcher = SamlUrlWatcher(PATTERN)
        watcher._record(["msedge.exe https://ntp.msn.com/edge/ntp?locale=en"])
        assert watcher.fallback_url is None

    def test_a_pattern_match_beats_a_fallback(self):
        watcher = SamlUrlWatcher(PATTERN)
        watcher._record(["chrome.exe https://vpn.corp.example/auth?x=1"])
        watcher._record([SHELLEXEC_LINE])
        assert watcher.url == "https://auth.pingone.com/idp/SSO.saml2?SAMLRequest=abc123"
        assert watcher.fallback_url == watcher.url
