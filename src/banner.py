"""AnyConnect banner/accept dialog handler via pywinauto."""
from __future__ import annotations
import time

from src.logging_config import get_logger

log = get_logger(__name__)


def accept_banner(timeout: int = 30) -> bool:
    """Watch for AnyConnect banner dialog and click Accept.

    Some org configs show a policy banner after SAML auth that the user
    must accept before the tunnel fully establishes.

    Returns True if banner was found and accepted, False if timed out.
    """
    try:
        from pywinauto import Desktop
    except ImportError:
        log.warning("pywinauto not available — skipping banner detection.")
        return False

    deadline = time.monotonic() + timeout
    log.info("Watching for AnyConnect banner dialog (timeout %ds)...", timeout)

    while time.monotonic() < deadline:
        try:
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            for win in windows:
                title = win.window_text().lower()
                # AnyConnect banner dialogs typically have "anyconnect" in the title
                if "anyconnect" in title and ("banner" in title or "accept" in title or "policy" in title):
                    log.info("Found banner dialog: '%s'", win.window_text())
                    # Desktop().windows() returns resolved UIAWrapper objects, which
                    # only support .descendants()/.children() — not .child_window().
                    for btn in win.descendants(control_type="Button"):
                        text = btn.window_text().strip().lower()
                        if text in ("accept", "ok", "continue", "agree", "i accept"):
                            btn.click()
                            log.info("Clicked '%s' button on banner.", text)
                            return True
                    log.warning("Banner dialog found but no Accept button located.")
                    return False
        except Exception as exc:
            log.debug("Banner scan iteration failed (non-fatal): %s", exc)

        time.sleep(2)

    log.info("No banner dialog appeared within %ds — proceeding.", timeout)
    return False

