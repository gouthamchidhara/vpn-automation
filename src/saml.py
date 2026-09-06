"""Ping Identity SAML form automation + Duo wait."""
from __future__ import annotations
import sys
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from src.config import Selectors
from src.logging_config import get_logger

log = get_logger(__name__)


def fill_username(page: Page, username: str, selectors: Selectors, timeout: int = 15) -> None:
    """Wait for username field, fill it, submit."""
    log.info("Filling username field...")
    el = page.wait_for_selector(selectors.username_input, timeout=timeout * 1000)
    if not el:
        raise RuntimeError(f"Username field not found: {selectors.username_input}")
    el.fill(username)
    log.info("Username filled. Submitting...")
    # Some Ping flows have a "Next" button before password
    submit = page.wait_for_selector(selectors.submit_btn, timeout=5000)
    if submit:
        submit.click()
    # Wait for password field to appear (next step)
    time.sleep(1)


def fill_password(page: Page, password: str, selectors: Selectors, timeout: int = 15) -> None:
    """Wait for password field, fill it, submit."""
    log.info("Filling password field...")
    el = page.wait_for_selector(selectors.password_input, timeout=timeout * 1000)
    if not el:
        raise RuntimeError(f"Password field not found: {selectors.password_input}")
    el.fill(password)
    log.info("Password filled. Submitting...")
    submit = page.wait_for_selector(selectors.submit_btn, timeout=5000)
    if submit:
        submit.click()


def wait_for_duo(page: Page, selectors: Selectors, timeout: int = 120) -> bool:
    """Detect Duo iframe, wait for user to approve push.

    Returns True if Duo approved (page navigated away from Duo).
    Returns False if timeout without approval.
    """
    log.info("Checking for Duo MFA challenge...")
    # Wait for Duo iframe or any duo-related content
    try:
        duo_frame = page.wait_for_selector(selectors.duo_iframe, timeout=10000)
        if duo_frame:
            log.info("Duo iframe detected.")
        else:
            log.info("No Duo iframe — checking for Duo in main frame...")
    except PlaywrightTimeout:
        log.info("No Duo iframe found within 10s — may not be required or uses different auth.")
        # If no Duo, the page may have already redirected to callback
        return True

    print("\n" + "=" * 50)
    print("  ⏳ DUO PUSH — Approve on your phone now!")
    print("=" * 50 + "\n", flush=True)

    # Poll until the page URL changes away from Ping/Duo (success = redirect to 127.0.0.1)
    deadline = time.monotonic() + timeout
    original_url = page.url
    while time.monotonic() < deadline:
        try:
            current_url = page.url
            # Success: redirected to AnyConnect callback (127.0.0.1)
            if "127.0.0.1" in current_url or "localhost" in current_url:
                log.info("Duo approved — redirected to callback: %s", current_url)
                return True
            # Page might close (some SAML flows close the tab)
            if page.is_closed():
                log.info("Page closed after Duo — likely success.")
                return True
            # Check if still on Duo/Ping page
            if current_url != original_url:
                log.info("URL changed from Duo: %s", current_url)
                # Could be an error page, but likely success
                return True
        except Exception:
            # Page may have navigated/closed
            log.info("Page navigation detected during Duo wait — assuming success.")
            return True
        time.sleep(2)

    log.error("Duo push not approved within %ds timeout.", timeout)
    return False


def wait_for_callback(page: Page, timeout: int = 30) -> bool:
    """Wait for SAML redirect to 127.0.0.1 (AnyConnect callback)."""
    log.info("Waiting for SAML callback redirect...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            url = page.url
            if "127.0.0.1" in url or "localhost" in url:
                log.info("SAML callback received: %s", url)
                return True
            if page.is_closed():
                log.info("Page closed — callback likely received.")
                return True
        except Exception:
            return True
        time.sleep(1)
    log.warning("SAML callback not detected within %ds.", timeout)
    return False


def saml_login(
    page: Page,
    username: str,
    password: str,
    selectors: Selectors,
    duo_timeout: int = 120,
    callback_timeout: int = 30,
) -> bool:
    """Full SAML login flow: username → password → Duo → callback.

    Returns True on success.
    """
    try:
        fill_username(page, username, selectors)
        fill_password(page, password, selectors)

        # Wait for Duo (or skip if not present)
        duo_ok = wait_for_duo(page, selectors, timeout=duo_timeout)
        if not duo_ok:
            return False

        # Wait for the callback redirect
        callback_ok = wait_for_callback(page, timeout=callback_timeout)
        if not callback_ok:
            log.error("SAML callback not received — login may have failed.")
            return False

        log.info("SAML login flow completed successfully.")
        return True

    except PlaywrightTimeout as exc:
        log.error("SAML flow timed out: %s", exc)
        return False
    except Exception as exc:
        log.error("SAML flow error: %s", exc)
        return False
