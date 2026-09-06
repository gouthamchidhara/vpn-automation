# vpn-auto-login

Automated Cisco AnyConnect + Ping Identity + Duo login tool.

## How it works

1. Launches Edge/Chrome with CDP debug port
2. Starts `vpncli.exe connect <host>` → AnyConnect opens Ping SAML in external browser
3. Playwright attaches to the Ping tab, fills username + password from Windows Credential Manager
4. Waits for you to approve Duo push on your phone
5. Detects SAML callback redirect → confirms VPN tunnel Connected

## Setup

```powershell
cd C:\Users\868977\vpn-auto-login
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
```

## Store credentials (encrypted in Windows Credential Manager)

```powershell
python -m src --setup
```

## Run

```powershell
python -m src
```

## Options

```
--setup              Store credentials in Credential Manager
--record-selectors   Dump current Ping DOM selectors for config
--no-submit          Dry run: fill creds but don't submit
--disconnect         Disconnect VPN
-u, --username       VPN username (prompted if not stored)
--config PATH        Custom config file path
-v, --verbose        Debug logging
--text-log           Human-readable log format
```

## Configuration

Edit `vpn-config.json` (created on first `--setup` run) to set:
- `vpn_host` — your VPN gateway hostname
- `ping_host_regex` — regex matching your Ping Identity login URL
- `selectors` — CSS selectors for the login form fields
- Timeouts, browser path, etc.

## Requirements

- Windows 10/11
- Cisco AnyConnect 4.10+ with `<EnableExternalBrowser>true</EnableExternalBrowser>`
- Python 3.10+
- Edge or Chrome (default browser)
