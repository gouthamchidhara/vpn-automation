"""Fallback: recover the SAML URL AnyConnect handed to another browser.

If the URL-handler override in `url_handler` does not take effect (locked-down
registry, a browser that re-registers itself mid-run), AnyConnect's SAML tab
opens in the user's normal browser and our automation never sees it. The URL
is still recoverable: ShellExecute launches the browser with the URL as a
command-line argument, so it shows up in the process table.

We poll for it and then load that exact URL in the browser we control. The
stray tab in the other browser is harmless — only one of them completes the
handshake, and the callback goes back to AnyConnect either way.
"""
from __future__ import annotations
import re
import subprocess
import sys
import time

from src.logging_config import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BROWSER_PROCESSES = ("chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "brave.exe")

_URL_RE = re.compile(r'https?://[^\s"\']+')

_PS_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"{filter}\" "
    "| Select-Object -ExpandProperty CommandLine"
)


def _process_command_lines() -> list[str]:
    """Command lines of every running browser process (Windows only)."""
    if not IS_WINDOWS:
        return []
    proc_filter = " or ".join(f"Name='{name}'" for name in BROWSER_PROCESSES)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS_QUERY.format(filter=proc_filter)],
            capture_output=True, text=True, timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Process scan failed (non-fatal): %s", exc)
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def extract_saml_url(command_lines: list[str], pattern: re.Pattern[str]) -> str | None:
    """Return the first URL in any command line that matches the SAML pattern."""
    for line in command_lines:
        for url in _URL_RE.findall(line):
            url = url.rstrip('",\'')
            if pattern.search(url):
                return url
    return None


def capture_saml_url(pattern: re.Pattern[str], timeout: int = 30, poll: float = 1.0) -> str | None:
    """Poll browser command lines until a SAML URL appears, or give up."""
    if not IS_WINDOWS:
        return None
    log.info("Scanning browser processes for the SAML URL (up to %ds)...", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = extract_saml_url(_process_command_lines(), pattern)
        if url:
            log.info("Recovered the SAML URL from a browser process.")
            log.debug("SAML URL: %s", url)
            return url
        time.sleep(poll)
    log.warning("No SAML URL found in any browser process within %ds.", timeout)
    return None
