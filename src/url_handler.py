"""Keep the Windows https:// association healthy for AnyConnect.

AnyConnect opens the SSO page through the Windows https handler, so the
handler has to resolve to a real browser command. Earlier versions of this
tool redirected that handler at an automated browser instance; that made
AnyConnect fail with "Authentication failed due to problem navigating to
the single sign-on URL", because the redirected command hands the URL to
the running instance and exits at once, which AnyConnect reads as the
browser failing to start. Worse, removing the redirect could leave an empty
HKCU\\Software\\Classes\\<ProgId> key behind, which shadows the machine-wide
registration and breaks every https link on the machine.

The redirect is gone. What remains is the repair: every run removes any
per-user registration this tool left behind, so a machine damaged by an
older version heals itself.
"""
from __future__ import annotations
import sys

from src.logging_config import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

_USER_CHOICE_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\UserChoice"
_CLASSES = r"Software\Classes"
# Values an older version wrote alongside its redirect. Still read here so
# a machine that carries one gets repaired.
_MARKER_VALUE = "VpnAutoLoginShim"
_BACKUP_VALUE = "VpnAutoLoginPrevCmd"
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
            # We created the chain — remove every key we added, including the
            # ProgId itself. Leaving an EMPTY HKCU\Software\Classes\<ProgId>
            # behind is worse than leaving the override in place: it shadows
            # the machine-wide registration, so Windows can no longer resolve
            # https at all and AnyConnect fails with "problem navigating to the
            # single sign-on URL".
            _delete_tree(prog_id)
        log.info("Restored the original URL handler for %s.", prog_id)
    except OSError as exc:
        log.warning("Could not restore URL handler for %s: %s", prog_id, exc)


def _delete_tree(prog_id: str) -> None:
    """Delete HKCU\\Software\\Classes\\<ProgId> and everything under it.

    Scoped to the browser ProgIds this module knows about, and only ever under
    HKCU — the machine-wide registration in HKLM is what takes over afterwards.
    """
    winreg = _winreg()

    def _delete(path: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                while True:
                    try:
                        child = winreg.EnumKey(key, 0)
                    except OSError:
                        break
                    _delete(fr"{path}\{child}")
        except OSError:
            return
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError as exc:
            log.debug("Could not delete %s: %s", path, exc)

    _delete(fr"{_CLASSES}\{prog_id}")


def association_status(prog_id: str) -> tuple[str, str]:
    """Describe the per-user registration for a ProgId: (state, command).

    state is "none" (nothing in HKCU, machine registration applies), "ours"
    (still pointing at an automated profile), "broken" (a key with no usable
    open command — this is what stops Windows resolving https), or "user"
    (somebody else's legitimate per-user registration).
    """
    if not IS_WINDOWS:
        return "none", ""
    winreg = _winreg()
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, fr"{_CLASSES}\{prog_id}").Close()
    except OSError:
        return "none", ""
    command, _ = _read_command(prog_id)
    if not command:
        return "broken", ""
    if "vpn-auto-login" in command:
        return "ours", command
    return "user", command


def repair_browser_association() -> list[str]:
    """Undo anything this tool left behind that can break the https handler.

    Returns the ProgIds that were repaired. Safe to call on every run: a
    legitimate per-user browser registration is left untouched.
    """
    if not IS_WINDOWS:
        return []
    repaired = []
    for prog_id in _KNOWN_PROG_IDS:
        _, marker = _read_command(prog_id)
        state, command = association_status(prog_id)
        if marker or state in ("ours", "broken"):
            reason = {
                "ours": "it still points at the automated browser",
                "broken": "it has no usable open command and shadows the real one",
            }.get(state, "a previous run left an override")
            log.warning("Repairing the %s URL association — %s.", prog_id, reason)
            log.debug("Current per-user command for %s: %r", prog_id, command)
            if marker:
                _restore(prog_id)
            if association_status(prog_id)[0] in ("ours", "broken"):
                _delete_tree(prog_id)
            repaired.append(prog_id)
    if repaired:
        log.warning("Repaired the browser association for: %s. "
                    "Windows will use the system registration again.", ", ".join(repaired))
    return repaired


def describe_association() -> str:
    """One-line summary of how Windows will open an https link right now."""
    if not IS_WINDOWS:
        return "not Windows — no URL association to inspect"
    prog_id = get_prog_id("https") or "<unknown>"
    state, command = association_status(prog_id)
    if state == "none":
        return f"https opens via {prog_id} (system registration)"
    return f"https opens via {prog_id} (per-user registration: {state}) -> {command or '<missing>'}"


def cleanup_stale() -> None:
    """Remove an override a previous crashed run left behind."""
    repair_browser_association()
