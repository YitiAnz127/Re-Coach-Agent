from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("RECOACH_LLM_provider", "template")
os.environ.setdefault("RECOACH_DEV_USER", "test_user")

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.services import memory as memory_service


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "test.db"))
    db.reset_for_tests(str(tmp_path / "test.db"))
    with TestClient(create_app()) as c:
        yield c


def parse_sse(text: str) -> list[dict]:
    events = []
    for raw in text.strip().split("\n\n"):
        data_lines = [l[5:].lstrip() for l in raw.splitlines() if l.startswith("data:")]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def post_turn(
    client: TestClient,
    session_id: str,
    content: str,
    client_turn_id: str | None = None,
):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": client_turn_id or f"client_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
    )


def create_session(client: TestClient) -> str:
    resp = client.post("/api/v1/sessions", json={"locale": "zh-CN"})
    assert resp.status_code == 200
    return resp.json()["data"]["sessionId"]


def test_create_session(client):
    session_id = create_session(client)
    assert session_id.startswith("ses_")


def test_broad_question_triggers_single_clarification(client):
    session_id = create_session(client)
    resp = post_turn(client, session_id, "讲讲反向传播")
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "turn.started"
    assert types[-1] == "turn.completed"
    assert types.count("turn.completed") + types.count("turn.error") == 1  # 唯一终止事件
    assert events[0]["mode"] == "clarify"
    presentation = events[-1]["presentation"]
    assert presentation["mode"] == "clarify"
    assert presentation["clarificationOptions"]  # 每轮一个问题 + 选项
    assert presentation["personalization"] == []  # 澄清轮不伪造个性化


def test_specific_question_skips_clarification(client):
    session_id = create_session(client)
    resp = post_turn(client, session_id, "我知道导数和梯度下降，但不理解反向传播每层的梯度怎样通过链式法则连起来。")
    events = parse_sse(resp.text)
    assert events[0]["mode"] == "explain"
    deltas = [e for e in events if e["type"] == "assistant.delta"]
    assert len(deltas) > 0
    body = "".join(e["delta"] for e in deltas)
    assert "反向传播" in body
    presentation = events[-1]["presentation"]
    assert presentation["mode"] == "explain"
    assert set(presentation["metrics"]) == {
        "timeToFirstTokenMs", "memorySearchMs", "contextCompileMs",
        "memoryCapsuleTokens", "totalInputTokens",
    }


def test_two_turn_memory_takes_effect(client):
    """P0 验收核心：第一轮显式写入偏好，第二轮同主题问题命中该偏好。"""
    session_id = create_session(client)
    resp1 = post_turn(client, session_id, "以后讲解时请先用数值例子再讲公式，记住这个偏好。")
    assert "已记住" in resp1.text

    resp2 = post_turn(client, session_id, "我知道导数，但不理解反向传播每层梯度怎样连起来，请讲解一下。")
    events2 = parse_sse(resp2.text)
    presentation = events2[-1]["presentation"]
    personalization = presentation["personalization"]
    assert len(personalization) >= 1, "第二轮应检索到第一轮写入的长期偏好"
    assert personalization[0]["memoryId"]
    assert presentation["metrics"]["memoryCapsuleTokens"] > 0

    # 只读记忆透明度接口
    resp3 = client.get("/api/v1/memories?status=active")
    assert resp3.status_code == 200
    rules = [m["rule"] for m in resp3.json()["data"]]
    assert any("数值例子" in r for r in rules)


def test_forget_via_natural_language(client):
    session_id = create_session(client)
    post_turn(client, session_id, "以后每次都先给公式，记住。")
    resp = post_turn(client, session_id, "忘记之前关于先给公式的偏好。")
    assert "已遗忘" in resp.text or "没有找到" in resp.text
    active = client.get("/api/v1/memories?status=active").json()["data"]
    assert all("先给公式" not in m["rule"] for m in active)


def test_idempotent_retry_returns_same_turn(client):
    session_id = create_session(client)
    turn_key = f"client_{uuid.uuid4().hex[:12]}"
    resp1 = post_turn(client, session_id, "我知道导数，请解释反向传播怎么把梯度传回前面的层。", turn_key)
    resp2 = post_turn(client, session_id, "我知道导数，请解释反向传播怎么把梯度传回前面的层。", turn_key)
    events1 = parse_sse(resp1.text)
    events2 = parse_sse(resp2.text)
    assert events1[-1]["turnId"] == events2[-1]["turnId"], "相同键+相同 payload 应返回同一 Turn"
    assert events1[-1]["presentation"] == events2[-1]["presentation"]


def test_conflicting_retry_returns_409(client):
    session_id = create_session(client)
    turn_key = f"client_{uuid.uuid4().hex[:12]}"
    post_turn(client, session_id, "第一个问题关于反向传播。", turn_key)
    resp = post_turn(client, session_id, "完全不同的内容。", turn_key)
    assert resp.status_code == 409
    assert resp.json()["error"]["retryable"] is False


def test_session_not_found(client):
    resp = post_turn(client, "ses_missing", "你好")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_delta_concat_equals_canonical(client):
    """assistant.delta 顺序拼接必须等于服务端保存的 canonical 消息。"""
    session_id = create_session(client)
    turn_key = f"client_{uuid.uuid4().hex[:12]}"
    resp = post_turn(client, session_id, "我知道导数，请解释梯度下降为什么沿负梯度方向走。", turn_key)
    events = parse_sse(resp.text)
    streamed = "".join(e["delta"] for e in events if e["type"] == "assistant.delta")
    row = db.query_one("SELECT response_text FROM turns WHERE client_turn_id=?", (turn_key,))
    assert row is not None
    assert streamed == row["response_text"]


def test_meta(client):
    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["capabilities"]["sqlite"] is True
    assert data["capabilities"]["fairAbFork"] is True
    assert "memoryModes" not in data["capabilities"]
    assert "policyVersion" in data


def test_normal_turn_uses_the_server_memory_default(client):
    memory_service.write_memory(
        "test_user",
        type_="explanation_preference",
        rule="讲解时先用数值例子再讲公式",
        source_event_id="evt_default_seed",
    )
    question = "我知道导数，请解释反向传播每层梯度怎样通过链式法则连接。"
    settings = get_settings()
    previous = settings.memory_on
    settings.memory_on = False
    try:
        response = post_turn(client, create_session(client), question)
    finally:
        settings.memory_on = previous

    presentation = parse_sse(response.text)[-1]["presentation"]
    assert presentation["personalization"] == []
    assert presentation["metrics"]["memoryCapsuleTokens"] == 0


def test_legacy_turn_with_memory_mode_still_replays(client):
    session_id = create_session(client)
    turn_key = f"client_{uuid.uuid4().hex[:12]}"
    question = "我知道导数，请解释反向传播每层梯度怎样连接。"
    first = post_turn(client, session_id, question, turn_key)
    first_events = parse_sse(first.text)

    legacy_payload = {
        "message": {"content": question},
        "locale": "zh-CN",
        "memoryMode": "on",
    }
    with db.tx() as connection:
        connection.execute(
            "UPDATE turns SET request_json=? WHERE client_turn_id=?",
            (json.dumps(legacy_payload, ensure_ascii=False), turn_key),
        )

    replay = post_turn(client, session_id, question, turn_key)
    replay_events = parse_sse(replay.text)
    assert replay.status_code == 200
    assert replay_events[-1]["turnId"] == first_events[-1]["turnId"]


@pytest.mark.parametrize("mode", ["auto", "on", "off", "sometimes"])
def test_request_level_memory_mode_is_rejected(client, mode):
    session_id = create_session(client)
    response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "解释一下反向传播。"},
            "clientTurnId": "client_invalid_mode",
            "locale": "zh-CN",
            "memoryMode": mode,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_v11_retrospective_only_when_knowledge_unit_closes(client):
    session_id = create_session(client)

    # 普通提问（无收尾信号）：结构化复盘不出现
    response = post_turn(client, session_id, "请从公式推导开始解释反向传播。")
    events = parse_sse(response.text)
    presentation = events[-1]["presentation"]
    assert presentation["depth"] == "L4"
    assert "retrospective" not in presentation

    # 用户自报理解：知识单元收尾，出现复盘
    closure = post_turn(client, session_id, "我懂了，明白了，谢谢。")
    closure_events = parse_sse(closure.text)
    closure_presentation = closure_events[-1]["presentation"]
    assert closure_presentation["retrospective"]["connections"]
    assert closure_presentation["retrospective"]["approach"]
