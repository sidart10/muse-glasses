"""LiveBackend.ask against a scripted Muse: the cases that broke or could break live."""

import asyncio

import pytest

import musegadget_mac  # noqa: F401  (SDK import path + macOS state dir)
from muse_bridge import backends


def ev(name, message_id=None, *, parent=None, session="main", **payload):
    data = {"session_id": session, **payload}
    if message_id is not None:
        data["message_id"] = message_id
    if parent is not None:
        data["reply_to_message_id"] = parent
    return {"type": "event", "event": name, "payload": data}


class FakeSession:
    def __init__(self):
        self.chat_subscribed = asyncio.Event()
        self.chat_subscribed.set()
        self.sent = []

    async def send_chat(self, text, session_id=None, *, output_modality="text"):
        self.sent.append(text)
        return {"ok": True, "status": 200, "response": {"result": {"message_id": "u1", "session_id": "main"}}}


class FakeService:
    def __init__(self, session):
        self.current = session
        self.queues = set()

    def session(self):
        return self.current

    def ready(self):
        return self.current is not None and self.current.chat_subscribed.is_set()

    def listen(self):
        q = asyncio.Queue()
        self.queues.add(q)
        return q

    def unlisten(self, q):
        self.queues.discard(q)

    def push(self, event):
        for q in list(self.queues):
            q.put_nowait(event)


def make_backend(monkeypatch, **timing):
    monkeypatch.setattr(backends, "SUBSCRIBE_WAIT_S", 0.5)
    monkeypatch.setattr(backends, "STALL_S", timing.get("stall", 5.0))
    monkeypatch.setattr(backends, "NO_ANSWER_QUIET_S", timing.get("no_answer", 5.0))
    monkeypatch.setattr(backends, "TURN_TIMEOUT_S", timing.get("timeout", 10.0))
    monkeypatch.setattr(backends, "_POLL_S", 0.02)
    session = FakeSession()
    live = object.__new__(backends.LiveBackend)
    live._service = FakeService(session)
    live._lock = asyncio.Lock()
    live._followers = set()
    live._sink = None
    live._watcher = live._task = None
    live._last_approvals = None
    return live, session


async def run_turn(live, script, *, sink_events=None):
    """Run ask() while feeding ``script`` (list of events or ('sleep', s)) to the service."""
    out = []

    async def feeder():
        await asyncio.sleep(0.05)
        for item in script:
            if isinstance(item, tuple):
                await asyncio.sleep(item[1])
            elif callable(item):
                item()
            else:
                live._service.push(item)
                await asyncio.sleep(0.01)

    task = asyncio.ensure_future(feeder())
    async for event in live.ask("hello", "req1"):
        out.append(event)
    await task
    return out


def texts(events, kind):
    return [e.get("text") for e in events if e["type"] == kind]


async def test_normal_answer_streams_and_finishes(monkeypatch):
    live, _ = make_backend(monkeypatch)
    out = await run_turn(live, [
        ev("agent.status", activity_code="researching", activity_text="Checking your calendar"),
        ev("delta.message_start", "r1", parent="u1"),
        ev("delta.text_append", "r1", parent="u1", text="You have two meetings."),
        ev("delta.message_done", "r1", parent="u1", display_text="You have two meetings."),
        ev("task.status", status="completed", task_id="t1"),
    ])
    assert "Checking your calendar" in texts(out, "activity")
    finals = [e for e in out if e["type"] == "reply" and e["done"]]
    assert finals[-1]["text"] == "You have two meetings." and finals[-1]["message_id"] == "r1"
    assert out[-1]["type"] == "done"


async def test_phone_message_reply_is_not_shown_as_the_glasses_answer(monkeypatch):
    live, _ = make_backend(monkeypatch, stall=0.4)
    out = await run_turn(live, [
        ev("message.user", "u-phone", role="user"),               # the user typed on the phone
        ev("delta.message_start", "r-phone"),                      # parentless reply to that
        ev("delta.message_done", "r-phone", display_text="Answer to the phone message"),
        ev("delta.message_start", "r1", parent="u1"),
        ev("delta.message_done", "r1", parent="u1", display_text="Answer for the glasses"),
        ev("task.status", status="completed", task_id="t1"),
    ])
    shown = texts(out, "reply")
    assert "Answer for the glasses" in shown
    assert "Answer to the phone message" not in shown


async def test_lost_message_done_does_not_hang(monkeypatch):
    live, _ = make_backend(monkeypatch, stall=0.3, timeout=5.0)
    out = await asyncio.wait_for(run_turn(live, [
        ev("delta.message_start", "r1", parent="u1"),
        ev("delta.text_append", "r1", parent="u1", text="Partial answer"),
    ]), 3.0)
    assert texts(out, "reply")[-1] == "Partial answer"
    assert out[-1]["type"] == "done"


async def test_disconnect_mid_turn_reports_error_quickly(monkeypatch):
    live, session = make_backend(monkeypatch, timeout=5.0)

    def drop():
        live._service.current = None

    out = await asyncio.wait_for(run_turn(live, [ev("agent.status", activity_text="Working"), drop]), 3.0)
    assert out[-1]["type"] == "error" and "connection" in out[-1]["text"].lower()


async def test_not_subscribed_refuses_instead_of_losing_the_reply(monkeypatch):
    live, session = make_backend(monkeypatch)
    session.chat_subscribed.clear()
    out = await asyncio.wait_for(run_turn(live, []), 3.0)
    assert out == [{"type": "error", "text": "Beru can't reach Muse right now. Try again in a moment."}]
    assert session.sent == []


async def test_finished_task_without_reply_gives_up_cleanly(monkeypatch):
    live, _ = make_backend(monkeypatch, no_answer=0.3, timeout=5.0)
    out = await asyncio.wait_for(run_turn(live, [ev("task.status", status="completed", task_id="t1")]), 3.0)
    assert out[-1]["type"] == "error" and "without a reply" in out[-1]["text"]


async def test_late_reply_is_delivered_after_the_turn(monkeypatch):
    live, _ = make_backend(monkeypatch, stall=0.2)
    monkeypatch.setattr(backends, "FOLLOW_UP_S", 2.0)
    late = []

    async def sink(event, request_id):
        late.append((request_id, event))

    live._sink = sink
    out = await run_turn(live, [
        ev("delta.message_start", "r1", parent="u1"),
        ev("delta.message_done", "r1", parent="u1", display_text="On it."),
    ])
    assert out[-1]["type"] == "done"
    live._service.push(ev("delta.message_start", "r2", parent="u1"))
    live._service.push(ev("delta.message_done", "r2", parent="u1", display_text="Booked for 7 PM."))
    live._service.push(ev("delta.message_done", "rx", parent="someone-else", display_text="Not ours"))
    for _ in range(50):
        if late:
            break
        await asyncio.sleep(0.05)
    assert [(rid, e["text"], e.get("late")) for rid, e in late] == [("req1", "Booked for 7 PM.", True)]
    for task in list(live._followers):
        task.cancel()
