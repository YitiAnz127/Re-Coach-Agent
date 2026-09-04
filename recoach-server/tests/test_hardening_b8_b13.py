"""B8、B9 与 B13 并发/错误关联回归测试。"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.services import brief as brief_service
from app.services import coach as coach_service
from app.services import turns as turn_store


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "b8-b13.db"))
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _create_session(client: TestClient, user_id: str = "race_user") -> str:
    response = client.post(
        "/api/v1/sessions",
        json={"locale": "zh-CN"},
        headers={"x-user-id": user_id},
    )
    assert response.status_code == 200
    return response.json()["data"]["sessionId"]


def _turn_request(
    client: TestClient,
    session_id: str,
    *,
    content: str,
    client_turn_id: str,
    user_id: str = "race_user",
):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": client_turn_id,
            "locale": "zh-CN",
        },
        headers={"x-user-id": user_id},
    )


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for raw in text.strip().split("\n\n"):
        data = [line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def test_insert_race_rechecks_streaming_turn_instead_of_returning_500(
    client: TestClient, monkeypatch
):
    session_id = _create_session(client)
    client_turn_id = "client_insert_race"
    content = "我知道导数，请解释反向传播每层梯度怎样连接。"
    payload = {"message": {"content": content}, "locale": "zh-CN"}
    turn_store.create_turn(session_id, "race_user", client_turn_id, payload)
    original_find = turn_store.find_by_client_key
    calls = 0

    def find_after_collision(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original_find(*args, **kwargs)

    def collide(*_args, **_kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(turn_store, "find_by_client_key", find_after_collision)
    monkeypatch.setattr(turn_store, "create_turn", collide)

    response = _turn_request(
        client,
        session_id,
        content=content,
        client_turn_id=client_turn_id,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TURN_IN_PROGRESS"
    assert response.json()["error"]["requestId"] == response.headers["x-request-id"]


def test_insert_race_replays_completed_canonical_turn(
    client: TestClient, monkeypatch
):
    session_id = _create_session(client)
    client_turn_id = "client_insert_completed"
    content = "解释反向传播的链式法则。"
    payload = {"message": {"content": content}, "locale": "zh-CN"}
    turn_id = turn_store.create_turn(session_id, "race_user", client_turn_id, payload)
    turn_store.finish_turn(
        turn_id,
        status="completed",
        mode="explain",
        response_text="canonical answer",
        presentation={"mode": "explain", "focus": "反向传播", "plan": []},
    )
    original_find = turn_store.find_by_client_key
    calls = 0

    def find_after_collision(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original_find(*args, **kwargs)

    def collide(*_args, **_kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(turn_store, "find_by_client_key", find_after_collision)
    monkeypatch.setattr(turn_store, "create_turn", collide)

    response = _turn_request(
        client,
        session_id,
        content=content,
        client_turn_id=client_turn_id,
    )
    events = _parse_sse(response.text)

    assert response.status_code == 200
    assert events[-1]["type"] == "turn.completed"
    assert events[-1]["turnId"] == turn_id


def test_restart_turn_is_an_atomic_error_state_claim(client: TestClient):
    session_id = _create_session(client)
    client_turn_id = "client_retry_claim"
    payload = {"message": {"content": "解释梯度下降。"}, "locale": "zh-CN"}
    turn_id = turn_store.create_turn(
        session_id, "race_user", client_turn_id, payload
    )
    turn_store.finish_turn(turn_id, status="error", mode="explain")

    assert turn_store.restart_turn(turn_id) is True
    assert turn_store.restart_turn(turn_id) is False
    stored = turn_store.find_by_client_key(session_id, client_turn_id)
    assert stored is not None
    assert stored["status"] == "streaming"


def test_lost_retry_claim_rechecks_status_and_returns_409(
    client: TestClient, monkeypatch
):
    session_id = _create_session(client)
    client_turn_id = "client_retry_loser"
    content = "解释梯度消失。"
    payload = {"message": {"content": content}, "locale": "zh-CN"}
    turn_id = turn_store.create_turn(session_id, "race_user", client_turn_id, payload)
    turn_store.finish_turn(turn_id, status="error", mode="explain")
    stale_error = turn_store.find_by_client_key(session_id, client_turn_id)
    assert stale_error is not None
    assert turn_store.restart_turn(turn_id) is True
    original_find = turn_store.find_by_client_key
    calls = 0

    def stale_then_current(*args, **kwargs):
        nonlocal calls
        calls += 1
        return stale_error if calls == 1 else original_find(*args, **kwargs)

    monkeypatch.setattr(turn_store, "find_by_client_key", stale_then_current)
    monkeypatch.setattr(turn_store, "restart_turn", lambda _turn_id: False)

    response = _turn_request(
        client,
        session_id,
        content=content,
        client_turn_id=client_turn_id,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TURN_IN_PROGRESS"


def test_validation_error_uses_one_request_id(client: TestClient):
    session_id = _create_session(client)
    response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={"message": {"content": ""}, "clientTurnId": "x"},
        headers={"x-user-id": "race_user"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["requestId"] == response.headers["x-request-id"]


def test_sse_error_reuses_response_request_id(client: TestClient, monkeypatch):
    session_id = _create_session(client)

    async def broken_stream(**_kwargs):
        yield "content", "部分回答。"
        raise RuntimeError("simulated provider interruption")

    monkeypatch.setattr(coach_service, "stream_explanation", broken_stream)
    response = _turn_request(
        client,
        session_id,
        content="我知道导数，请解释反向传播每层梯度怎样连接。",
        client_turn_id="client_request_id",
    )
    events = _parse_sse(response.text)

    assert events[-1]["type"] == "turn.error"
    assert events[-1]["requestId"] == response.headers["x-request-id"]
