"""Detect the OS default browser.

AnyConnect opens SAML links via Windows ShellExecute, which always launches
the user's DEFAULT browser. Driving that same binary (on our own temp profile)
is what lets `url_handler` route the SAML tab into the window we control, so
the default browser's exe — not a hardcoded Edge path — is the right one to
automate.

Note: we deliberately do NOT rewrite the UserChoice key to change which
browser is the default. Windows 10/11 protect it with a per-user hash, so
writing ProgId alone either gets reverted or leaves the user with a broken
association. `url_handler` overrides the launch command of the existing
default instead.
"""
from __future__ import annotations
import os
import re
import sys

from src.logging_config import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

_REG_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"

# Browsers we can drive over CDP
_CHROMIUM_EXES = ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe")


def _get_prog_id() -> str | None:
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        return None


def _get_exe_from_prog_id(prog_id: str) -> str | None:
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, fr"{prog_id}\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
        match = re.match(r'^"([^"]+)"', command) or re.match(r"^(\S+)", command)
        return match.group(1) if match else None
    except OSError:
        return None


def get_default_browser_exe() -> str | None:
    """Return the exe path of the current default browser, or None."""
    prog_id = _get_prog_id()
    return _get_exe_from_prog_id(prog_id) if prog_id else None


def is_chromium(exe_path: str) -> bool:
    """True if the exe is a Chromium-based browser we can drive over CDP."""
    return os.path.basename(exe_path).lower() in _CHROMIUM_EXES


def resolve_browser_exe(configured_exe: str, prefer_default: bool = True) -> str:
    """Pick the browser to automate: the OS default when usable, else config.

    Falling back to the configured exe keeps things working when the default
    browser is Firefox (no CDP) or cannot be read from the registry.
    """
    if prefer_default:
        default_exe = get_default_browser_exe()
        if default_exe and is_chromium(default_exe) and os.path.exists(default_exe):
            log.info("Automating the default browser: %s", os.path.basename(default_exe))
            return default_exe
        if default_exe:
            log.info("Default browser (%s) can't be automated — using %s instead.",
                     os.path.basename(default_exe), os.path.basename(configured_exe))
    return configured_exe
