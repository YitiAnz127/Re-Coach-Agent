from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.schemas import ResolvedTask
from app.services.teaching import infer_teaching_start
from app.services.gate import run_gate


def task(concept: str = "反向传播") -> ResolvedTask:
    return ResolvedTask(domain="deep_learning", concept=concept, proposition=f"解释{concept}", desired_depth="L4")


def test_explicit_start_is_independent_of_requested_depth():
    novice = infer_teaching_start(task(), "我是新手，请详细推导反向传播", [])
    advanced = infer_teaching_start(task(), "我熟悉链式法则，直接推导反向传播", [])
    assert novice.level == "novice"
    assert advanced.level == "advanced"
    assert novice.source == advanced.source == "current_explicit"


def test_ambiguous_question_stays_unknown_and_state_is_proposition_scoped():
    states = [{"concept": "反向传播", "proposition": "另一道命题", "state": "self_reported_understood", "evidence_kind": "user_self_report"}]
    result = infer_teaching_start(task(), "请详细解释反向传播", states)
    assert result.level == "unknown"
    assert infer_teaching_start(task(), "请详细解释反向传播", []).level == "unknown"


def test_current_explicit_statement_overrides_previous_concept_feedback():
    previous = {"level": "advanced"}
    result = infer_teaching_start(task(), "我是新手，请推导反向传播", [], previous)
    assert result.level == "novice"
    assert result.source == "current_explicit"


def test_greeting_does_not_inherit_previous_concept_as_a_calibration_target():
    result = run_gate("谢谢", clarify_streak=0, known_context=["反向传播"])
    assert result.task.task_scope == "寒暄与开场"
    assert result.task.concept == ""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "teaching.db"))
    db.reset_for_tests(str(tmp_path / "teaching.db"))
    with TestClient(create_app()) as instance:
        yield instance


def ask(client: TestClient, session_id: str, content: str) -> dict:
    response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={"message": {"content": content}, "clientTurnId": f"client_{uuid.uuid4().hex}", "locale": "zh-CN"},
    )
    assert response.status_code == 200
    return json.loads([line[5:] for line in response.text.splitlines() if line.startswith("data:")][-1])


def test_calibration_updates_only_the_same_concept_and_can_be_corrected(client):
    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]
    first = ask(client, session_id, "请详细解释反向传播的链式法则")
    turn_id = first["turnId"]
    assert first["presentation"]["teachingStart"]["level"] == "unknown"
    url = f"/api/v1/sessions/{session_id}/turns/{turn_id}/calibration"
    saved = client.post(url, json={"rating": "too_fast"})
    assert saved.status_code == 200
    assert saved.json()["data"]["level"] == "novice"
    history = client.get(f"/api/v1/sessions/{session_id}/turns").json()["data"]["turns"]
    assert history[0]["calibration"] == "too_fast"
    same = ask(client, session_id, "请解释反向传播中梯度怎么传递")
    other = ask(client, session_id, "请解释梯度下降的更新步骤")
    assert same["presentation"]["teachingStart"]["level"] == "novice"
    assert same["presentation"]["teachingStart"]["source"] == "concept_feedback"
    assert other["presentation"]["teachingStart"]["level"] == "unknown"
    corrected = client.post(url, json={"rating": "too_basic"})
    assert corrected.status_code == 200
    assert corrected.json()["data"]["level"] == "familiar"
    assert client.post(url, json={"rating": "too_basic"}).json()["data"]["level"] == "familiar"


def test_calibration_requires_owned_completed_explanation(client):
    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]
    assert client.post(f"/api/v1/sessions/{session_id}/turns/turn_missing/calibration", json={"rating": "too_fast"}).status_code == 404
    clarified = ask(client, session_id, "讲讲反向传播")
    assert client.post(f"/api/v1/sessions/{session_id}/turns/{clarified['turnId']}/calibration", json={"rating": "too_fast"}).status_code == 422
    assert client.post(f"/api/v1/sessions/{session_id}/turns/{clarified['turnId']}/calibration", json={"rating": "invalid"}).status_code == 422
