"""Detect and temporarily override the OS default browser.

AnyConnect opens SAML links via Windows ShellExecute, which always launches
the user's DEFAULT browser — not whatever browser_exe happens to be in
config. If we launch a different browser for CDP control, the SAML tab opens
in an untracked process and our automation never sees it.

Solution: temporarily set Edge as the default browser before triggering the
VPN connect, then restore the original default on exit.
"""
from __future__ import annotations
import os
import re
import subprocess
import winreg

from src.logging_config import get_logger

log = get_logger(__name__)

_REG_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
_EDGE_PROG_ID = "MSEdgeHTM"

# Known browser prog IDs -> exe path commands in HKCR
_BROWSER_COMMANDS = {
    "ChromeHTML": r"Google\Chrome\Application\chrome.exe",
    "MSEdgeHTM":  r"Microsoft\Edge\Application\msedge.exe",
}


def _get_prog_id() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        return None


def _get_exe_from_prog_id(prog_id: str) -> str | None:
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


def set_default_browser(exe_path: str) -> bool:
    """Set the default HTTPS browser via registry.

    Uses the well-known prog ID for the given exe. Works on Windows 10/11
    but may be overridden by OS defaults app UI in some builds.
    Returns True if the registry write succeeded.
    """
    exe_name = os.path.basename(exe_path).lower()
    if "msedge" in exe_name:
        prog_id = "MSEdgeHTM"
    elif "chrome" in exe_name:
        prog_id = "ChromeHTML"
    else:
        log.error("Unsupported browser for default switch: %s", exe_path)
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProgId", 0, winreg.REG_SZ, prog_id)
        log.info("Set default browser to %s (ProgId=%s).", exe_name, prog_id)
        return True
    except OSError as exc:
        log.warning("Failed to set default browser: %s", exc)
        return False


def restore_default_browser(original_exe: str | None) -> None:
    """Restore the original default browser if we changed it."""
    if not original_exe:
        return
    exe_name = os.path.basename(original_exe).lower()
    if "msedge" in exe_name:
        # Already Edge — nothing to restore
        return
    set_default_browser(original_exe)
    log.info("Restored default browser to %s.", os.path.basename(original_exe))
