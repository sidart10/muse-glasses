"""The SDK's gadget ``Service``, narrowed for the glasses bridge.

Differences from the stock Linux service:

* Muse is offered **no commands**. The stock executor's ``system.run``,
  ``file.read`` and ``file.write`` are never registered, and any invoke is refused.
* While a session is up, Muse's chat event stream (all chats; the bridge uses the main
  chat) is read continuously and fanned out to whoever is listening (one turn at a time).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from musegadget import __version__
from musegadget.link_client import DeviceDescription, LinkSession, Outcome
from musegadget.service import DEFAULT_NOISE_HOST, Service

from muse_bridge.backends import GLASSES_SESSION_ID

log = logging.getLogger(__name__)

# Muse may call these on this device. Deliberately empty: Muse gets no shell, files or other commands.
ALLOWED_COMMANDS: dict = {}
_RESUBSCRIBE_S = 2.0


def refuse_command(command: str, params: dict, timeout_ms: int | None = None) -> dict:
    log.warning("refused a command Muse asked for: %s", command)
    return {"ok": False, "error": {"code": "unsupported", "message": "this device runs no commands"}}


# MUSE_BRIDGE_EVENT_TAP=1 appends every raw chat event to a private file, for debugging.
_TAP_FILE = Path.home() / "Library" / "Logs" / "muse-bridge-events.jsonl"


def _tap(event: dict) -> None:
    if os.environ.get("MUSE_BRIDGE_EVENT_TAP") != "1":
        return
    with open(_TAP_FILE, "a", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
        fh.write(json.dumps(event)[:4000] + "\n")


class _NoExecutor:
    run = staticmethod(refuse_command)


class BridgeService(Service):
    def __init__(self, *, identity, sdk_token: str | None) -> None:
        super().__init__(identity=identity, executor=_NoExecutor(), sdk_token=sdk_token,
                         display_name="Glasses bridge")
        self._listeners: set[asyncio.Queue] = set()

    # -- for LiveBackend ---------------------------------------------------------

    def session(self) -> LinkSession | None:
        session = self._current
        return session if session is not None and session.registered_at is not None else None

    def ready(self) -> bool:
        session = self.session()
        return session is not None and session.chat_subscribed.is_set()

    def listen(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._listeners.add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    # -- Service override ----------------------------------------------------------

    async def _session(self, vm: dict, pairing: dict) -> tuple[Outcome, float]:
        device = DeviceDescription(
            node_id=self.identity.node_id,
            display_name=self.display_name,
            version=__version__,
            commands=ALLOWED_COMMANDS,
        )
        session = LinkSession(
            noise_host=pairing.get("noise_host") or DEFAULT_NOISE_HOST,
            vm_id=vm["vm_id"] or vm["vm_name"],
            vm_auth_token=vm["vm_auth_token"],
            device=device,
            run_command=refuse_command,
        )
        log.info("connecting to %s", vm["vm_name"] or vm["vm_id"])
        started = time.monotonic()
        self._current = session
        reader = asyncio.ensure_future(self._read_chat(session))
        try:
            outcome = await session.run(self._stop)
        except Exception as exc:
            log.warning("session failed: %s: %s", type(exc).__name__, exc)
            outcome = Outcome.CLOSED
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            self._current = None
        lasted = time.monotonic() - (session.registered_at or time.monotonic())
        log.info("session ended: %s after %.0fs", outcome.value, time.monotonic() - started)
        return outcome, lasted

    async def _read_chat(self, session: LinkSession) -> None:
        await session.registered.wait()
        while True:
            try:
                async for event in session.subscribe_chat():  # all chats, like the firmware; MuseTurn filters
                    _tap(event)
                    for queue in list(self._listeners):
                        if queue.full():
                            log.warning("listener backed up; dropped a %s event", event.get("event"))
                        else:
                            queue.put_nowait(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("chat subscription ended: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(_RESUBSCRIBE_S)
