"""Credential storage via Windows Credential Manager (DPAPI-encrypted).

Usage:
    --setup          interactive: prompt for username/password, store in keyring
    get(user)        retrieve password for default service
    store(user, pw)  store password
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
    """Interactive credential setup. Returns (username, password)."""
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

    password = getpass.getpass("VPN Password: ")
    if not password:
        print("Error: password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    store_password(username, password)
    print(f"✓ Credentials stored for '{username}'.")
    return username, password


def get_or_setup(username: str | None = None) -> tuple[str, str]:
    """Get creds from keyring, or run setup if missing."""
    if username:
        pw = get_password(username)
        if pw:
            return username, pw
        print(f"No stored credentials for '{username}'. Running setup...")
        return setup()
    # No username given — check if any exist, otherwise setup
    return setup()
