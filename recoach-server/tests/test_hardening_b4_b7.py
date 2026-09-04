"""B4-B7 代码加固与 Bug 修复回归测试。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.schemas import ResolvedTask, SessionBrief
from app.services import brief as brief_service
from app.services import turns as turn_store


def _assert_error_envelope(response, *, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool)
    assert error["requestId"].startswith("req_")
    assert response.headers["x-request-id"] == error["requestId"]
    return error


def test_unknown_route_uses_error_envelope(tmp_path):
    db.reset_for_tests(str(tmp_path / "b4-404.db"))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/route-that-does-not-exist")

    error = _assert_error_envelope(response, status=404, code="INVALID_REQUEST")
    assert error["retryable"] is False


def test_method_not_allowed_uses_error_envelope(tmp_path):
    db.reset_for_tests(str(tmp_path / "b4-405.db"))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/sessions")

    _assert_error_envelope(response, status=405, code="INVALID_REQUEST")
    assert response.headers["allow"] == "POST"


def test_unhandled_exception_uses_internal_error_envelope(tmp_path):
    db.reset_for_tests(str(tmp_path / "b4-500.db"))
    app = create_app()

    @app.get("/_test/boom")
    def boom():
        raise RuntimeError("must not leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/_test/boom", headers={"origin": "http://localhost:4173"}
        )

    error = _assert_error_envelope(response, status=500, code="INTERNAL")
    assert error["retryable"] is True
    assert "must not leak" not in response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_explain_turn_preserves_existing_open_questions():
    brief = SessionBrief(open_questions=["链式法则在每层怎样连接？"])
    task = ResolvedTask(
        goal="理解反向传播",
        concept="反向传播",
        proposition="反向传播逐层传递梯度",
    )

    updated = brief_service.apply_turn_delta(
        brief,
        user_text="请继续讲一个例子。",
        focus="反向传播示例",
        mode="explain",
        task=task,
    )

    assert updated.open_questions == brief.open_questions


def test_stale_brief_update_cannot_overwrite_newer_version(tmp_path):
    db.reset_for_tests(str(tmp_path / "b6-cas.db"))
    session_id = brief_service.create_session("b6_user", "zh-CN")
    session = brief_service.get_session(session_id)
    assert session is not None
    original_version = session[2]

    fresh = SessionBrief(goal="较新的目标")
    assert brief_service.update_brief(
        session_id, fresh, expected_version=original_version
    ) == original_version + 1

    stale = SessionBrief(goal="过期写入不应覆盖")
    brief_service.update_brief(
        session_id, stale, expected_version=original_version
    )

    stored = brief_service.get_session(session_id)
    assert stored is not None
    assert stored[1].goal == "较新的目标"
    assert stored[2] == original_version + 1


def test_startup_recovers_streaming_turn_for_same_key_retry(tmp_path):
    db.reset_for_tests(str(tmp_path / "b7-recovery.db"))
    user_id = "b7_user"
    session_id = brief_service.create_session(user_id, "zh-CN")
    client_turn_id = "client_b7_recovery"
    content = "我知道导数，请解释反向传播每层梯度怎样连接。"
    payload = {"message": {"content": content}, "locale": "zh-CN"}
    turn_id = turn_store.create_turn(
        session_id, user_id, client_turn_id, payload
    )
    db.reset_for_tests(str(tmp_path / "b7-recovery.db"))
    before_startup = turn_store.find_by_client_key(session_id, client_turn_id)
    assert before_startup is not None
    assert before_startup["status"] == "streaming"

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        recovered = turn_store.find_by_client_key(session_id, client_turn_id)
        assert recovered is not None
        assert recovered["status"] == "error"
        assert json.loads(recovered["error_json"]) == {
            "code": "CANCELLED",
            "type": "ProcessRestart",
        }

        response = client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": content},
                "clientTurnId": client_turn_id,
                "locale": "zh-CN",
            },
            headers={"x-user-id": user_id},
        )

    assert response.status_code == 200
    assert f'"turnId": "{turn_id}"' in response.text
    stored = turn_store.find_by_client_key(session_id, client_turn_id)
    assert stored is not None
    assert stored["id"] == turn_id
    assert stored["status"] == "completed"
