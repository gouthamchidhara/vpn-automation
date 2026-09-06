"""Double-click entry point for the packaged .exe.

Runs the full CLI flow, then keeps the console window open so the user can
read the final status (Connected / error) before it closes.
"""
from __future__ import annotations
import sys

from src.main import cli


def main() -> None:
    code = 0
    try:
        cli()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    if getattr(sys, "frozen", False):
        print("\nPress Enter to close this window...")
        try:
            input()
        except EOFError:
            pass
    sys.exit(code)


if __name__ == "__main__":
    main()
