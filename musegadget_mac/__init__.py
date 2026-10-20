"""macOS port of Meta's Muse Gadget Linux SDK (vendored unchanged in vendor/muse-gadget-sdk).

Importing this package registers the CoreBluetooth server as
``musegadget.ble_server`` so the SDK's lazy import picks it up instead of the
BlueZ one, and points the SDK's state at a per-user macOS directory.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

STATE_DIR = Path.home() / "Library" / "Application Support" / "musegadget"
os.environ.setdefault("MUSEGADGET_STATE_DIR", str(STATE_DIR))
os.environ.setdefault("MUSEGADGET_SOCKET", str(STATE_DIR / "musegadget.sock"))
STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

from musegadget_mac.ble_server_macos import BleServer  # noqa: E402

_shim = types.ModuleType("musegadget.ble_server")
_shim.BleServer = BleServer
sys.modules["musegadget.ble_server"] = _shim
