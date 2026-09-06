"""Configuration — VPN host, Ping/Duo selectors, timeouts. NO secrets here."""
from __future__ import annotations
import json
import re
import sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

# When frozen (PyInstaller), __file__ points into a temp extraction dir, so
# resolve config next to the .exe instead; otherwise use the repo root.
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = _BASE_DIR / "vpn-config.json"

# ── Defaults (override via vpn-config.json) ──────────────────────────────────

@dataclass
class Selectors:
    """CSS selectors for the Ping Identity SAML login page."""
    username_input: str = "input[name='identifier'], input#identifier, input[name='username'], input[placeholder*='AA ID' i], input#okta-signin-username, input[autocomplete='username']"
    password_input: str = "input[name='password'], input#password, input[type='password'], input#okta-signin-password"
    submit_btn: str = "button[type='submit'], input[type='submit'], button.continueBtn, button:has-text('Next'), button:has-text('Sign In'), button:has-text('Continue')"
    duo_iframe: str = "iframe[id='duo_iframe'], iframe[src*='duosecurity']"
    # Login failure text rendered by Ping when the password is rejected
    error_msg: str = ".alert-error, .error-message, [role='alert'], .ping-error, .validation-error"


@dataclass
class DuoSelectors:
    """Selectors for the Duo prompt (Universal Prompt and legacy iframe)."""
    # "Other options" opens the factor list when the default factor isn't push
    other_options: str = "button:has-text('Other options'), a:has-text('Other options'), .other-options-link"
    # Choose Duo Push from the factor list
    push_option: str = "button:has-text('Duo Push'), .method-button:has-text('Duo Push'), a:has-text('Duo Push'), button:has-text('Send Me a Push')"
    # Button that actually sends the push on the default screen
    push_btn: str = "#auth_methods .push-label button, button:has-text('Send Me a Push'), button:has-text('Send me a Push'), button#passwordless-auth-button, button.auth-button:has-text('Push')"
    # The 3-digit number Duo shows for "verified push" ("enter this number")
    verification_code: str = ".verification-code, .verification-code-container, [class*='verification-code'], .passcode-numbers"
    # Post-approval "Is this your device?" screen
    trust_browser_btn: str = "#trust-browser-button, button:has-text('Yes, this is my device'), button:has-text('Yes, trust browser'), button:has-text('Trust browser')"
    dont_trust_btn: str = "#dont-trust-browser-button, button:has-text('No, other people use this device')"
    # Duo error / denial text
    error_msg: str = ".message-text, .error, [role='alert'], .push-label .error"


@dataclass
class Config:
    # VPN
    vpn_host: str = ""                          # e.g. "vpn.company.com"
    vpn_profile: str = ""                       # AnyConnect profile name
    vpncli_path: str = r"C:\Program Files (x86)\Cisco\Cisco AnyConnect Secure Mobility Client\vpncli.exe"
    vpnui_path: str  = r"C:\Program Files (x86)\Cisco\Cisco AnyConnect Secure Mobility Client\vpnui.exe"

    # Last-used VPN username (not a secret — password stays in Credential Manager only)
    last_username: str = ""

    # Ping Identity
    ping_host_regex: str = r"pingone\.com|pingidentity\.com|auth\.company\.com"
    # Duo (Universal Prompt is a full-page redirect; legacy Duo is an iframe)
    duo_host_regex: str = r"duosecurity\.com|\.duo\.com"
    # AnyConnect's local SAML callback listener
    callback_host_regex: str = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)"
    # Any URL AnyConnect hands to the browser for the SAML handshake. Used to
    # recognise the external-browser URL when we capture it from a process
    # command line.
    saml_url_regex: str = r"SAMLRequest|/idp/|/sso/|/as/authorization|/\+CSCOE\+/saml"

    # Browser
    cdp_port: int = 9222
    browser_exe: str = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    # Prefer the OS default browser exe (Chrome/Edge) over browser_exe. The
    # default browser is what AnyConnect's ShellExecute launches, so driving
    # the same binary lets the SAML tab land in the window we control.
    use_default_browser: bool = True
    # Temporarily point the default-browser ProgId command at our automated
    # instance so AnyConnect's SAML tab opens inside it. Restored on exit.
    hijack_default_browser: bool = True
    browser_user_data_dir: str = ""             # temp dir created at runtime if empty

    # Behaviour
    trust_browser: bool = True                  # answer Duo's "trust this device" prompt

    # Timeouts (seconds)
    ping_page_timeout: int = 60                 # wait for the SAML tab to appear
    saml_fill_timeout: int = 20                 # wait for form fields
    duo_action_timeout: int = 20                # wait for a Duo button/screen
    duo_wait_timeout: int = 120                 # user approves Duo push
    callback_timeout: int = 60                  # SAML redirect back to AnyConnect
    connected_timeout: int = 90                 # vpncli state -> Connected
    banner_timeout: int = 30                    # Accept dialog wait

    selectors: Selectors = field(default_factory=Selectors)
    duo_selectors: DuoSelectors = field(default_factory=DuoSelectors)

    def ping_host_pattern(self) -> re.Pattern[str]:
        return re.compile(self.ping_host_regex, re.IGNORECASE)

    def duo_host_pattern(self) -> re.Pattern[str]:
        return re.compile(self.duo_host_regex, re.IGNORECASE)

    def callback_pattern(self) -> re.Pattern[str]:
        return re.compile(self.callback_host_regex, re.IGNORECASE)

    def saml_url_pattern(self) -> re.Pattern[str]:
        return re.compile(self.saml_url_regex, re.IGNORECASE)

    def login_page_regex(self) -> str:
        """Regex matching any page the SAML login can start on (IdP or Duo)."""
        return f"({self.ping_host_regex})|({self.duo_host_regex})"


def _filter_known(raw: dict, cls) -> dict:
    """Drop keys that aren't fields of `cls` so an old config file still loads."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in known}


def load_config(path: Path | None = None) -> Config:
    """Load config from JSON file, falling back to defaults."""
    p = path or CONFIG_PATH
    if not p.exists():
        return Config()
    raw = json.loads(p.read_text(encoding="utf-8"))
    sel = Selectors(**_filter_known(raw.pop("selectors", {}), Selectors))
    duo = DuoSelectors(**_filter_known(raw.pop("duo_selectors", {}), DuoSelectors))
    return Config(**_filter_known(raw, Config), selectors=sel, duo_selectors=duo)


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Write config to JSON (human-editable)."""
    p = path or CONFIG_PATH
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
