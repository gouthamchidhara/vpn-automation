"""CLI entrypoint — orchestrates the full VPN login flow."""
from __future__ import annotations
import argparse
import sys
import time

from src.config import load_config, save_config, Config
from src.creds import setup as creds_setup, get_or_setup
from src.vpn import VpnCli, wait_for_connected, VpnState
from src.browser import DebugBrowser
from src.saml import saml_login
from src.banner import accept_banner
from src.logging_config import setup_logging, get_logger


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
                        help="VPN username (prompted if not stored)")
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

    # Load config
    from pathlib import Path
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
        vpn_cli = VpnCli(vpncli_path=cfg.vpncli_path)
        vpn_cli.disconnect()
        sys.exit(0)

    # ── Full connect flow ────────────────────────────────────────────────────
    if not cfg.vpn_host:
        log.error("VPN host not configured. Edit vpn-config.json or pass --config.")
        sys.exit(1)

    username, password = get_or_setup(args.username)
    log.info("Starting VPN login for user: %s", username)

    # Phase 1: Launch Edge with a TEMP profile + CDP debug port.
    # Real browser profiles (Chrome/Edge with 40+ tabs, extensions, startup
    # boost) consistently fail to bind the CDP port. A clean temp profile
    # starts in <2 seconds with zero interference.
    # Instead of waiting for AnyConnect's ShellExecute to open the SAML URL
    # in the default browser, we navigate to the VPN gateway URL directly in
    # our controlled browser after AnyConnect initiates the SAML flow.
    import tempfile
    temp_profile = tempfile.mkdtemp(prefix="vpn-auto-login-")
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    log.info("Using Edge with temp profile: %s", temp_profile)
    browser = DebugBrowser(
        browser_exe=edge_exe,
        cdp_port=cfg.cdp_port,
        user_data_dir=temp_profile,
    )
    vpn: VpnCli | None = None

    try:
        browser.launch()
        context = browser.connect()

        # Phase 2: Start AnyConnect GUI → click Connect.
        # This tells AnyConnect to initiate the VPN handshake and open a local
        # callback listener for the SAML token.
        vpn = VpnCli(vpncli_path=cfg.vpncli_path)
        from src.gui import connect_via_gui
        if not connect_via_gui(cfg.vpnui_path, cfg.vpn_profile or cfg.vpn_host):
            log.error("Failed to start VPN connection via AnyConnect GUI.")
            sys.exit(1)

        # Phase 3: Navigate directly to VPN gateway in our controlled browser.
        # Instead of waiting for AnyConnect's ShellExecute (which opens the
        # default browser we can't control), we trigger the SAML flow ourselves
        # by visiting the gateway URL. The gateway redirects to the Ping IdP.
        gateway_url = f"https://{cfg.vpn_host}/"
        log.info("Navigating to VPN gateway: %s", gateway_url)
        page = context.new_page()
        page.goto(gateway_url, wait_until="domcontentloaded", timeout=30000)

        # Phase 4: Wait for Ping SAML page to load in our controlled browser
        ping_pattern = cfg.ping_host_pattern()
        log.info("Waiting for Ping SAML page (pattern: %s)...", cfg.ping_host_regex)
        page = browser.find_page_by_url(cfg.ping_host_regex, timeout=cfg.ping_page_timeout)
        if not page:
            log.error("Ping SAML page did not open in browser within %ds.", cfg.ping_page_timeout)
            sys.exit(1)

        log.info("Ping page found: %s", page.url)

        # Phase 4: Fill credentials + wait for Duo
        if args.no_submit:
            log.info("DRY RUN: filling credentials but not submitting.")
            from src.saml import fill_username, fill_password
            fill_username(page, username, cfg.selectors, cfg.saml_fill_timeout)
            fill_password(page, password, cfg.selectors, cfg.saml_fill_timeout)
            log.info("DRY RUN complete. Credentials filled successfully. Exiting.")
            sys.exit(0)

        ok = saml_login(
            page=page,
            username=username,
            password=password,
            selectors=cfg.selectors,
            duo_timeout=cfg.duo_wait_timeout,
            callback_timeout=30,
        )
        if not ok:
            log.error("SAML login failed.")
            sys.exit(1)

        # Phase 5: Handle banner dialog (may appear after SAML auth)
        accept_banner(timeout=cfg.banner_timeout)

        # Phase 6: Wait for VPN to report Connected
        log.info("Waiting for VPN tunnel to establish...")
        state = wait_for_connected(vpn, timeout=cfg.connected_timeout)
        if state == VpnState.CONNECTED:
            log.info("✅ VPN connected successfully!")
            print("\n✅ VPN Connected!", flush=True)
            sys.exit(0)
        else:
            log.error("VPN did not reach Connected state. Final state: %s", state.value)
            print(f"\n❌ VPN connection failed. State: {state.value}", file=sys.stderr, flush=True)
            sys.exit(1)

    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(130)
    except Exception as exc:
        log.error("Unexpected error: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        if vpn is not None:
            vpn.terminate_connect_process()
        browser.close()


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
