from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.services import brief as brief_service
from app.services import coach as coach_service
from app.services import orchestrator as orchestrator_service
from app.services import memory as memory_service
from app.services import turns as turn_store


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "p0-contract.db"))
    with TestClient(create_app()) as test_client:
        yield test_client


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for raw in text.strip().split("\n\n"):
        data_lines = [line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:")]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def create_session(client: TestClient, user_id: str = "user_a") -> str:
    response = client.post(
        "/api/v1/sessions",
        json={"locale": "zh-CN"},
        headers={"x-user-id": user_id},
    )
    assert response.status_code == 200
    return response.json()["data"]["sessionId"]


def post_turn(
    client: TestClient,
    session_id: str,
    content: str,
    *,
    user_id: str = "user_a",
    client_turn_id: str | None = None,
):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": client_turn_id or f"client_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers={"x-user-id": user_id},
    )


def test_session_owner_is_enforced(client: TestClient):
    session_id = create_session(client, "owner")

    response = post_turn(
        client,
        session_id,
        "我知道导数，请解释反向传播每层梯度怎样连接。",
        user_id="other_user",
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_completion_is_not_emitted_before_canonical_persistence(client: TestClient, monkeypatch):
    session_id = create_session(client)
    original_finish = turn_store.finish_turn
    calls = 0

    def fail_first_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated persistence failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(turn_store, "finish_turn", fail_first_finish)

    response = post_turn(
        client,
        session_id,
        "我知道导数，请解释反向传播每层梯度怎样连接。",
    )
    events = parse_sse(response.text)
    terminals = [event["type"] for event in events if event["type"] in {"turn.completed", "turn.error"}]

    assert terminals == ["turn.error"]


def test_retry_after_stream_failure_does_not_duplicate_side_effects(client: TestClient, monkeypatch):
    session_id = create_session(client)
    client_turn_id = f"client_{uuid.uuid4().hex[:12]}"
    original_stream = coach_service.stream_explanation

    async def broken_stream(**_kwargs):
        yield "部分回答。"
        raise RuntimeError("simulated stream interruption")

    monkeypatch.setattr(coach_service, "stream_explanation", broken_stream)
    first = post_turn(
        client,
        session_id,
        "我知道导数，请解释反向传播每层梯度怎样连接。",
        client_turn_id=client_turn_id,
    )
    first_events = parse_sse(first.text)
    assert first_events[-1]["type"] == "turn.error"
    turn_id = first_events[-1]["turnId"]

    monkeypatch.setattr(coach_service, "stream_explanation", original_stream)
    second = post_turn(
        client,
        session_id,
        "我知道导数，请解释反向传播每层梯度怎样连接。",
        client_turn_id=client_turn_id,
    )
    second_events = parse_sse(second.text)
    assert second_events[-1]["type"] == "turn.completed"
    assert second_events[-1]["turnId"] == turn_id

    message_rows = db.query(
        "SELECT role, COUNT(*) AS count FROM messages WHERE turn_id=? GROUP BY role",
        (turn_id,),
    )
    assert {row["role"]: row["count"] for row in message_rows} == {"assistant": 1, "user": 1}

    duplicate_events = db.query(
        "SELECT kind, COUNT(*) AS count FROM events WHERE turn_id=? GROUP BY kind HAVING COUNT(*) > 1",
        (turn_id,),
    )
    assert duplicate_events == []


def test_schema_migration_and_health_are_available(client: TestClient):
    migrations = db.query("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in migrations]

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "recoach-server"}


def test_clarification_updates_structured_open_question(client: TestClient):
    session_id = create_session(client)

    response = post_turn(client, session_id, "讲讲反向传播")
    events = parse_sse(response.text)
    assert events[-1]["presentation"]["mode"] == "clarify"

    row = db.query_one("SELECT brief_json FROM sessions WHERE id=?", (session_id,))
    assert row is not None
    brief = json.loads(row["brief_json"])
    assert brief["open_questions"]
    assert brief["current_focus"] == "定位真实卡点"



def test_clarification_followup_reuses_session_concept(client: TestClient):
    session_id = create_session(client)
    first = post_turn(client, session_id, "讲讲反向传播")
    assert parse_sse(first.text)[-1]["presentation"]["mode"] == "clarify"

    second = post_turn(client, session_id, "梯度具体怎样传递？")
    started = parse_sse(second.text)[0]

    assert started["mode"] == "explain"
    assert "反向传播" in started["focus"]


def test_memory_off_disables_global_long_term_rules(client: TestClient):
    session_id = create_session(client)
    memory_service.write_memory(
        "user_a",
        type_="interaction_rule",
        rule="每次都用三句话回答",
        source_event_id="evt_external_rule",
    )
    settings = get_settings()
    previous = settings.memory_on
    settings.memory_on = False
    try:
        response = post_turn(
            client,
            session_id,
            "我知道导数，请解释反向传播每层梯度怎样连接。",
        )
    finally:
        settings.memory_on = previous

    presentation = parse_sse(response.text)[-1]["presentation"]
    assert presentation["personalization"] == []
    assert presentation["metrics"]["memoryCapsuleTokens"] == 0



def test_forget_retry_keeps_the_original_outcome(client: TestClient, monkeypatch):
    session_id = create_session(client)
    post_turn(client, session_id, "以后每次都先给公式，记住。")
    client_turn_id = f"client_{uuid.uuid4().hex[:12]}"
    original_stream = coach_service.stream_explanation

    async def broken_stream(**_kwargs):
        yield "部分回答。"
        raise RuntimeError("simulated stream interruption")

    monkeypatch.setattr(coach_service, "stream_explanation", broken_stream)
    first = post_turn(
        client,
        session_id,
        "忘记之前关于先给公式的偏好。",
        client_turn_id=client_turn_id,
    )
    assert parse_sse(first.text)[-1]["type"] == "turn.error"

    monkeypatch.setattr(coach_service, "stream_explanation", original_stream)
    second = post_turn(
        client,
        session_id,
        "忘记之前关于先给公式的偏好。",
        client_turn_id=client_turn_id,
    )
    body = "".join(
        event["delta"] for event in parse_sse(second.text) if event["type"] == "assistant.delta"
    )

    assert "已遗忘 1 条相关偏好" in body


def test_session_only_rule_is_not_duplicated_when_finalization_retries(client: TestClient, monkeypatch):
    session_id = create_session(client)
    client_turn_id = f"client_{uuid.uuid4().hex[:12]}"
    original_finish = turn_store.finish_turn
    calls = 0

    def fail_first_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated finalization failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(turn_store, "finish_turn", fail_first_finish)
    first = post_turn(
        client,
        session_id,
        "这次先不要公式。",
        client_turn_id=client_turn_id,
    )
    assert parse_sse(first.text)[-1]["type"] == "turn.error"

    second = post_turn(
        client,
        session_id,
        "这次先不要公式。",
        client_turn_id=client_turn_id,
    )
    assert parse_sse(second.text)[-1]["type"] == "turn.completed"

    row = db.query_one("SELECT brief_json FROM sessions WHERE id=?", (session_id,))
    brief = json.loads(row["brief_json"])
    assert brief["session_rules"] == ["这次先不要公式。"]


def test_memory_off_does_not_read_or_write_concept_state(client, monkeypatch):
    source_session_id = create_session(client)
    fork_response = client.post(
        f"/api/v1/sessions/{source_session_id}/forks",
        json={},
        headers={"x-user-id": "user_a"},
    )
    off_session_id = next(
        fork["sessionId"]
        for fork in fork_response.json()["data"]["forks"]
        if fork["memoryMode"] == "off"
    )

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("memoryMode=off must not read Concept State")

    monkeypatch.setattr(brief_service, "concept_states_for", fail_if_read)
    response = client.post(
        f"/api/v1/sessions/{off_session_id}/turns",
        json={
            "message": {
                "content": "我知道导数，但不理解反向传播每层梯度怎样连接。"
            },
            "clientTurnId": f"client_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers={"x-user-id": "user_a"},
    )

    assert response.status_code == 200
    assert parse_sse(response.text)[-1]["type"] == "turn.completed"
    assert db.query(
        "SELECT id FROM concept_states WHERE user_id=?", ("user_a",)
    ) == []


def test_cancelled_turn_is_marked_failed_for_retry(client, monkeypatch):
    session_id = create_session(client)
    client_turn_id = f"client_{uuid.uuid4().hex[:12]}"
    turn_id = turn_store.create_turn(
        session_id,
        "user_a",
        client_turn_id,
        {
            "message": {
                "content": "我知道导数，但不理解反向传播每层梯度怎样连接。"
            },
            "locale": "zh-CN",
        },
    )

    async def cancelled_stream(**_kwargs):
        raise asyncio.CancelledError
        yield  # pragma: no cover

    monkeypatch.setattr(coach_service, "stream_explanation", cancelled_stream)

    async def consume():
        async for _event in orchestrator_service.run_turn(
            user_id="user_a",
            session_id=session_id,
            turn_id=turn_id,
            user_text="我知道导数，但不理解反向传播每层梯度怎样连接。",
        ):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume())

    stored = turn_store.find_by_client_key(session_id, client_turn_id)
    assert stored is not None
    assert stored["status"] == "error"
