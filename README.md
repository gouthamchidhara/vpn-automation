# vpn-auto-login

Automated Cisco AnyConnect + Ping Identity + Duo login tool.

## How it works

1. Reads your username/password from Windows Credential Manager — no prompts
   once they are stored
2. Launches your default browser on a clean temp profile with a CDP debug port
3. Temporarily points the Windows `https://` handler at that instance, so the
   SAML tab AnyConnect opens lands in the browser the tool can drive (restored
   on exit)
4. Starts the AnyConnect GUI and clicks Connect — AnyConnect opens the Ping
   SAML page
5. Fills username + password on the Ping page
6. Sends the Duo push, prints the verification number Duo shows (when your
   policy uses verified push), and waits for you to approve on your phone
7. On approval: answers Duo's "trust this device" prompt, follows the SAML
   callback back to AnyConnect, accepts the banner, and confirms the tunnel
   reaches Connected. On denial or timeout it stops and says why

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

`--setup` is the only command that asks about overwriting an existing
credential. A normal run never does: it uses the remembered username and the
stored password, and only asks (once, then remembers) if nothing is stored yet.

## Run

```powershell
python -m src
```

## Options

```
--setup              Store credentials in Credential Manager
--record-selectors   Dump current Ping/Duo DOM selectors for config
--no-submit          Dry run: fill creds but don't submit
--disconnect         Disconnect VPN
-u, --username       VPN username (defaults to the remembered one)
--config PATH        Custom config file path
-v, --verbose        Debug logging
--text-log           Human-readable log format
```

## Configuration

Edit `vpn-config.json` (created on first `--setup` run) to set:

| Key | Purpose |
| --- | --- |
| `vpn_host` | Your VPN gateway hostname |
| `vpn_profile` | AnyConnect profile name shown in the GUI dropdown |
| `ping_host_regex` | Regex matching your Ping Identity login URL |
| `duo_host_regex` | Regex matching the Duo prompt URL |
| `callback_host_regex` | AnyConnect's local SAML callback listener |
| `selectors` | CSS selectors for the login form fields |
| `duo_selectors` | CSS selectors for the Duo prompt buttons |
| `use_default_browser` | Automate the OS default browser (recommended) |
| `hijack_default_browser` | Route the SAML tab into the automated browser |
| `trust_browser` | Answer Duo's "trust this device" prompt with yes |
| `*_timeout` | Per-phase timeouts in seconds |

A stale `ping_host_regex` no longer blocks the login: the tool takes the tab
AnyConnect opens whatever its hostname is, and only uses the regex to
recognise the page faster.

## How the SAML tab is captured

AnyConnect opens the SAML URL with `ShellExecute`, which always launches your
*default* browser with its *default* profile — a window Playwright cannot
attach to (Chrome/Edge 136+ refuse `--remote-debugging-port` on the default
profile directory). Three mechanisms, in order:

1. **Handler override.** `HKCU\Software\Classes\<ProgId>\shell\open\command` is
   pointed at the same exe with our `--user-data-dir`. Chromium treats the
   profile directory as its single-instance key, so the URL is handed to the
   already-running automated instance as a new tab. The original command is
   saved and restored on exit; a leftover from a crashed run is cleaned up at
   the next start.
2. **Command-line recovery.** If the override does not take, the URL is read
   from the other browser's process command line and loaded here instead.
3. **Gateway fallback.** Failing both, the tool opens `https://<vpn_host>/` and
   follows the redirect to the IdP.

Your own browser windows are never closed — only a stale listener on the CDP
port is cleared.

## Troubleshooting

- **Duo shows a number but nothing happens** — the number is printed in the
  console too; tap it in the Duo app.
- **"Could not reach the SAML login page"** — run with `-v` and check whether
  the handler override installed; a locked-down HKCU falls back to the
  command-line recovery.
- **Login stops with "Duo push was denied"** — nothing else is attempted by
  design; rerun the tool.
- **Wrong password** — the IdP's own error text is reported and the run stops
  before Duo. Update it with `update_password_gui.py` or `--setup`.

## Requirements

- Windows 10/11
- Cisco AnyConnect 4.10+ with `<EnableExternalBrowser>true</EnableExternalBrowser>`
- Python 3.10+
- Edge or Chrome (default browser)

## Tests

```powershell
pytest -q
```

`tests/test_banner.py` needs `pywinauto`, so those two tests only run on
Windows.
