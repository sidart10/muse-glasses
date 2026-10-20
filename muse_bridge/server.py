"""HTTP face of the bridge: the glasses Web App and its two API calls.

``POST /api/say {"text"}`` queues a message for Muse and returns at once (WebMCP
gives a tool 10 seconds). ``GET /api/feed?after=<seq>`` long-polls the events of
every request, oldest first. Static files under ``/`` are the built Web App and
need no token; everything under ``/api/`` needs ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import os
import re
import logging
import time
import uuid
from collections import deque
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiohttp.abc import AbstractAccessLogger

from muse_bridge import settings
from muse_bridge.backends import Backend

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
FEED_KEEP = 500
FEED_WAIT_S = 25.0
SAY_QUEUE_MAX = 5
RECENT_IDS_KEEP = 2000
SPOKEN_ANSWER_MAX = 400  # longer answers stay on the display rather than being read in full
ASK_WAIT_MAX_S = 8.5  # WebMCP abandons a tool call after 10 s; leave room for the trip back

# Prepended to every glasses message so Muse answers for a small lens and a TTS
# voice (spec D4). "Tell me more" style follow-ups still get longer answers.
GLASSES_HINT = ("[Sent from my Ray-Ban Display glasses by voice. Answer in 1 to 3 short "
                "spoken sentences, no markdown, no lists, unless I ask for more detail.]\n")


class Feed:
    """Bounded, sequence-numbered event log that long-pollers wait on."""

    def __init__(self) -> None:
        self._events: deque[dict] = deque(maxlen=FEED_KEEP)
        self._last_seq = 0
        self._changed = asyncio.Condition()

    def _next_seq(self) -> int:
        # Millisecond clock, so numbers keep rising across bridge restarts and a
        # client polling "after" an old number still sees new events.
        self._last_seq = max(self._last_seq + 1, int(time.time() * 1000))
        return self._last_seq

    async def add(self, request_id: str, event: dict) -> None:
        async with self._changed:
            self._events.append({"seq": self._next_seq(), "request_id": request_id,
                                 "at": time.time(), **event})
            self._changed.notify_all()

    def after(self, seq: int) -> list[dict]:
        return [event for event in self._events if event["seq"] > seq]

    async def wait_after(self, seq: int, timeout: float) -> list[dict]:
        async with self._changed:
            try:
                await asyncio.wait_for(self._changed.wait_for(lambda: bool(self.after(seq))), timeout)
            except asyncio.TimeoutError:
                pass
            return self.after(seq)


class Bridge:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.feed = Feed()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=SAY_QUEUE_MAX)
        self._worker: asyncio.Task | None = None
        self._ids: dict[str, None] = {}  # recent request ids, oldest first

    async def start(self) -> None:
        if hasattr(self.backend, "set_global_sink"):
            self.backend.set_global_sink(lambda event, request_id="muse": self.feed.add(request_id, event))
        await self.backend.start()
        self._worker = asyncio.ensure_future(self._work())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        await self.backend.stop()

    def seen_request(self, request_id: str) -> bool:
        return request_id in self._ids

    def submit(self, text: str, request_id: str | None = None) -> str:
        request_id = request_id or uuid.uuid4().hex[:12]
        self._queue.put_nowait((request_id, text))  # raises QueueFull when backed up
        self._ids[request_id] = None
        while len(self._ids) > RECENT_IDS_KEEP:
            self._ids.pop(next(iter(self._ids)))
        return request_id

    async def wait_for_answer(self, request_id: str, timeout: float) -> dict:
        """Wait up to ``timeout`` for this request to finish; returns its answer if it did."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        seen = 0
        replies: dict[str, str] = {}
        while True:
            remaining = deadline - loop.time()
            for event in self.feed.after(seen):
                seen = event["seq"]
                if event["request_id"] != request_id:
                    continue
                if event["type"] == "reply":
                    replies[event.get("message_id") or "reply"] = event.get("text", "")
                elif event["type"] == "done":
                    return {"status": "answered", "answer": "\n\n".join(t for t in replies.values() if t)}
                elif event["type"] == "error":
                    return {"status": "error", "answer": event.get("text", "")}
            if remaining <= 0:
                return {"status": "pending", "answer": ""}
            await self.feed.wait_after(seen, remaining)

    async def _work(self) -> None:
        while True:
            request_id, text = await self._queue.get()
            await self.feed.add(request_id, {"type": "sent", "text": text})
            if settings.disabled():  # kill switch also drops anything already queued
                await self.feed.add(request_id, {"type": "error", "text": "The bridge is switched off."})
                continue
            try:
                async for event in self.backend.ask(GLASSES_HINT + text, request_id):
                    await self.feed.add(request_id, event)
            except Exception as exc:
                log.exception("request %s failed", request_id)
                await self.feed.add(request_id, {"type": "error", "text": f"Bridge error: {type(exc).__name__}"})


def _authorized(request: web.Request) -> bool:
    expected = settings.api_token()
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


@web.middleware
async def _guard(request: web.Request, handler):
    if request.path.startswith("/api/"):
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if settings.disabled():
            return web.json_response({"error": "bridge is switched off"}, status=503)
    response = await handler(request)
    if request.path.startswith("/assets/"):
        # Vite fingerprints asset names, so a changed file is a new URL.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path.startswith(("/icons/", "/mascot/", "/fonts/")):
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        response.headers["Cache-Control"] = "no-store, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def _say(request: web.Request) -> web.Response:
    bridge: Bridge = request.app["bridge"]
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "expected JSON"}, status=400)
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"error": "expected {\"text\": \"...\"}"}, status=400)
    if len(text) > MAX_TEXT_CHARS:
        return web.json_response({"error": "message too long"}, status=413)
    try:
        request_id = bridge.submit(text.strip())
    except asyncio.QueueFull:
        return web.json_response({"error": "busy, try again in a moment"}, status=429)
    log.info("queued request %s (%d chars)", request_id, len(text))
    return web.json_response({"request_id": request_id, "status": "sent"})


_CLIENT_ID = re.compile(r"[a-f0-9]{12,32}")


async def _ask(request: web.Request) -> web.Response:
    """Voice path: queue a message and wait briefly, so Meta AI can speak a quick answer itself."""
    bridge: Bridge = request.app["bridge"]
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "expected JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected JSON object"}, status=400)
    text, client_id = body.get("text"), body.get("request_id")
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"error": "expected {\"text\": \"...\"}"}, status=400)
    if len(text) > MAX_TEXT_CHARS:
        return web.json_response({"error": "message too long"}, status=413)
    if client_id is not None and not (isinstance(client_id, str) and _CLIENT_ID.fullmatch(client_id)):
        return web.json_response({"error": "bad request_id"}, status=400)
    try:
        wait_ms = float(body.get("wait_ms", 0))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad wait_ms"}, status=400)
    if not math.isfinite(wait_ms):
        return web.json_response({"error": "bad wait_ms"}, status=400)
    wait = min(max(wait_ms / 1000, 0.0), ASK_WAIT_MAX_S)
    if client_id is not None and bridge.seen_request(client_id):
        return web.json_response({"error": "request_id already used"}, status=409)
    try:
        request_id = bridge.submit(text.strip(), client_id)
    except asyncio.QueueFull:
        return web.json_response({"error": "busy, try again in a moment"}, status=429)
    result = await bridge.wait_for_answer(request_id, wait) if wait else {"status": "pending", "answer": ""}
    # L6: Meta AI reads answers verbatim; long ones belong on the display, and raw
    # error text shouldn't be spoken as if Beru said it.
    if result["status"] == "answered" and len(result["answer"]) > SPOKEN_ANSWER_MAX:
        result = {"status": "pending", "answer": ""}
    elif result["status"] == "error":
        result = {"status": "error", "answer": "Sorry, I couldn't get that done. Details are on the display."}
    return web.json_response({"request_id": request_id, **result})


async def _feed(request: web.Request) -> web.Response:
    bridge: Bridge = request.app["bridge"]
    try:
        after = int(request.query.get("after", "0"))
        wait = min(float(request.query.get("wait", FEED_WAIT_S)), FEED_WAIT_S)
    except ValueError:
        return web.json_response({"error": "bad query"}, status=400)
    events = await bridge.feed.wait_after(after, wait) if wait > 0 else bridge.feed.after(after)
    return web.json_response({"connected": bridge.backend.connected(), "events": events})


async def _audio(request: web.Request) -> web.StreamResponse:
    backend = request.app["bridge"].backend
    if not hasattr(backend, "audio"):
        return web.json_response({"error": "no audio in this mode"}, status=404)
    message_id = request.match_info["message_id"]
    if not message_id.replace("-", "").isalnum() or len(message_id) > 128:
        return web.json_response({"error": "bad id"}, status=400)
    response = web.StreamResponse(headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-store"})
    try:
        async for chunk in backend.audio(message_id):
            if not response.prepared:
                await response.prepare(request)
            await response.write(chunk)
    except Exception as exc:
        log.warning("Muse audio for %s failed: %s: %s", message_id[:8], type(exc).__name__, exc)
        if not response.prepared:
            return web.json_response({"error": "no audio"}, status=502)
    if not response.prepared:
        return web.json_response({"error": "no audio"}, status=502)
    await response.write_eof()
    return response


SPEECH_CONCURRENCY = 2  # the current sentence + one prefetch; anything beyond is refused, not queued
VOICE_URL = os.environ.get("BERU_VOICE_URL", "http://127.0.0.1:8796/tts")


async def _speech(request: web.Request) -> web.Response:
    """One sentence of Beru's Kokoro voice as MP3. 503 means: use the glasses' own voice."""
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "expected JSON"}, status=400)
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        return web.json_response({"error": "text must be 1-600 characters"}, status=400)
    gate: asyncio.Semaphore = request.app["speech_gate"]
    if gate.locked():
        return web.json_response({"error": "voice busy"}, status=503)
    session: ClientSession = request.app["http"]
    try:
        async with gate, session.post(VOICE_URL, json={"text": text}, timeout=ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return web.json_response({"error": "voice unavailable"}, status=503)
            audio = await resp.read()
    except (ClientError, asyncio.TimeoutError):
        return web.json_response({"error": "voice unavailable"}, status=503)
    return web.Response(body=audio, content_type="audio/mpeg")


async def _diag(request: web.Request) -> web.Response:
    """The glasses app reports what its host offers (WebMCP present? tools registered?)."""
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "expected JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected JSON object"}, status=400)
    safe = {k: str(v)[:300] for k, v in list(body.items())[:12]}
    log.info("glasses diag: %s", safe)
    return web.json_response({"ok": True})


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "connected": request.app["bridge"].backend.connected()})


class PathOnlyAccessLogger(AbstractAccessLogger):
    """Access log without query strings, so nothing secret in a URL is ever written down."""

    def log(self, request: web.BaseRequest, response: web.StreamResponse, time: float) -> None:
        self.logger.info("%s %s %s %.0fms", request.method, request.path, response.status, time * 1000)


APP_PATH = "/beru/"  # the address the install link points at


def make_app(bridge: Bridge, static_dir: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[_guard], client_max_size=64 * 1024)
    app["bridge"] = bridge
    app.router.add_post("/api/say", _say)
    app.router.add_post("/api/ask", _ask)
    app.router.add_post("/api/speech", _speech)
    app.router.add_post("/api/diag", _diag)
    app.router.add_get("/api/feed", _feed)
    app.router.add_get("/api/health", _health)
    app.router.add_get("/api/audio/{message_id}", _audio)
    if static_dir and (static_dir / "index.html").exists():
        index = static_dir / "index.html"
        async def _index(_request: web.Request) -> web.FileResponse:
            return web.FileResponse(index)

        app.router.add_get("/", _index)
        # The glasses keep a launcher icon per app address, so a new icon needs a new address.
        # The app is also served from APP_PATH; its icon manifest sits beside it, per Meta's
        # rule that the manifest is looked up relative to the launch directory.
        manifest = static_dir / ".well-known" / "meta-wearables-manifest.json"

        async def _app_redirect(_request: web.Request) -> web.Response:
            raise web.HTTPPermanentRedirect(APP_PATH)

        async def _manifest(_request: web.Request) -> web.FileResponse:
            return web.FileResponse(manifest, headers={"Content-Type": "application/json; charset=utf-8"})

        app.router.add_get(APP_PATH.rstrip("/"), _app_redirect)
        app.router.add_get(APP_PATH, _index)
        if manifest.exists():
            app.router.add_get(APP_PATH + ".well-known/meta-wearables-manifest.json", _manifest)
        app.router.add_static("/", static_dir, show_index=False)

    async def on_startup(app_):
        app_["http"] = ClientSession()
        app_["speech_gate"] = asyncio.Semaphore(SPEECH_CONCURRENCY)
        await bridge.start()

    async def on_cleanup(app_):
        await bridge.stop()
        await app_["http"].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
