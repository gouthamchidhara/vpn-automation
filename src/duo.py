"""Duo MFA automation.

Handles both Duo prompts:

* Universal Prompt — a full-page redirect to *.duosecurity.com. Depending on
  policy it either auto-sends the push, shows a "Send Me a Push" button, or
  shows a factor list behind "Other options".
* Legacy prompt — an iframe (`#duo_iframe`) embedded in the IdP page.

The flow is the same in both: make sure a push is actually sent, surface
whatever Duo shows the user (the 3-digit verification number, when the policy
uses verified push), then wait for the phone approval and report the result —
approved, denied, or timed out — so the caller can decide whether to carry on
to the VPN connect.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass
from enum import Enum

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from src.config import DuoSelectors
from src.logging_config import get_logger

log = get_logger(__name__)

_CODE_RE = re.compile(r"\b(\d{1,3})\b")
# Duo renders these when the user taps Deny, or when the push expires
_DENIED_MARKERS = (
    "login request denied",
    "request denied",
    "denied by user",
    "authentication denied",
    "duo push denied",
)
_EXPIRED_MARKERS = (
    "login request timed out",
    "request timed out",
    "the request expired",
    "push notification expired",
)


class DuoResult(Enum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    NOT_REQUIRED = "not_required"
    ERROR = "error"


@dataclass
class DuoOutcome:
    result: DuoResult
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result in (DuoResult.APPROVED, DuoResult.NOT_REQUIRED)


def is_duo_page(page, duo_host_pattern: re.Pattern[str]) -> bool:
    """True when the tab itself is on Duo (Universal Prompt)."""
    try:
        return bool(duo_host_pattern.search(page.url))
    except PlaywrightError:
        return False


def duo_scope(page, selectors: DuoSelectors, duo_host_pattern: re.Pattern[str], timeout: int = 15):
    """Return the Page or Frame that holds the Duo UI, or None if there is none.

    Universal Prompt lives in the page itself; the legacy prompt lives in an
    iframe. Both expose the locator API we need, so the caller can treat them
    the same way.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_duo_page(page, duo_host_pattern):
            return page
        for frame in _frames(page):
            try:
                if duo_host_pattern.search(frame.url or ""):
                    log.info("Duo prompt found in an embedded frame.")
                    return frame
            except PlaywrightError:
                continue
        time.sleep(1)
    return None


def _frames(page):
    try:
        return list(page.frames)
    except PlaywrightError:
        return []


def _click_if_present(scope, selector: str, what: str, timeout_ms: int = 3000) -> bool:
    """Click the first visible match, if there is one. Never raises."""
    try:
        locator = scope.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        locator.click()
        log.info("Clicked %s in the Duo prompt.", what)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        return False


def _text(scope, selector: str, timeout_ms: int = 1500) -> str:
    try:
        locator = scope.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        return (locator.inner_text() or "").strip()
    except (PlaywrightTimeout, PlaywrightError):
        return ""


def _body_text(scope) -> str:
    try:
        return (scope.locator("body").first.inner_text() or "").lower()
    except (PlaywrightTimeout, PlaywrightError):
        return ""


def send_push(scope, selectors: DuoSelectors, timeout: int = 20) -> bool:
    """Make sure a Duo push is on its way.

    Returns True if a push was triggered (by us or automatically by Duo).
    """
    # Newer Universal Prompt policies send the push on load and go straight to
    # "Check for a Duo Push". Nothing to click in that case.
    if _push_already_sent(scope):
        log.info("Duo already sent the push automatically.")
        return True

    if _click_if_present(scope, selectors.push_btn, "'Send Me a Push'", timeout * 1000 // 4):
        return True

    # Push isn't the default factor — open the factor list and pick it.
    if _click_if_present(scope, selectors.other_options, "'Other options'"):
        if _click_if_present(scope, selectors.push_option, "the 'Duo Push' option", timeout * 1000 // 4):
            return True

    # Some tenants render push as the only option with a generic submit button.
    if _push_already_sent(scope):
        return True

    log.warning("No Duo push button found — the prompt may use a different factor.")
    return False


def _push_already_sent(scope) -> bool:
    body = _body_text(scope)
    return any(marker in body for marker in (
        "check for a duo push",
        "pushed a login request",
        "sent a push",
        "approve this login",
        "verify it's you",
        "verify it’s you",
    ))


def read_verification_code(scope, selectors: DuoSelectors, timeout: int = 10) -> str | None:
    """Return the number Duo asks the user to tap on their phone, if shown."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = _text(scope, selectors.verification_code)
        if raw:
            match = _CODE_RE.search(raw.replace("\n", " "))
            if match:
                return match.group(1)
        time.sleep(1)
    return None


def announce(code: str | None) -> None:
    """Tell the user, on the console, exactly what Duo is asking for."""
    print("\n" + "=" * 52, flush=True)
    if code:
        print("  📲 DUO PUSH SENT — approve it on your phone")
        print(f"  👉 Tap this number in the Duo app:  {code}")
    else:
        print("  📲 DUO PUSH SENT — approve it on your phone")
    print("=" * 52 + "\n", flush=True)


def wait_for_approval(
    page,
    scope,
    selectors: DuoSelectors,
    duo_host_pattern: re.Pattern[str],
    timeout: int = 120,
    trust_browser: bool = True,
) -> DuoOutcome:
    """Poll the Duo prompt until it is approved, denied, or times out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The tab closing or navigating off Duo is the success signal.
        try:
            if page.is_closed():
                log.info("SAML tab closed after Duo — treating as approved.")
                return DuoOutcome(DuoResult.APPROVED, "tab closed")
            url = page.url
        except PlaywrightError:
            return DuoOutcome(DuoResult.APPROVED, "tab navigated away")

        if not duo_host_pattern.search(url):
            log.info("Duo approved — the browser moved on to %s", url)
            return DuoOutcome(DuoResult.APPROVED, url)

        # "Is this your device?" appears after approval and blocks the redirect.
        selector = selectors.trust_browser_btn if trust_browser else selectors.dont_trust_btn
        if _click_if_present(scope, selector, "the trust-this-device answer", 1500):
            log.info("Duo approved — answered the trust-this-device prompt.")
            time.sleep(2)
            continue

        body = _body_text(scope)
        if any(marker in body for marker in _DENIED_MARKERS):
            log.error("Duo push was denied on the phone.")
            return DuoOutcome(DuoResult.DENIED, "denied on device")
        if any(marker in body for marker in _EXPIRED_MARKERS):
            log.error("Duo push expired before it was approved.")
            return DuoOutcome(DuoResult.TIMEOUT, "push expired")

        time.sleep(2)

    log.error("Duo was not approved within %ds.", timeout)
    return DuoOutcome(DuoResult.TIMEOUT, f"no approval within {timeout}s")


def handle_duo(
    page,
    selectors: DuoSelectors,
    duo_host_pattern: re.Pattern[str],
    detect_timeout: int = 20,
    action_timeout: int = 20,
    approval_timeout: int = 120,
    trust_browser: bool = True,
) -> DuoOutcome:
    """Full Duo step: detect prompt → send push → show the number → wait.

    Returns NOT_REQUIRED when no Duo prompt appears (some sessions are already
    inside the remembered-device window).
    """
    scope = duo_scope(page, selectors, duo_host_pattern, timeout=detect_timeout)
    if scope is None:
        log.info("No Duo prompt appeared — MFA is not being asked for.")
        return DuoOutcome(DuoResult.NOT_REQUIRED, "no Duo prompt")

    log.info("Duo prompt detected.")
    if not send_push(scope, selectors, timeout=action_timeout):
        # Not fatal: the push may already be in flight, or the user may pick a
        # factor themselves. Keep waiting rather than failing the whole login.
        log.warning("Could not trigger the Duo push automatically — "
                    "approve on your phone or choose a factor in the browser.")

    code = read_verification_code(scope, selectors, timeout=8)
    if code:
        log.info("Duo verification number: %s", code)
    announce(code)

    return wait_for_approval(
        page, scope, selectors, duo_host_pattern,
        timeout=approval_timeout, trust_browser=trust_browser,
    )
