import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient
from app import db
from app.auth import authorize
from app.config import get_settings
from app.config import Settings
from app.main import create_app, MaxBodySizeMiddleware
from app.services import ratelimit, turn_gate, turns

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "second.db"))
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 0)
    turn_gate.reset()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    turn_gate.reset()

@pytest.mark.parametrize("operation", ["create", "restart", "collision"])
def test_database_failure_releases_concurrency_slot(client, monkeypatch, operation):
    session = client.post("/api/v1/sessions", json={}).json()["data"]["sessionId"]
    payload = {"message": {"content": "explain gradients"}, "locale": "zh-CN"}
    if operation == "restart":
        tid = turns.create_turn(session, "dev_user", "client_test", payload)
        turns.finish_turn(tid, status="error", mode="explain")
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")
    if operation == "collision":
        def collide(*args, **kwargs):
            raise sqlite3.IntegrityError("collision")
        monkeypatch.setattr(turns, "create_turn", collide)
        calls = 0
        def lookup(*args):
            nonlocal calls
            calls += 1
            if calls > 1: fail()
            return None
        monkeypatch.setattr(turns, "find_by_client_key", lookup)
    else:
        monkeypatch.setattr(turns, "restart_turn" if operation == "restart" else "create_turn", fail)
    response = client.post(f"/api/v1/sessions/{session}/turns", json={**payload, "clientTurnId": "client_test"})
    assert response.status_code == 500
    assert turn_gate.inflight() == 0

def test_limiter_evicts_expired_keys_and_enforces_capacity(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 2)
    clock = [0.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock[0])
    try:
        assert ratelimit.check("a", limit=1)[0]
        assert ratelimit.check("b", limit=1)[0]
        assert not ratelimit.check("c", limit=1)[0]
        assert not ratelimit.check("a", limit=1)[0]
        clock[0] = 61.0
        assert ratelimit.check("c", limit=1)[0]
        assert len(ratelimit._hits) <= 2
    finally:
        ratelimit.reset()

def test_non_ascii_bearer_is_rejected_without_exception(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", "valid-ascii-token")
    assert authorize({"headers": [(b"authorization", b"Bearer \xff")], "client": ("127.0.0.1", 1)})[0] is False

def test_chunked_body_is_limited_before_app_reads_it():
    called = []
    async def app(scope, receive, send):
        called.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    async def run():
        chunks = iter([{"type": "http.request", "body": b"123", "more_body": True}, {"type": "http.request", "body": b"456", "more_body": False}])
        sent = []
        async def receive(): return next(chunks)
        async def send(message): sent.append(message)
        await MaxBodySizeMiddleware(app, 5)({"type": "http", "headers": []}, receive, send)
        assert sent[0]["status"] == 413
        assert not called
    asyncio.run(run())


def test_actual_body_size_wins_over_false_content_length():
    """中间件不能只信客户端声明的较小 Content-Length。"""
    called = []

    async def app(scope, receive, send):
        called.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def run():
        messages = iter(
            [
                {
                    "type": "http.request",
                    "body": b"123456",
                    "more_body": False,
                }
            ]
        )
        sent = []

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "headers": [(b"content-length", b"1")],
        }
        await MaxBodySizeMiddleware(app, 5)(scope, receive, send)
        assert sent[0]["status"] == 413
        assert not called

    asyncio.run(run())


@pytest.mark.parametrize(
    "name,value",
    [
        ("rate_limit_per_minute", -1),
        ("max_concurrent_turns", -1),
        ("max_body_bytes", -1),
        ("llm_max_tokens", 0),
        ("llm_timeout", 0),
        ("llm_max_continuations", 3),
        ("memory_max_selected", 0),
        ("memory_hard_limit", 0),
        ("memory_capsule_tokens", 0),
    ],
)
def test_unsafe_numeric_settings_fail_fast(name, value):
    with pytest.raises(ValueError):
        Settings(**{name: value})
