"""Where the bridge keeps its secrets and switches.

Secrets live in the macOS Keychain (never in files): the Muse SDK token and the
API token the glasses send. The kill switch is a flag file, so it works even if
the Keychain is locked. Rotating the API token touches a revision file, so a
running bridge (another process) picks up the new token on its next request.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

SDK_TOKEN_SERVICE = "muse-gadget-sdk-token"
API_TOKEN_SERVICE = "muse-bridge-api-token"
STATE_DIR = Path(os.environ.get("MUSE_BRIDGE_STATE_DIR")
                 or Path.home() / "Library" / "Application Support" / "muse-bridge")
DISABLED_FLAG = STATE_DIR / "disabled"
TOKEN_REVISION = STATE_DIR / "token-revision"
_KEYCHAIN_NOT_FOUND = 44  # `security` exit status for a missing item

_cache: dict = {"token": None, "revision": None}


class KeychainError(RuntimeError):
    pass


def _keychain_get(service: str) -> str | None:
    """The item's secret; None only when the item doesn't exist. Other failures raise."""
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeychainError(f"Keychain read failed: {type(exc).__name__}") from exc
    if out.returncode == _KEYCHAIN_NOT_FOUND:
        return None
    if out.returncode != 0:
        raise KeychainError(f"Keychain read failed (status {out.returncode})")
    return out.stdout.strip() or None


def _keychain_set(service: str, value: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-a", os.environ.get("USER", "muse-glasses"),
                    "-s", service, "-w", value], check=True, capture_output=True, timeout=10)


def _revision() -> float | None:
    try:
        return TOKEN_REVISION.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def sdk_token() -> str | None:
    return os.environ.get("MUSEGADGET_SDK_TOKEN") or _keychain_get(SDK_TOKEN_SERVICE)


def api_token() -> str:
    """The bearer token the glasses must send; created on first use, re-read after a rotation."""
    if os.environ.get("MUSE_BRIDGE_API_TOKEN"):
        return os.environ["MUSE_BRIDGE_API_TOKEN"]
    revision = _revision()
    if _cache["token"] is None or _cache["revision"] != revision:
        token = _keychain_get(API_TOKEN_SERVICE)
        if token is None:
            token = rotate_api_token()
            revision = _revision()
        _cache.update(token=token, revision=revision)
    return _cache["token"]


def rotate_api_token() -> str:
    token = secrets.token_urlsafe(32)
    _keychain_set(API_TOKEN_SERVICE, token)
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    TOKEN_REVISION.touch()
    os.utime(TOKEN_REVISION)  # bump mtime even when the file already existed
    _cache.update(token=token, revision=_revision())
    return token


def disabled() -> bool:
    return DISABLED_FLAG.exists()


def set_disabled(off: bool) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if off:
        DISABLED_FLAG.touch()
    else:
        DISABLED_FLAG.unlink(missing_ok=True)
