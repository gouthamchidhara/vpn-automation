"""Credential storage via Windows Credential Manager (DPAPI-encrypted).

Usage:
    --setup                     interactive: prompt for username/password, store
    get_password(user)          retrieve password
    store_password(user, pw)    store password
    resolve(hint, last_user)    non-interactive when creds already exist
"""
from __future__ import annotations
import getpass
import sys
import keyring

SERVICE_NAME = "vpn-auto-login"


def get_password(username: str) -> str | None:
    """Retrieve password from Windows Credential Manager."""
    return keyring.get_password(SERVICE_NAME, username)


def store_password(username: str, password: str) -> None:
    """Store password in Windows Credential Manager."""
    keyring.set_password(SERVICE_NAME, username, password)


def setup() -> tuple[str, str]:
    """Interactive credential setup (only for --setup). Returns (username, password)."""
    print(f"Storing credentials in Windows Credential Manager (service: {SERVICE_NAME})")
    print("These are encrypted per-user via DPAPI. No plaintext on disk.\n")
    username = input("VPN Username: ").strip()
    if not username:
        print("Error: username cannot be empty.", file=sys.stderr)
        sys.exit(1)

    existing = get_password(username)
    if existing:
        choice = input(f"Credentials for '{username}' already exist. Overwrite? [y/N]: ").strip().lower()
        if choice != "y":
            print("Kept existing credentials.")
            return username, existing

    password = _prompt_password(username)
    store_password(username, password)
    print(f"✓ Credentials stored for '{username}'.")
    return username, password


def _prompt_password(username: str) -> str:
    password = getpass.getpass(f"VPN Password for '{username}': ")
    if not password:
        print("Error: password cannot be empty.", file=sys.stderr)
        sys.exit(1)
    return password


def resolve(username: str | None = None, last_username: str = "") -> tuple[str, str]:
    """Return (username, password) for a normal run, prompting as little as possible.

    Username comes from --username, else the remembered one in the config. The
    password comes from Credential Manager. Nothing is asked when both are
    already known — a normal run must never stop for input, and never asks
    about overwriting a credential that is already correct.
    """
    user = (username or last_username or "").strip()
    if not user:
        user = input("VPN Username: ").strip()
        if not user:
            print("Error: username cannot be empty.", file=sys.stderr)
            sys.exit(1)

    password = get_password(user)
    if password:
        return user, password

    # First run for this username (or the credential was deleted): ask once and
    # store it, so later runs are fully unattended.
    print(f"No stored password for '{user}' — asking once, then remembering it.")
    password = _prompt_password(user)
    store_password(user, password)
    print(f"✓ Credentials stored for '{user}'. Future runs will not prompt.")
    return user, password


def get_or_setup(username: str | None = None) -> tuple[str, str]:
    """Backwards-compatible alias for resolve()."""
    return resolve(username)
