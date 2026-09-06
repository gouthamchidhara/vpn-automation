"""Drive the AnyConnect GUI (vpnui.exe) to start a connection.

vpncli.exe rejects SAML groups outright ("The requested authentication type is
not supported in AnyConnect CLI."), so the GUI is the only client that can
initiate this connection. Once Connect is clicked, AnyConnect still opens the
external SAML browser tab exactly as it would for a manual click.
"""
from __future__ import annotations
import re
import subprocess
import time

from src.logging_config import get_logger

log = get_logger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_MAIN_TITLE = "cisco anyconnect secure mobility client"
_CONNECT_RE = re.compile(r"^connect$|^connect\b", re.IGNORECASE)


def connect_via_gui(vpnui_path: str, profile_name: str, launch_wait: int = 10) -> bool:
    """Launch (or focus) the AnyConnect GUI, pick the profile, click Connect."""
    # A prior aborted run can leave a "Please complete the authentication
    # process in the browser window" popup open; it has no Connect button and
    # confuses window matching, so clear it before doing anything else.
    _cancel_stale_auth_dialogs()

    window = _open_main_window(vpnui_path, launch_wait)
    if window is None:
        return False

    window.set_focus()

    # Desktop().windows() returns resolved UIAWrapper objects, which only
    # support .descendants()/.children() — not .child_window().
    try:
        combos = window.descendants(control_type="ComboBox")
        if combos:
            combos[0].select(profile_name)
            log.info("Selected VPN profile '%s' in AnyConnect GUI.", profile_name)
        else:
            log.warning("No profile dropdown found (may already be on '%s').", profile_name)
    except Exception as exc:
        log.warning("Could not select profile '%s' (may already be selected): %s", profile_name, exc)

    # Selecting the profile refreshes the window's UI Automation tree —
    # re-fetch a fresh reference and retry the Connect button search.
    time.sleep(1)
    return click_connect(attempts=3)


def click_connect(attempts: int = 2, vpnui_path: str | None = None) -> bool:
    """Click Connect in the AnyConnect GUI. Used to start and to resume a login.

    After Duo approves, some profiles hand control back to the GUI sitting on
    "Ready to connect" — this presses Connect again so the tunnel actually
    comes up.
    """
    window = None
    for attempt in range(attempts):
        window = _find_main_window()
        if window is None and vpnui_path:
            window = _open_main_window(vpnui_path, launch_wait=10)
        if window is None:
            time.sleep(1)
            continue
        try:
            window.set_focus()
        except Exception:
            pass
        try:
            connect_btn = _find_connect_button(window)
            if connect_btn is not None:
                connect_btn.click()
                log.info("Clicked Connect in AnyConnect GUI.")
                return True
            log.info("No Connect button on screen (attempt %d/%d) — "
                     "AnyConnect may already be connecting.", attempt + 1, attempts)
        except Exception as exc:
            log.warning("Connect attempt %d failed: %s", attempt + 1, exc)
        time.sleep(1)

    log.error("Could not find the Connect button after %d attempts.", attempts)
    if window is not None:
        _dump_controls(window)
    return False


def _open_main_window(vpnui_path: str, launch_wait: int):
    """Return the AnyConnect main window, launching the GUI if needed."""
    window = _find_main_window()
    if window is not None:
        return window
    log.info("Launching AnyConnect GUI: %s", vpnui_path)
    try:
        subprocess.Popen([vpnui_path], creationflags=_NO_WINDOW)
    except FileNotFoundError:
        log.error("vpnui.exe not found at %s", vpnui_path)
        return None
    window = _wait_for_main_window(launch_wait)
    if window is None:
        log.error("AnyConnect GUI window did not appear within %ds.", launch_wait)
    return window


def _cancel_stale_auth_dialogs() -> None:
    """Cancel any leftover 'Please complete the authentication...' popup from a prior run."""
    from pywinauto import Desktop
    try:
        desktop = Desktop(backend="uia")
        for win in desktop.windows():
            title = win.window_text().lower()
            if "anyconnect" in title and _MAIN_TITLE not in title:
                for btn in win.descendants(control_type="Button"):
                    if btn.window_text().strip().lower() == "cancel":
                        btn.click()
                        log.info("Cancelled stale AnyConnect dialog: '%s'", win.window_text())
                        time.sleep(1)
                        break
    except Exception as exc:
        log.debug("Stale dialog cleanup failed (non-fatal): %s", exc)


def _find_main_window():
    from pywinauto import Desktop
    try:
        desktop = Desktop(backend="uia")
        for win in desktop.windows():
            if _MAIN_TITLE in win.window_text().lower():
                return win
    except Exception:
        pass
    return None


def _wait_for_main_window(timeout: int):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        win = _find_main_window()
        if win is not None:
            return win
        time.sleep(1)
    return None


def is_connect_text(text: str) -> bool:
    """True for the Connect button's label, false for Disconnect."""
    cleaned = (text or "").replace("&", "").strip()
    if "disconnect" in cleaned.lower():
        return False
    return bool(_CONNECT_RE.match(cleaned))


def _find_connect_button(window):
    """Match the Connect button — never Disconnect, never the 'Connect to:' label."""
    # Strategy 1: Button controls labelled Connect
    for ctrl in window.descendants(control_type="Button"):
        if is_connect_text(ctrl.window_text()):
            return ctrl
    # Strategy 2: any invokable control whose automation id names the connect action
    for ctrl in window.descendants():
        try:
            aid = getattr(ctrl.element_info, "automation_id", "") or ""
            if is_connect_text(aid) or aid.lower() in ("connectbutton", "btnconnect"):
                return ctrl
        except Exception:
            continue
    return None


def _dump_controls(window) -> None:
    """Log all controls in the window for debugging."""
    log.error("=== DUMPING ALL CONTROLS IN ANYCONNECT WINDOW ===")
    log.error("Window title: '%s'", window.window_text())
    for ctrl in window.descendants():
        try:
            ctype = ctrl.element_info.control_type or "?"
            name = ctrl.element_info.name or ""
            aid = getattr(ctrl.element_info, "automation_id", "") or ""
            text = ""
            try:
                text = ctrl.window_text() or ""
            except Exception:
                pass
            log.error("  [%s] name='%s' aid='%s' text='%s'", ctype, name, aid, text)
        except Exception:
            pass
    log.error("=== END CONTROLS DUMP ===")
