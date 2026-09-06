"""Configuration — VPN host, Ping selectors, timeouts. NO secrets here."""
from __future__ import annotations
import json
import re
import sys
from dataclasses import dataclass, field, asdict
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

    # Browser
    cdp_port: int = 9222
    browser_exe: str = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser_user_data_dir: str = ""             # temp dir created at runtime if empty

    # Timeouts (seconds)
    ping_page_timeout: int = 30                 # wait for Ping tab to appear
    saml_fill_timeout: int = 15                 # wait for form fields
    duo_wait_timeout: int = 120                 # user approves Duo push
    connected_timeout: int = 60                 # vpncli state -> Connected
    banner_timeout: int = 30                    # Accept dialog wait

    selectors: Selectors = field(default_factory=Selectors)

    def ping_host_pattern(self) -> re.Pattern[str]:
        return re.compile(self.ping_host_regex, re.IGNORECASE)


def load_config(path: Path | None = None) -> Config:
    """Load config from JSON file, falling back to defaults."""
    p = path or CONFIG_PATH
    if not p.exists():
        return Config()
    raw = json.loads(p.read_text(encoding="utf-8"))
    sel = Selectors(**raw.pop("selectors", {}))
    return Config(**raw, selectors=sel)


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Write config to JSON (human-editable)."""
    p = path or CONFIG_PATH
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
