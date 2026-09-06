"""Tests for logging redaction filter."""
import logging
import pytest
from src.logging_config import RedactingFilter


class TestRedactingFilter:
    def setup_method(self):
        self.f = RedactingFilter()

    def test_redacts_password(self):
        assert "[REDACTED]" in self.f._redact("password=hunter2")
        assert "[REDACTED]" in self.f._redact("password: mysecret123")

    def test_redacts_token(self):
        assert "[REDACTED]" in self.f._redact("token=abc123def456")
        assert "[REDACTED]" in self.f._redact("bearer eyJhbGciOiJIUzI1NiJ9.test.signature")

    def test_redacts_cookie(self):
        assert "[REDACTED]" in self.f._redact("cookie=session_abc123xyz")

    def test_redacts_saml(self):
        assert "[REDACTED]" in self.f._redact("saml_response=very_long_base64_blob")

    def test_leaves_normal_text(self):
        msg = "VPN connected successfully on port 443"
        assert self.f._redact(msg) == msg

    def test_filter_modifies_record(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="auth password=mysecret", args=None, exc_info=None
        )
        self.f.filter(record)
        assert "mysecret" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_filter_with_dict_args(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="login user=%(user)s pw=%(pw)s",
            args={"user": "alice", "pw": "secret123"},
            exc_info=None,
        )
        self.f.filter(record)
        assert "secret123" not in str(record.args)

    def test_custom_pattern(self):
        f = RedactingFilter(extra_patterns=[r"apikey=\S+"])
        assert "[REDACTED]" in f._redact("apikey=sk-1234567890")
