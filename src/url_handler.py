"""Temporarily route Windows' https:// handler into our automated browser.

AnyConnect opens the SAML page with ShellExecute, which launches the user's
DEFAULT browser with its DEFAULT profile. Our Playwright-controlled browser
runs on a separate temp profile (real profiles refuse to bind the CDP port),
so the SAML tab lands in a window we cannot see — the exact failure where
"Chrome opened the SAML page and nothing happened".

Chrome/Edge treat the profile directory as the single-instance key: launching
the same exe with the same --user-data-dir hands the URL to the already
running instance as a new tab. So we don't need to change which browser is
the default (the UserChoice hash makes that unreliable anyway) — we only
override the *command* registered for the current ProgId, under
HKCU\\Software\\Classes, which shadows the machine-wide registration:

    "chrome.exe" --user-data-dir=<temp> --remote-debugging-port=9222 "%1"

The original value is saved and restored on exit; a marker value lets a later
run clean up an override left behind by a crash.
"""
from __future__ import annotations
import sys

from src.logging_config import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

_USER_CHOICE_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\UserChoice"
_CLASSES = r"Software\Classes"
_MARKER_VALUE = "VpnAutoLoginShim"      # marks a key we created/modified
_BACKUP_VALUE = "VpnAutoLoginPrevCmd"   # original command, restored on removal
_KNOWN_PROG_IDS = ("ChromeHTML", "MSEdgeHTM", "FirefoxURL")


def _winreg():
    import winreg
    return winreg


def get_prog_id(scheme: str = "https") -> str | None:
    """ProgId currently registered for the scheme (e.g. 'ChromeHTML')."""
    if not IS_WINDOWS:
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USER_CHOICE_KEY.format(scheme=scheme)) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        return None


def _command_key_path(prog_id: str) -> str:
    return fr"{_CLASSES}\{prog_id}\shell\open\command"


def _read_command(prog_id: str) -> tuple[str | None, bool]:
    """Return (current HKCU command, marker_present)."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _command_key_path(prog_id)) as key:
            try:
                current = winreg.QueryValueEx(key, "")[0]
            except OSError:
                current = None
            try:
                marker = bool(winreg.QueryValueEx(key, _MARKER_VALUE)[0])
            except OSError:
                marker = False
            return current, marker
    except OSError:
        return None, False


class BrowserUrlHijack:
    """Point the current https ProgId at our automated browser instance.

    Use as a context manager; the registry change is always undone on exit.
    """

    def __init__(self, browser_exe: str, user_data_dir: str, cdp_port: int,
                 schemes: tuple[str, ...] = ("https", "http")):
        self.browser_exe = browser_exe
        self.user_data_dir = user_data_dir
        self.cdp_port = cdp_port
        self.schemes = schemes
        self._installed: list[str] = []

    @property
    def command(self) -> str:
        return (
            f'"{self.browser_exe}" --user-data-dir="{self.user_data_dir}" '
            f'--remote-debugging-port={self.cdp_port} --no-first-run '
            f'--no-default-browser-check "%1"'
        )

    # ── lifecycle ────────────────────────────────────────────────────────────

    def install(self) -> bool:
        """Install the override. Returns True if at least one ProgId was set."""
        if not IS_WINDOWS:
            log.debug("Not Windows — skipping URL handler override.")
            return False
        cleanup_stale()
        prog_ids = []
        for scheme in self.schemes:
            pid = get_prog_id(scheme)
            if pid and pid not in prog_ids:
                prog_ids.append(pid)
        if not prog_ids:
            log.warning("Could not read the default browser ProgId — "
                        "AnyConnect's SAML tab may open outside our browser.")
            return False

        winreg = _winreg()
        for prog_id in prog_ids:
            previous, _ = _read_command(prog_id)
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _command_key_path(prog_id)) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, self.command)
                    winreg.SetValueEx(key, _MARKER_VALUE, 0, winreg.REG_SZ, "1")
                    # Empty string == "there was nothing here before, delete the key"
                    winreg.SetValueEx(key, _BACKUP_VALUE, 0, winreg.REG_SZ, previous or "")
                self._installed.append(prog_id)
                log.info("Routed %s links into the automated browser (ProgId %s).",
                         "/".join(self.schemes), prog_id)
            except OSError as exc:
                log.warning("Could not override URL handler for %s: %s", prog_id, exc)
        return bool(self._installed)

    def remove(self) -> None:
        """Restore the original handler command."""
        if not IS_WINDOWS:
            return
        for prog_id in self._installed:
            _restore(prog_id)
        self._installed.clear()

    def __enter__(self) -> "BrowserUrlHijack":
        self.install()
        return self

    def __exit__(self, *_exc) -> None:
        self.remove()


def _restore(prog_id: str) -> None:
    """Undo our override for one ProgId (restore or delete the key)."""
    winreg = _winreg()
    path = _command_key_path(prog_id)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
            try:
                previous = winreg.QueryValueEx(key, _BACKUP_VALUE)[0]
            except OSError:
                previous = ""
            if previous:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, previous)
            else:
                try:
                    winreg.DeleteValue(key, "")
                except OSError:
                    pass
            for name in (_MARKER_VALUE, _BACKUP_VALUE):
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
        if not previous:
            # We created the chain — remove the empty keys we added, deepest first.
            for suffix in (r"\shell\open\command", r"\shell\open", r"\shell"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, fr"{_CLASSES}\{prog_id}{suffix}")
                except OSError:
                    break
        log.info("Restored the original URL handler for %s.", prog_id)
    except OSError as exc:
        log.warning("Could not restore URL handler for %s: %s", prog_id, exc)


def cleanup_stale() -> None:
    """Remove an override a previous crashed run left behind."""
    if not IS_WINDOWS:
        return
    for prog_id in _KNOWN_PROG_IDS:
        _, marker = _read_command(prog_id)
        if marker:
            log.info("Found a leftover URL handler override for %s — cleaning it up.", prog_id)
            _restore(prog_id)
