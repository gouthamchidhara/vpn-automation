"""Structured JSON logging with credential redaction."""
from __future__ import annotations
import json
import logging
import re
import sys
from datetime import datetime, timezone

# Patterns to redact from any log message
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(password|passwd|pwd|token|secret|cookie|saml\w*)[=:]\s*\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
]

REDACTED_MARKER = "[REDACTED]"


class RedactingFilter(logging.Filter):
    """Replaces sensitive values in log records with [REDACTED]."""

    def __init__(self, extra_patterns: list[str] | None = None):
        super().__init__()
        self._patterns = list(_SENSITIVE_PATTERNS)
        if extra_patterns:
            self._patterns.extend(re.compile(p, re.IGNORECASE) for p in extra_patterns)

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the main message
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        # Redact args if present
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (REDACTED_MARKER if self._is_sensitive_key(k) else self._redact(str(v))) if isinstance(v, str) else v
                               for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        return True

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        return bool(re.match(r"(?i)^(password|passwd|pwd|pw|token|secret|cookie|saml|auth)$", key))

    def _redact(self, text: str) -> str:
        for pat in self._patterns:
            text = pat.sub(REDACTED_MARKER, text)
        return text


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: int = logging.INFO, json_output: bool = True) -> None:
    """Configure root logger with redaction + optional JSON."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger."""
    return logging.getLogger(name)
