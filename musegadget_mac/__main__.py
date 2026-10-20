"""`python -m musegadget_mac <command>`: the SDK's CLI with the macOS Bluetooth server.

The SDK token is read from the macOS Keychain (service ``muse-gadget-sdk-token``)
so it never sits in a file, an environment file or shell history.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import musegadget_mac  # noqa: F401  (registers the macOS BleServer and state dir)

KEYCHAIN_SERVICE = "muse-gadget-sdk-token"


def _token_from_keychain() -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.environ.get("MUSEGADGET_SDK_TOKEN"):
        token = _token_from_keychain()
        if token:
            os.environ["MUSEGADGET_SDK_TOKEN"] = token
    from musegadget.cli import main as sdk_main

    return sdk_main()


if __name__ == "__main__":
    sys.exit(main())
