"""CLI entrypoint — orchestrates the full VPN login flow."""
from __future__ import annotations
import argparse
import re
import sys
import tempfile
import time
from pathlib import Path

from src.config import load_config, save_config, Config
from src.creds import setup as creds_setup, resolve as resolve_creds
from src.vpn import VpnCli, wait_for_connected, VpnState
from src.browser import DebugBrowser
from src.default_browser import resolve_browser_exe
from src.duo import DuoResult
from src.saml import fill_password, fill_username, saml_login_with_config
from src.saml_url import capture_saml_url
from src.url_handler import BrowserUrlHijack, cleanup_stale
from src.banner import accept_banner
from src.gui import click_connect, connect_via_gui
from src.logging_config import setup_logging, get_logger

# Tabs a freshly launched browser opens on its own — never the SAML page.
_BLANK_URLS = re.compile(
    r"^(about:|chrome://|edge://|chrome-extension://|$)|"
    r"(ntp\.msn\.com|/newtab|google\.com/_/chrome)",
    re.IGNORECASE,
)


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Automated Cisco AnyConnect + Ping Identity + Duo login"
    )
    parser.add_argument("--setup", action="store_true",
                        help="Store VPN credentials in Windows Credential Manager")
    parser.add_argument("--record-selectors", action="store_true",
                        help="Open Ping page and dump DOM selectors for config")
    parser.add_argument("--no-submit", action="store_true",
                        help="Dry run: fill credentials but don't submit (test selectors)")
    parser.add_argument("--disconnect", action="store_true",
                        help="Disconnect the VPN")
    parser.add_argument("--username", "-u", type=str, default=None,
                        help="VPN username (defaults to the remembered one)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config JSON file")
    parser.add_argument("--json-log", action="store_true", default=True,
                        help="Use JSON log format (default)")
    parser.add_argument("--text-log", action="store_true",
                        help="Use human-readable log format")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    import logging
    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=level, json_output=not args.text_log)
    log = get_logger("vpn-auto-login")

    cfg_path = Path(args.config) if args.config else None
    cfg = load_config(cfg_path)

    # ── Subcommands ──────────────────────────────────────────────────────────
    if args.setup:
        username, _ = creds_setup()
        cfg.last_username = username
        save_config(cfg, cfg_path)
        sys.exit(0)

    if args.record_selectors:
        _record_selectors(cfg)
        sys.exit(0)

    if args.disconnect:
        VpnCli(vpncli_path=cfg.vpncli_path).disconnect()
        sys.exit(0)

    # ── Full connect flow ────────────────────────────────────────────────────
    if not cfg.vpn_host:
        log.error("VPN host not configured. Edit vpn-config.json or pass --config.")
        sys.exit(1)

    # Credentials come from Credential Manager. A normal run only prompts when
    # nothing is stored yet — never for a username it already remembers, and
    # never with an "overwrite?" question about a credential that is fine.
    username, password = resolve_creds(args.username, cfg.last_username)
    if username != cfg.last_username:
        cfg.last_username = username
        save_config(cfg, cfg_path)
    log.info("Starting VPN login for user: %s", username)

    # Phase 1: launch the browser we will drive, on a TEMP profile with CDP.
    # Real profiles refuse to bind the debug port (Chrome/Edge 136+ block it
    # outright for the default profile directory).
    browser_exe = resolve_browser_exe(cfg.browser_exe, prefer_default=cfg.use_default_browser)
    temp_profile = cfg.browser_user_data_dir or tempfile.mkdtemp(prefix="vpn-auto-login-")
    log.info("Using browser %s with temp profile: %s", browser_exe, temp_profile)
    browser = DebugBrowser(
        browser_exe=browser_exe,
        cdp_port=cfg.cdp_port,
        user_data_dir=temp_profile,
    )
    hijack = BrowserUrlHijack(browser_exe, temp_profile, cfg.cdp_port)
    vpn: VpnCli | None = None
    cleanup_stale()

    try:
        browser.launch()
        browser.connect()
        pre_existing = _open_urls(browser)

        # Phase 2: route ShellExecute'd links into THIS browser instance, so
        # the SAML tab AnyConnect opens lands in a window we can drive instead
        # of a second browser we cannot see.
        if cfg.hijack_default_browser:
            hijack.install()

        # Phase 3: AnyConnect GUI → Connect. vpncli refuses SAML groups, so the
        # GUI is the only client that can start this handshake.
        vpn = VpnCli(vpncli_path=cfg.vpncli_path)
        if not connect_via_gui(cfg.vpnui_path, cfg.vpn_profile or cfg.vpn_host):
            log.error("Failed to start VPN connection via AnyConnect GUI.")
            sys.exit(1)

        # Phase 4: get hold of the SAML login tab.
        page = _acquire_saml_page(browser, cfg, pre_existing)
        if page is None:
            log.error("The SAML login page never reached the automated browser.")
            print("\n❌ Could not reach the SAML login page. Run with -v for details.",
                  file=sys.stderr, flush=True)
            sys.exit(1)
        log.info("Driving the SAML login page: %s", page.url)

        # Phase 5: credentials.
        if args.no_submit:
            log.info("DRY RUN: filling credentials but not submitting.")
            fill_username(page, username, cfg.selectors, cfg.saml_fill_timeout)
            fill_password(page, password, cfg.selectors, cfg.saml_fill_timeout)
            log.info("DRY RUN complete. Credentials filled successfully. Exiting.")
            sys.exit(0)

        # Phase 6: credentials → Duo push/number → SAML callback.
        outcome = saml_login_with_config(page, username, password, cfg)
        if not outcome.ok:
            _report_auth_failure(outcome)
            sys.exit(1)

        # Phase 7: Duo said yes — carry the connection through.
        accept_banner(timeout=cfg.banner_timeout)
        _finish_connect(vpn, cfg)

        log.info("Waiting for the VPN tunnel to establish...")
        state = wait_for_connected(vpn, timeout=cfg.connected_timeout)
        if state == VpnState.CONNECTED:
            log.info("VPN connected successfully.")
            print("\n✅ VPN Connected!", flush=True)
            sys.exit(0)
        log.error("VPN did not reach Connected state. Final state: %s", state.value)
        print(f"\n❌ VPN connection failed. State: {state.value}", file=sys.stderr, flush=True)
        sys.exit(1)

    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        log.error("Unexpected error: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        hijack.remove()
        if vpn is not None:
            vpn.terminate_connect_process()
        browser.close()


# ── SAML tab acquisition ─────────────────────────────────────────────────────

def _open_urls(browser: DebugBrowser) -> set[str]:
    """URLs already open before AnyConnect adds the SAML tab."""
    urls = set()
    for page in browser.pages():
        try:
            urls.add(page.url)
        except Exception:
            continue
    return urls


def _acquire_saml_page(browser: DebugBrowser, cfg: Config, pre_existing: set[str]):
    """Return the page showing the SAML login, however it got opened.

    Three ways, in order of reliability:
      1. AnyConnect's tab landed in our browser (the URL-handler override).
      2. It went to another browser — recover the URL from that process's
         command line and load it here.
      3. Nothing found: drive the gateway URL ourselves and let it redirect.
    """
    log = get_logger("vpn-auto-login")

    page = _wait_for_new_tab(browser, cfg, pre_existing, timeout=cfg.ping_page_timeout)
    if page is not None:
        return page

    log.warning("No SAML tab appeared in the automated browser — "
                "looking for the URL AnyConnect opened elsewhere.")
    url = capture_saml_url(cfg.saml_url_pattern(), timeout=20)
    if url:
        try:
            return browser.open_url(url)
        except Exception as exc:
            log.error("Could not load the recovered SAML URL: %s", exc)

    gateway_url = f"https://{cfg.vpn_host}/"
    log.warning("Falling back to the gateway URL: %s", gateway_url)
    try:
        page = browser.open_url(gateway_url)
    except Exception as exc:
        log.error("Could not open the gateway URL: %s", exc)
        return None
    # Give the gateway a moment to redirect to the IdP before we start typing.
    _wait_for_login_form(page, cfg, timeout=cfg.ping_page_timeout)
    return page


def _wait_for_new_tab(browser: DebugBrowser, cfg: Config, pre_existing: set[str], timeout: int):
    """Wait for a tab that is neither blank nor one that was already open.

    Matching on "a login page appeared" rather than on ping_host_regex alone
    matters: the IdP hostname is site-specific, and a stale regex in the config
    must not be the reason the login stalls.
    """
    log = get_logger("vpn-auto-login")
    login_pattern = re.compile(cfg.login_page_regex(), re.IGNORECASE)
    deadline = time.monotonic() + timeout
    fallback = None
    while time.monotonic() < deadline:
        for page in browser.pages():
            try:
                url = page.url
            except Exception:
                continue
            if _BLANK_URLS.search(url) or url in pre_existing:
                continue
            if login_pattern.search(url):
                log.info("SAML tab opened in the automated browser: %s", url)
                _focus(page)
                return page
            # Unknown host: accept it once it actually shows a login form —
            # it may still be mid-redirect towards the IdP.
            if _has_login_form(page, cfg):
                log.info("Login form found in the automated browser: %s", url)
                _focus(page)
                return page
            fallback = fallback or page
        time.sleep(1)

    if fallback is not None:
        log.warning("Using the only new tab that opened: %s", _url_of(fallback))
        _focus(fallback)
    return fallback


def _has_login_form(page, cfg: Config) -> bool:
    """True when the page is showing the IdP's username field."""
    try:
        page.locator(cfg.selectors.username_input).first.wait_for(
            state="visible", timeout=1000)
        return True
    except Exception:
        return False


def _wait_for_login_form(page, cfg: Config, timeout: int) -> None:
    """Give redirects time to settle on a page that actually has a login form."""
    login_pattern = re.compile(cfg.login_page_regex(), re.IGNORECASE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if login_pattern.search(_url_of(page)) or _has_login_form(page, cfg):
            return
        time.sleep(1)


def _url_of(page) -> str:
    try:
        return page.url
    except Exception:
        return "<closed>"


def _focus(page) -> None:
    try:
        page.bring_to_front()
    except Exception:
        pass


# ── Post-authentication ──────────────────────────────────────────────────────

def _report_auth_failure(outcome) -> None:
    """Explain, in plain words, why the login did not go through."""
    log = get_logger("vpn-auto-login")
    messages = {
        DuoResult.DENIED: "Duo push was denied on your phone — connection cancelled.",
        DuoResult.TIMEOUT: "Duo push was not approved in time — connection cancelled.",
        DuoResult.ERROR: "SAML login failed before Duo could complete.",
    }
    message = messages.get(outcome.result, "SAML login failed.")
    if outcome.detail:
        message = f"{message} ({outcome.detail})"
    log.error(message)
    print(f"\n❌ {message}", file=sys.stderr, flush=True)


def _finish_connect(vpn: VpnCli, cfg: Config) -> None:
    """After Duo approves, make sure AnyConnect is actually connecting.

    Most profiles carry straight on once the SAML token comes back. Some drop
    the GUI back to "Ready to connect" instead, and then the tunnel only comes
    up if Connect is pressed again — so press it, but only when the client is
    not already connecting or connected.
    """
    log = get_logger("vpn-auto-login")
    state = vpn.state()
    log.info("VPN state after Duo approval: %s", state.value)
    if state in (VpnState.CONNECTED, VpnState.CONNECTING):
        return
    log.info("AnyConnect is idle after authentication — clicking Connect again.")
    click_connect(attempts=2, vpnui_path=cfg.vpnui_path)


def _record_selectors(cfg: Config) -> None:
    """Open Ping page for manual selector inspection."""
    log = get_logger("vpn-auto-login.selectors")
    log.info("Opening browser for selector recording...")
    log.info("1. Connect to VPN manually first.")
    log.info("2. Open the Ping login page.")
    log.info("3. Use DevTools (F12) to inspect elements.")
    log.info("4. Update vpn-config.json selectors section.")
    print("\nCurrent selectors config:")
    from dataclasses import asdict
    import json
    print(json.dumps(asdict(cfg.selectors), indent=2))
    print("\nCurrent Duo selectors config:")
    print(json.dumps(asdict(cfg.duo_selectors), indent=2))
