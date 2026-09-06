"""Ping Identity SAML form automation, Duo hand-off, and callback detection."""
from __future__ import annotations
import re
import time

from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
)

from src.config import Config, DuoSelectors, Selectors
from src.duo import DuoOutcome, DuoResult, handle_duo
from src.logging_config import get_logger

log = get_logger(__name__)

_DEFAULT_CALLBACK_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", re.IGNORECASE)


def _visible(page: Page, selector: str, timeout_ms: int = 1500) -> bool:
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        return False


def _click_submit(page: Page, selectors: Selectors, timeout: int = 10) -> bool:
    """Click the form's submit/next button. Falls back to pressing Enter."""
    try:
        button = page.locator(selectors.submit_btn).first
        button.wait_for(state="visible", timeout=timeout * 1000)
        button.click()
        return True
    except (PlaywrightTimeout, PlaywrightError) as exc:
        log.debug("Submit button not clickable (%s) — pressing Enter instead.", exc)
        try:
            page.keyboard.press("Enter")
            return True
        except PlaywrightError:
            return False


def fill_username(page: Page, username: str, selectors: Selectors, timeout: int = 20) -> None:
    """Fill the username field. Only advances the form on two-step logins."""
    log.info("Filling the username field...")
    el = page.wait_for_selector(selectors.username_input, timeout=timeout * 1000)
    if not el:
        raise RuntimeError(f"Username field not found: {selectors.username_input}")
    el.fill(username)

    # Single-page forms show both fields at once. Submitting after the username
    # would post an empty password and fail the login, so only click through
    # when the password field is genuinely not on screen yet.
    if _visible(page, selectors.password_input, timeout_ms=1000):
        log.info("Username filled (single-page form — password is on the same screen).")
        return

    log.info("Username filled. Advancing to the password step...")
    _click_submit(page, selectors)
    time.sleep(1)


def fill_password(page: Page, password: str, selectors: Selectors, timeout: int = 20) -> None:
    """Fill the password field and submit the form."""
    log.info("Filling the password field...")
    el = page.wait_for_selector(selectors.password_input, timeout=timeout * 1000)
    if not el:
        raise RuntimeError(f"Password field not found: {selectors.password_input}")
    el.fill(password)
    log.info("Password filled. Submitting the login form...")
    _click_submit(page, selectors)


def login_error(page: Page, selectors: Selectors) -> str | None:
    """Return the IdP's error text (wrong password, locked account), if any."""
    try:
        locator = page.locator(selectors.error_msg).first
        locator.wait_for(state="visible", timeout=1500)
        text = (locator.inner_text() or "").strip()
        return text or None
    except (PlaywrightTimeout, PlaywrightError):
        return None


def wait_for_callback(page: Page, timeout: int = 60,
                      callback_pattern: re.Pattern[str] | None = None) -> bool:
    """Wait for the SAML redirect back to AnyConnect's local listener."""
    pattern = callback_pattern or _DEFAULT_CALLBACK_RE
    log.info("Waiting for the SAML callback to AnyConnect (up to %ds)...", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                log.info("SAML tab closed — AnyConnect took over the handshake.")
                return True
            url = page.url
        except PlaywrightError:
            # The tab was torn down mid-read, which only happens once the
            # handshake has moved back to AnyConnect.
            log.info("SAML tab went away — treating the callback as delivered.")
            return True

        if pattern.search(url):
            log.info("SAML callback received: %s", url)
            return True
        time.sleep(1)

    log.warning("No SAML callback seen within %ds (last URL: %s).", timeout, _safe_url(page))
    return False


def _safe_url(page: Page) -> str:
    try:
        return page.url
    except PlaywrightError:
        return "<closed>"


def saml_login(
    page: Page,
    username: str,
    password: str,
    selectors: Selectors,
    duo_selectors: DuoSelectors | None = None,
    duo_host_pattern: re.Pattern[str] | None = None,
    callback_pattern: re.Pattern[str] | None = None,
    fill_timeout: int = 20,
    duo_action_timeout: int = 20,
    duo_timeout: int = 120,
    callback_timeout: int = 60,
    trust_browser: bool = True,
) -> DuoOutcome:
    """Run the browser half of the login: credentials → Duo → callback.

    Returns the Duo outcome so the caller can tell "approved" from "denied" and
    only drive the VPN connect when the MFA actually succeeded.
    """
    duo_selectors = duo_selectors or DuoSelectors()
    duo_pattern = duo_host_pattern or re.compile(r"duosecurity\.com|\.duo\.com", re.IGNORECASE)

    try:
        fill_username(page, username, selectors, timeout=fill_timeout)
        fill_password(page, password, selectors, timeout=fill_timeout)

        error = login_error(page, selectors)
        if error:
            log.error("The identity provider rejected the sign-in: %s", error)
            return DuoOutcome(DuoResult.ERROR, error)

        outcome = handle_duo(
            page,
            duo_selectors,
            duo_pattern,
            detect_timeout=duo_action_timeout,
            action_timeout=duo_action_timeout,
            approval_timeout=duo_timeout,
            trust_browser=trust_browser,
        )
        if not outcome.ok:
            return outcome

        if outcome.result is DuoResult.NOT_REQUIRED:
            # No Duo prompt can also mean the IdP never got past the password —
            # a slow error message that wasn't rendered yet on the first check.
            late_error = login_error(page, selectors)
            if late_error:
                log.error("The identity provider rejected the sign-in: %s", late_error)
                return DuoOutcome(DuoResult.ERROR, late_error)

        if not wait_for_callback(page, timeout=callback_timeout,
                                 callback_pattern=callback_pattern):
            log.error("SAML callback not received — AnyConnect never got the token.")
            return DuoOutcome(DuoResult.ERROR, "no SAML callback")

        log.info("SAML login completed (Duo: %s).", outcome.result.value)
        return outcome

    except PlaywrightTimeout as exc:
        log.error("The SAML flow timed out: %s", exc)
        return DuoOutcome(DuoResult.ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface any page error as a login failure
        log.error("SAML flow error: %s", exc)
        return DuoOutcome(DuoResult.ERROR, str(exc))


def saml_login_with_config(page: Page, username: str, password: str, cfg: Config) -> DuoOutcome:
    """saml_login() wired up from a Config object."""
    return saml_login(
        page=page,
        username=username,
        password=password,
        selectors=cfg.selectors,
        duo_selectors=cfg.duo_selectors,
        duo_host_pattern=cfg.duo_host_pattern(),
        callback_pattern=cfg.callback_pattern(),
        fill_timeout=cfg.saml_fill_timeout,
        duo_action_timeout=cfg.duo_action_timeout,
        duo_timeout=cfg.duo_wait_timeout,
        callback_timeout=cfg.callback_timeout,
        trust_browser=cfg.trust_browser,
    )
