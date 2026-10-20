"""Ways to put one message to Muse and follow its answer.

``LiveBackend`` keeps this Mac connected to your Muse as a paired gadget (the
SDK's ``Service``) and runs each request through a ``MuseTurn`` on the glasses'
main chat. ``FakeBackend`` answers locally, for development and tests.

Both yield the same small event dicts: ``{"type": "status", "text"}``,
``{"type": "reply", "text", "done"}`` and finally ``{"type": "done"}`` or
``{"type": "error", "text"}``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Protocol

log = logging.getLogger(__name__)

# The glasses talk in Muse's main chat (None). Side threads were tried first (spec D7):
# Muse accepts messages there but its replies do not reach a gadget's subscription.
GLASSES_SESSION_ID = None
TURN_TIMEOUT_S = 600.0  # big tasks (booking, research) run for minutes
# "voice" asks Muse for a spoken-style reply whose audio stream_tts can fetch.
OUTPUT_MODALITY = os.environ.get("MUSE_BRIDGE_MODALITY", "text")  # "voice" breaks replies (SDK issue #6)
_POLL_S = 0.25
SUBSCRIBE_WAIT_S = 8.0      # wait this long for the chat stream before giving up on a request
STALL_S = 45.0              # answered, then silent this long: treat as done (a lost message_done)
NO_ANSWER_QUIET_S = 20.0    # task reported finished but nothing said: give up after this quiet
FOLLOW_UP_S = 600.0         # keep listening for late replies to a finished request


_QUIET_CODES = {"online", "idle"}


def activity_from(raw: dict) -> str | None:
    """A short line about what Muse is doing, from an agent/task status event."""
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if raw.get("event") == "agent.status":
        code = str(payload.get("activity_code") or "")
        text = str(payload.get("activity_text") or code).strip()
        if text and code not in _QUIET_CODES:
            return text[:120]
    if raw.get("event") == "task.status":
        helpers = payload.get("active_subagent_count")
        if isinstance(helpers, int) and helpers > 0:
            return f"{helpers} helper{'s' if helpers != 1 else ''} working"
    return None


def approvals_from(raw: dict) -> list[dict] | None:
    """Pending approvals from an approvals.snapshot, trimmed for the lens."""
    if raw.get("event") != "approvals.snapshot":
        return None
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    items = []
    for approval in payload.get("pending_approvals") or []:
        if not isinstance(approval, dict):
            continue
        display = approval.get("display") if isinstance(approval.get("display"), dict) else {}
        rows = [f"{row.get('label')}: {row.get('value')}" for row in display.get("detail_rows") or []
                if isinstance(row, dict) and row.get("label") and row.get("value")]
        items.append({"id": str(approval.get("approval_id", ""))[:80],
                      "action": str(display.get("action_label") or "Muse needs your OK")[:80],
                      "details": [r[:120] for r in rows[:4]]})
    return items


class Backend(Protocol):
    def ask(self, text: str, request_id: str = "") -> AsyncIterator[dict]: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def connected(self) -> bool: ...


class FakeBackend:
    """Streams a canned answer, so the API and the glasses app can be built without Muse."""

    def __init__(self, delay_s: float = 0.4) -> None:
        self._delay = delay_s

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def connected(self) -> bool:
        return True

    def set_global_sink(self, sink) -> None:
        self._sink = sink

    async def ask(self, text: str, request_id: str = "") -> AsyncIterator[dict]:
        yield {"type": "status", "text": "thinking"}
        for step in ("Checking your calendar", "Drafting the invite", "2 helpers working"):
            await asyncio.sleep(self._delay)
            yield {"type": "activity", "text": step}
        if "approve" in text.lower() and getattr(self, "_sink", None):
            await self._sink({"type": "approvals", "items": [{"id": "fake", "action": "Send email",
                              "details": ["To: harsh@example.com", "Subject: Meeting at 7 PM"]}]}, "muse")
        await asyncio.sleep(self._delay)
        heard = text.split("]\n", 1)[-1]  # drop the glasses hint the bridge adds
        answer = f"Practice mode. Your real Muse isn't connected yet. I heard: {heard}"
        words, sent = answer.split(" "), ""
        for word in words:
            sent = f"{sent} {word}".strip()
            yield {"type": "reply", "text": sent, "done": False, "message_id": "fake-" + request_id}
            await asyncio.sleep(self._delay / 8)
        yield {"type": "reply", "text": sent, "done": True, "message_id": "fake-" + request_id}
        yield {"type": "done"}


class LiveBackend:
    """Your Muse, through this Mac's gadget pairing."""

    def __init__(self, sdk_token: str | None) -> None:
        # Imported here so the fake backend and the tests need no pairing state.
        from musegadget import identity

        from muse_bridge.service import BridgeService

        self._service = BridgeService(identity=identity.load_or_create(), sdk_token=sdk_token)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._watcher: asyncio.Task | None = None
        self._followers: set[asyncio.Task] = set()
        self._sink = None
        self._last_approvals: list | None = None

    def set_global_sink(self, sink) -> None:
        """``sink(event, request_id)`` posts to the feed outside a running request."""
        self._sink = sink

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._service.run())
        self._watcher = asyncio.ensure_future(self._watch_approvals())

    async def stop(self) -> None:
        for task in (self._watcher, *self._followers):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self._watcher, *self._followers) if t), return_exceptions=True)
        self._service.stop()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    def connected(self) -> bool:
        return self._service.ready()

    async def _watch_approvals(self) -> None:
        queue = self._service.listen()
        try:
            while True:
                raw = await queue.get()
                try:
                    items = approvals_from(raw)
                    if items is None or items == self._last_approvals:
                        continue
                    self._last_approvals = items
                    if self._sink:
                        await self._sink({"type": "approvals", "items": items}, "muse")
                except Exception:
                    log.exception("approvals watcher: bad event skipped")
        finally:
            self._service.unlisten(queue)

    async def audio(self, message_id: str):
        """Muse's own MP3 for one voice-modality reply, as it streams."""
        session = self._service.session()
        if session is None:
            raise ConnectionError("Not connected to Muse")
        async for chunk in session.stream_tts(message_id):
            yield chunk

    async def _ready_session(self):
        """The registered session once its chat subscription is up, or None after a short wait."""
        deadline = time.monotonic() + SUBSCRIBE_WAIT_S
        while time.monotonic() < deadline:
            session = self._service.session()
            if session is not None and session.chat_subscribed.is_set():
                return session
            await asyncio.sleep(0.2)
        return None

    async def ask(self, text: str, request_id: str = "") -> AsyncIterator[dict]:
        from musegadget.muse_turn import AgentStatus, MuseTurn, Reply

        # One open turn at a time. The glasses share Muse's main chat with the
        # phone app, so replies are only shown when they answer this message.
        async with self._lock:
            session = await self._ready_session()
            if session is None:
                yield {"type": "error", "text": "Beru can't reach Muse right now. Try again in a moment."}
                return
            turn = MuseTurn(GLASSES_SESSION_ID)
            events = self._service.listen()
            handed_off = False
            try:
                response = await session.send_chat(text, GLASSES_SESSION_ID, output_modality=OUTPUT_MODALITY)
                body = response.get("response")
                if not response.get("ok") or not isinstance(body, dict):
                    log.warning("Muse refused the message: HTTP %s", response.get("status"))
                    yield {"type": "error", "text": "Muse didn't accept that message. Try again in a moment."}
                    return
                pending = turn.acknowledge(body)
                own_ids = _ack_ids(body)
                started = last_event = time.monotonic()
                foreign_user_seen = False
                shown: dict[str, tuple[str, bool]] = {}
                last_activity = None
                while True:
                    for item in pending:
                        if isinstance(item, AgentStatus) and (item.activity_text or item.activity_code):
                            yield {"type": "status", "text": item.activity_text or item.activity_code}
                        elif isinstance(item, Reply) and item.text and (item.bound or not foreign_user_seen):
                            if shown.get(item.message_id) != (item.text, item.done):
                                shown[item.message_id] = (item.text, item.done)
                                yield {"type": "reply", "text": item.text, "done": item.done,
                                       "message_id": item.message_id}
                    now = time.monotonic()
                    answered = bool(shown)
                    if answered and turn.complete(now):
                        break
                    if answered and now - last_event > STALL_S:
                        log.info("turn quiet for %.0fs after an answer; ending it", now - last_event)
                        break
                    if not answered and turn.task_finished and now - last_event > NO_ANSWER_QUIET_S:
                        yield {"type": "error", "text": "Beru finished without a reply. Check the Muse app."}
                        return
                    if now - started > TURN_TIMEOUT_S:
                        yield {"type": "error", "text": "No answer after 10 minutes. Check the Muse app; it may still be working."}
                        return
                    if self._service.session() is not session or not session.chat_subscribed.is_set():
                        yield {"type": "error", "text": "Beru lost its connection to Muse while working. Check the Muse app."}
                        return
                    try:
                        raw = await asyncio.wait_for(events.get(), _POLL_S)
                    except asyncio.TimeoutError:
                        pending = []
                        continue
                    last_event = time.monotonic()
                    if _is_foreign_user_message(raw, own_ids):
                        foreign_user_seen = True
                    pending = turn.feed(raw)
                    activity = None if foreign_user_seen else activity_from(raw)
                    if activity and activity != last_activity:
                        last_activity = activity
                        yield {"type": "activity", "text": activity}
                yield {"type": "done"}
                # Muse often says "on it" and posts the real result minutes later:
                # keep following this message's thread in the background.
                if self._sink and request_id:
                    follower = asyncio.ensure_future(self._follow(turn, events, shown, request_id))
                    self._followers.add(follower)
                    follower.add_done_callback(self._followers.discard)
                    handed_off = True
            finally:
                if not handed_off:
                    self._service.unlisten(events)

    async def _follow(self, turn, events: asyncio.Queue, shown: dict, request_id: str) -> None:
        from musegadget.muse_turn import Reply

        deadline = time.monotonic() + FOLLOW_UP_S
        try:
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(events.get(), 5.0)
                except asyncio.TimeoutError:
                    continue
                for item in turn.feed(raw):
                    # Late replies must be proven to answer this message.
                    if isinstance(item, Reply) and item.bound and item.done and item.text \
                            and shown.get(item.message_id) != (item.text, True):
                        shown[item.message_id] = (item.text, True)
                        await self._sink({"type": "reply", "text": item.text, "done": True, "late": True,
                                          "message_id": item.message_id},
                                         request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("follow-up for %s stopped", request_id)
        finally:
            self._service.unlisten(events)


def _ack_ids(body: dict) -> set[str]:
    result = body.get("result", body)
    if not isinstance(result, dict):
        return set()
    return {v for k in ("message_id", "reply_to_message_id") if isinstance(v := result.get(k), str) and v}


def _is_foreign_user_message(raw: dict, own_ids: set[str]) -> bool:
    """A user message in the main chat that isn't ours, e.g. typed in the phone app."""
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if raw.get("event") != "message.user" and payload.get("role") != "user":
        return False
    message_id = payload.get("message_id") or payload.get("id")
    return isinstance(message_id, str) and message_id not in own_ids
