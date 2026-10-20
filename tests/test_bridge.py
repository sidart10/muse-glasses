import asyncio
import importlib

import pytest

TOKEN = "test-token-123"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_BRIDGE_API_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_BRIDGE_STATE_DIR", str(tmp_path))
    from muse_bridge import settings as module
    return importlib.reload(module)


@pytest.fixture
async def client(settings, aiohttp_client):
    from muse_bridge.backends import FakeBackend
    from muse_bridge.server import Bridge, make_app
    return await aiohttp_client(make_app(Bridge(FakeBackend(delay_s=0.05))))


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _until_done(client, request_id, after=0):
    events = []
    for _ in range(40):
        resp = await client.get(f"/api/feed?after={after}&wait=1", headers=AUTH)
        body = await resp.json()
        for event in body["events"]:
            after = event["seq"]
            if event["request_id"] == request_id:
                events.append(event)
        if events and events[-1]["type"] in ("done", "error"):
            return events
    raise AssertionError(f"no terminal event: {events}")


async def test_round_trip_with_fake_muse(client):
    resp = await client.post("/api/say", json={"text": "hello Muse"}, headers=AUTH)
    assert resp.status == 200
    request_id = (await resp.json())["request_id"]
    events = await _until_done(client, request_id)
    assert events[0] == {**events[0], "type": "sent", "text": "hello Muse"}
    finals = [e for e in events if e["type"] == "reply" and e["done"]]
    assert finals and "hello Muse" in finals[-1]["text"]
    assert events[-1]["type"] == "done"


async def test_api_requires_token(client):
    assert (await client.post("/api/say", json={"text": "hi"})).status == 401
    bad = {"Authorization": "Bearer nope"}
    assert (await client.get("/api/feed?wait=0", headers=bad)).status == 401


async def test_kill_switch_refuses_everything(client, settings):
    settings.set_disabled(True)
    assert (await client.post("/api/say", json={"text": "hi"}, headers=AUTH)).status == 503
    settings.set_disabled(False)
    assert (await client.post("/api/say", json={"text": "hi"}, headers=AUTH)).status == 200


async def test_rejects_empty_and_oversized_text(client):
    assert (await client.post("/api/say", json={"text": "  "}, headers=AUTH)).status == 400
    assert (await client.post("/api/say", json={"text": "x" * 5000}, headers=AUTH)).status == 413


async def test_glasses_hint_is_prepended(settings):
    from muse_bridge.server import GLASSES_HINT, Bridge

    seen = []

    class Recorder:
        async def start(self): pass
        async def stop(self): pass
        def connected(self): return True

        async def ask(self, text, request_id=""):
            seen.append(text)
            yield {"type": "done"}

    bridge = Bridge(Recorder())
    await bridge.start()
    bridge.submit("what's next on my calendar")
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)
    await bridge.stop()
    assert seen == [GLASSES_HINT + "what's next on my calendar"]


def test_muse_is_offered_no_commands():
    import musegadget_mac  # noqa: F401
    from muse_bridge.service import ALLOWED_COMMANDS, refuse_command

    assert ALLOWED_COMMANDS == {}
    for command in ("system.run", "file.read", "file.write"):
        assert command not in ALLOWED_COMMANDS
        assert refuse_command(command, {"cmd": "id"})["ok"] is False


def test_bridge_session_registers_only_allowed_commands(monkeypatch):
    import musegadget_mac  # noqa: F401
    from muse_bridge import service as bridge_service

    captured = {}

    class FakeLinkSession:
        registered_at = None

        def __init__(self, *, device, run_command, **_):
            captured["commands"] = device.commands
            captured["run_command"] = run_command

        async def run(self, stop):
            from musegadget.link_client import Outcome
            return Outcome.STOPPED

    monkeypatch.setattr(bridge_service, "LinkSession", FakeLinkSession)
    monkeypatch.setattr(bridge_service.BridgeService, "_read_chat", lambda self, s: asyncio.sleep(0))

    class Ident:
        node_id = "homelink-test"

    svc = bridge_service.BridgeService(identity=Ident(), sdk_token=None)
    asyncio.run(svc._session({"vm_id": "vm", "vm_name": "vm", "vm_auth_token": "t"}, {}))
    assert captured["commands"] == {}
    assert captured["run_command"] is bridge_service.refuse_command


def test_activity_lines_from_muse_status_events():
    from muse_bridge.backends import activity_from

    assert activity_from({"event": "agent.status", "payload": {"activity_code": "researching",
                          "activity_text": "Searching the web"}}) == "Searching the web"
    assert activity_from({"event": "agent.status", "payload": {"activity_code": "online",
                          "activity_text": "online"}}) is None
    assert activity_from({"event": "task.status", "payload": {"active_subagent_count": 2}}) == "2 helpers working"
    assert activity_from({"event": "delta.text_append", "payload": {}}) is None


def test_pending_approvals_are_trimmed_for_the_lens():
    from muse_bridge.backends import approvals_from

    snapshot = {"event": "approvals.snapshot", "payload": {
        "pending_approvals": [{"approval_id": "a1", "display": {"action_label": "Post content",
                               "detail_rows": [{"label": "Host", "value": "instagram"}, {"label": "x"}]}}],
        "recent_approvals": [{"approval_id": "old", "display": {"action_label": "ignored"}}]}}
    assert approvals_from(snapshot) == [{"id": "a1", "action": "Post content", "details": ["Host: instagram"]}]
    assert approvals_from({"event": "approvals.snapshot", "payload": {"pending_approvals": []}}) == []
    assert approvals_from({"event": "agent.status"}) is None


async def test_fake_backend_streams_activity_and_approval(client):
    resp = await client.post("/api/say", json={"text": "please approve the invite"}, headers=AUTH)
    request_id = (await resp.json())["request_id"]
    events = await _until_done(client, request_id)
    assert [e["text"] for e in events if e["type"] == "activity"][:1] == ["Checking your calendar"]
    feed = await (await client.get("/api/feed?after=0&wait=0", headers=AUTH)).json()
    approvals = [e for e in feed["events"] if e["type"] == "approvals"]
    assert approvals and approvals[0]["items"][0]["action"] == "Send email"


async def test_audio_no_longer_accepts_a_token_in_the_url(client):
    resp = await client.get(f"/api/audio/abc?k={TOKEN}")
    assert resp.status == 401


async def test_auth_is_checked_before_the_kill_switch(client, settings):
    settings.set_disabled(True)
    assert (await client.get("/api/feed?wait=0")).status == 401
    assert (await client.get("/api/feed?wait=0", headers=AUTH)).status == 503


def test_access_log_never_records_query_strings():
    import logging
    from unittest.mock import MagicMock
    from muse_bridge.server import PathOnlyAccessLogger

    logger = MagicMock(spec=logging.Logger)
    request = MagicMock(method="GET", path="/api/audio/x", path_qs="/api/audio/x?k=secret")
    PathOnlyAccessLogger(logger, "").log(request, MagicMock(status=200), 0.01)
    rendered = logger.info.call_args[0][0] % logger.info.call_args[0][1:]
    assert "secret" not in rendered and "/api/audio/x" in rendered


def test_rotation_in_another_process_is_picked_up(settings, monkeypatch, tmp_path):
    monkeypatch.delenv("MUSE_BRIDGE_API_TOKEN")
    store = {}
    monkeypatch.setattr(settings, "_keychain_get", lambda service: store.get(service))
    monkeypatch.setattr(settings, "_keychain_set", lambda service, value: store.__setitem__(service, value))
    first = settings.api_token()
    # "muse_bridge off" in another process: new token in the Keychain + revision bump
    import os, time
    store[settings.API_TOKEN_SERVICE] = "rotated-elsewhere"
    time.sleep(0.01)
    os.utime(settings.TOKEN_REVISION)
    assert first != "rotated-elsewhere"
    assert settings.api_token() == "rotated-elsewhere"


def test_keychain_failure_does_not_rotate_the_token(settings, monkeypatch):
    monkeypatch.delenv("MUSE_BRIDGE_API_TOKEN")
    rotated = []
    monkeypatch.setattr(settings, "rotate_api_token", lambda: rotated.append(1) or "new")

    def broken(service):
        raise settings.KeychainError("locked")

    monkeypatch.setattr(settings, "_keychain_get", broken)
    settings._cache.update(token=None, revision=None)
    with pytest.raises(settings.KeychainError):
        settings.api_token()
    assert rotated == []


async def test_busy_queue_returns_429(settings):
    from muse_bridge.server import SAY_QUEUE_MAX, Bridge

    class Slow:
        async def start(self): pass
        async def stop(self): pass
        def connected(self): return True
        async def ask(self, text, request_id=""):
            await asyncio.sleep(10)
            yield {"type": "done"}

    bridge = Bridge(Slow())
    await bridge.start()
    bridge.submit("in progress")
    await asyncio.sleep(0.05)  # the worker takes it; the queue is empty again
    for _ in range(SAY_QUEUE_MAX):
        bridge.submit("x")
    with pytest.raises(asyncio.QueueFull):
        bridge.submit("one too many")
    await bridge.stop()


async def test_ask_returns_a_quick_answer_for_meta_ai_to_speak(client):
    resp = await client.post("/api/ask", json={"text": "hello", "request_id": "abcdef012345", "wait_ms": 5000},
                             headers=AUTH)
    body = await resp.json()
    assert resp.status == 200
    assert body["request_id"] == "abcdef012345" and body["status"] == "answered"
    assert "hello" in body["answer"]


async def test_ask_reports_pending_when_the_answer_is_slow(client):
    resp = await client.post("/api/ask", json={"text": "hello", "wait_ms": 50}, headers=AUTH)
    body = await resp.json()
    assert body["status"] == "pending" and body["answer"] == ""


async def test_ask_validates_input(client):
    assert (await client.post("/api/ask", json={"text": "hi", "request_id": "NOT-HEX!"}, headers=AUTH)).status == 400
    assert (await client.post("/api/ask", json={"text": " "}, headers=AUTH)).status == 400
    assert (await client.post("/api/ask", json={"text": "hi", "wait_ms": "soon"}, headers=AUTH)).status == 400
    assert (await client.post("/api/ask", json={"text": "hi"})).status == 401


async def test_hashed_assets_are_cached_and_api_is_not(settings, aiohttp_client, tmp_path):
    from muse_bridge.backends import FakeBackend
    from muse_bridge.server import Bridge, make_app

    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)")
    c = await aiohttp_client(make_app(Bridge(FakeBackend(delay_s=0.01)), tmp_path))
    asset = await c.get("/assets/app-abc123.js")
    assert "immutable" in asset.headers["Cache-Control"]
    assert (await c.get("/")).headers["Cache-Control"].startswith("no-store")
    assert (await c.get("/api/health", headers=AUTH)).headers["Cache-Control"].startswith("no-store")


async def test_ask_rejects_nan_wait_and_reused_ids(client):
    bad = await client.post("/api/ask", data='{"text": "hi", "wait_ms": NaN}',
                            headers={**AUTH, "Content-Type": "application/json"})
    assert bad.status == 400
    first = await client.post("/api/ask", json={"text": "hi", "request_id": "aaaaaaaaaaaa"}, headers=AUTH)
    assert first.status == 200
    again = await client.post("/api/ask", json={"text": "hi", "request_id": "aaaaaaaaaaaa"}, headers=AUTH)
    assert again.status == 409


async def test_speech_falls_back_cleanly_when_the_voice_is_down(client, monkeypatch):
    from muse_bridge import server
    monkeypatch.setattr(server, "VOICE_URL", "http://127.0.0.1:9/tts")  # nothing listens here
    resp = await client.post("/api/speech", json={"text": "Hello there."}, headers=AUTH)
    assert resp.status == 503
    assert (await client.post("/api/speech", json={"text": ""}, headers=AUTH)).status == 400
    assert (await client.post("/api/speech", json={"text": "hi"})).status == 401


async def test_launcher_manifest_falls_back_to_the_bundled_glyph(aiohttp_client, tmp_path):
    import json

    from muse_bridge.backends import FakeBackend
    from muse_bridge.server import DEFAULT_LAUNCHER_ICON, Bridge, make_app
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / ".well-known").mkdir()
    (tmp_path / ".well-known" / "meta-wearables-manifest.json").write_text(json.dumps(
        {"version": 1, "name": "Beru", "appearance": {"theme_color": "#fff", "icon": {"src": "/icons/mascot-launcher.png"}}}))
    client = await aiohttp_client(make_app(Bridge(FakeBackend()), tmp_path))
    for path in ("/.well-known/meta-wearables-manifest.json", "/beru/.well-known/meta-wearables-manifest.json"):
        data = await (await client.get(path)).json()
        assert data["appearance"]["icon"]["src"] == DEFAULT_LAUNCHER_ICON
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "mascot-launcher.png").write_bytes(b"png")
    data = await (await client.get("/beru/.well-known/meta-wearables-manifest.json")).json()
    assert data["appearance"]["icon"]["src"] == "/icons/mascot-launcher.png"


async def test_feed_reports_the_web_app_build(aiohttp_client, tmp_path, monkeypatch):
    from muse_bridge.backends import FakeBackend
    from muse_bridge.server import Bridge, make_app
    monkeypatch.setenv("MUSE_BRIDGE_API_TOKEN", "t" * 32)
    (tmp_path / "index.html").write_text('<script type="module" crossorigin src="/assets/index-AbC123_x.js"></script>')
    client = await aiohttp_client(make_app(Bridge(FakeBackend()), tmp_path))
    data = await (await client.get("/api/feed?after=0&wait=0", headers={"Authorization": "Bearer " + "t" * 32})).json()
    assert data["build"] == "index-AbC123_x.js"
