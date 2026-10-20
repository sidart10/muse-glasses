"""macOS (CoreBluetooth) stand-in for the SDK's BlueZ-only ``ble_server.BleServer``.

Implements the same setup Transport (``mtu``, ``send_packets``, ``disconnect``)
and lifecycle (``run`` blocks until ``stop``), with the same service and
characteristic UUIDs, so ``musegadget.pair.pair`` drives it unchanged. Like the
Linux server it asks for no Bluetooth bonding: the Muse app's own ECDH pairing
runs over plain GATT writes and notifications.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Callable

from bless import (
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

from musegadget.ble_framing import CHUNK_STAGGER_S, MAX_PACKET_BYTES

log = logging.getLogger(__name__)

SERVICE_UUID = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"

# CoreBluetooth does not report the negotiated ATT MTU to a peripheral; iPhones
# negotiate well above this, and the framing never sends more than MAX_PACKET_BYTES.
_ASSUMED_MTU = MAX_PACKET_BYTES + 3
_CONNECTION_POLL_S = 0.5
_NOTIFY_RETRY_S = 0.02
_NOTIFY_RETRIES = 100


class BleServer:
    """Owns a CoreBluetooth peripheral while setup is open; implements the setup Transport."""

    def __init__(
        self,
        local_name: str,
        on_write: Callable[[bytes], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        self._local_name = local_name
        self._on_write = on_write
        self._on_disconnect = on_disconnect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: BlessServer | None = None
        self._stopped: asyncio.Event | None = None
        self._ready = threading.Event()
        self._send_lock = threading.Lock()

    # -- Transport --------------------------------------------------------------

    def mtu(self) -> int:
        return _ASSUMED_MTU

    def send_packets(self, packets: list[bytes]) -> None:
        self._ready.wait()
        with self._send_lock:
            for i, packet in enumerate(packets):
                if i:
                    time.sleep(CHUNK_STAGGER_S)
                future = asyncio.run_coroutine_threadsafe(self._notify(packet), self._loop)
                if not future.result():
                    log.warning("notification dropped after %d retries", _NOTIFY_RETRIES)

    def disconnect(self, delay: float) -> None:
        # A CoreBluetooth peripheral cannot drop a central. Withdrawing the
        # service ends setup the same way from the phone's point of view.
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.call_later, delay, self._request_stop)

    # -- Lifecycle --------------------------------------------------------------

    def run(self) -> None:
        """Start advertising and serve until :meth:`stop`. Blocks."""
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()

    def stop(self) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._request_stop)

    # -- Internals (event-loop thread) -----------------------------------------

    def _request_stop(self) -> None:
        if self._stopped:
            self._stopped.set()

    async def _serve(self) -> None:
        self._stopped = asyncio.Event()
        server = self._server = BlessServer(name=self._local_name, loop=self._loop)
        server.write_request_func = self._handle_write
        server.read_request_func = lambda characteristic, **_: characteristic.value
        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID,
            RX_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.writeable,
        )
        await server.add_new_characteristic(
            SERVICE_UUID,
            TX_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )
        # MUSEGADGET_ADV=name (default; the Muse app needs it) advertises the full name and no service UUID (fits
        # one packet); "both" keeps the UUID, but then macOS drops the name and the app sees nothing.
        # macOS cannot advertise manufacturer data either way.
        name_only = os.environ.get("MUSEGADGET_ADV", "name") == "name"
        await server.start(prioritize_local_name=name_only)
        log.info("advertising as %s (%s)", self._local_name, "name only" if name_only else "name + service UUID")
        self._ready.set()
        watcher = asyncio.ensure_future(self._watch_connection())
        try:
            await self._stopped.wait()
        finally:
            watcher.cancel()
            await server.stop()
            log.info("advertising stopped")

    async def _notify(self, packet: bytes) -> bool:
        characteristic = self._server.get_characteristic(TX_UUID)
        characteristic.value = bytearray(packet)
        for _ in range(_NOTIFY_RETRIES):
            # False means CoreBluetooth's transmit queue is full; it drains quickly.
            if self._server.update_value(SERVICE_UUID, TX_UUID):
                return True
            await asyncio.sleep(_NOTIFY_RETRY_S)
        return False

    async def _watch_connection(self) -> None:
        connected = False
        while True:
            now = await self._server.is_connected()
            if now != connected:
                log.info("central %s", "subscribed" if now else "unsubscribed")
                if connected and not now:
                    self._on_disconnect()
                connected = now
            await asyncio.sleep(_CONNECTION_POLL_S)

    def _handle_write(self, characteristic, value, **_) -> None:
        if str(characteristic.uuid).lower() == RX_UUID:
            self._on_write(bytes(value))
