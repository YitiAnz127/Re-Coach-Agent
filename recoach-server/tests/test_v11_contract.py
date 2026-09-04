from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.services import memory as memory_service


def parse_sse(text: str) -> list[dict]:
    events = []
    for raw in text.strip().split("\n\n"):
        data_lines = [line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:")]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def create_client(tmp_path):
    db.reset_for_tests(str(tmp_path / "v11.db"))
    return TestClient(create_app())


def create_session(client: TestClient, user_id: str = "v11_user") -> str:
    response = client.post("/api/v1/sessions", json={"locale": "zh-CN"}, headers={"x-user-id": user_id})
    assert response.status_code == 200
    return response.json()["data"]["sessionId"]


def post_turn(client: TestClient, session_id: str, content: str, *, user_id: str = "v11_user"):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": f"client_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers={"x-user-id": user_id},
    )


def test_verify_output_detects_closed_inline_and_block_formulas(tmp_path):
    """§5.2 公式约束检查必须识别正常的 $...$ 与 $$...$$ 公式（回归：原正则漏检块级公式）。"""
    from app.services.coach import verify_output
    from app.schemas import ResolvedTask

    low = ResolvedTask(
        goal="讲直觉",
        domain="machine_learning",
        concept="sigmoid",
        proposition="用直觉讲 sigmoid",
        desired_depth="L1",
        task_scope="直觉解释",
        output_preference=[],
    )
    no_formula = verify_output(low, "先建立一个直觉：输入越大输出越接近 1。")
    assert no_formula["status"] == "passed"

    inline = verify_output(low, "变化率是 $x^2$ 这样的量。")
    assert inline["status"] == "partial"
    assert "low_depth_formula" in inline["violations"]

    block = verify_output(low, "整体可以写成 $$\ny = mx + b\n$$")
    assert block["status"] == "partial"
    assert "low_depth_formula" in block["violations"]

    no_code = verify_output(low, "不需要代码，只看直觉。")
    assert "low_depth_code" not in no_code["violations"]

    has_code = verify_output(low, "实现是 ```python\nx = 1\n```")
    assert has_code["status"] == "partial"
    assert "low_depth_code" in has_code["violations"]


def test_invalid_request_uses_v11_error_envelope(tmp_path):
    with create_client(tmp_path) as client:
        response = client.post("/api/v1/sessions", json={"locale": 123})

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "INVALID_REQUEST"
    assert payload["retryable"] is False
    assert payload["requestId"].startswith("req_")


def test_fair_ab_fork_clones_baseline_and_freezes_memory_mode(tmp_path):
    with create_client(tmp_path) as client:
        source = create_session(client)
        first = post_turn(client, source, "我知道导数，但不理解反向传播每层梯度怎样连接。")
        assert first.status_code == 200

        response = client.post(
            f"/api/v1/sessions/{source}/forks",
            json={},
            headers={"x-user-id": "v11_user"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sourceSessionId"] == source
        assert {fork["memoryMode"] for fork in data["forks"]} == {"on", "off"}
        assert data["forks"][0]["forkGroupId"] == data["forks"][1]["forkGroupId"]

        removed_mode_selection = client.post(
            f"/api/v1/sessions/{source}/forks",
            json={"memoryModes": ["on", "off"]},
            headers={"x-user-id": "v11_user"},
        )
        assert removed_mode_selection.status_code == 422
        assert removed_mode_selection.json()["error"]["code"] == "INVALID_REQUEST"

        for fork in data["forks"]:
            fork_session = fork["sessionId"]
            replay = post_turn(client, fork_session, "解释刚才的梯度连接。")
            assert replay.status_code == 200
            assert parse_sse(replay.text)[-1]["type"] == "turn.completed"

        removed_override = client.post(
            f"/api/v1/sessions/{data['forks'][0]['sessionId']}/turns",
            json={
                "message": {"content": "再讲一次"},
                "clientTurnId": f"client_{uuid.uuid4().hex[:12]}",
                "locale": "zh-CN",
                "memoryMode": "off",
            },
            headers={"x-user-id": "v11_user"},
        )
        assert removed_override.status_code == 422
        assert removed_override.json()["error"]["code"] == "INVALID_REQUEST"


def test_fair_ab_fork_does_not_mutate_long_term_memory(tmp_path):
    with create_client(tmp_path) as client:
        source = create_session(client)
        post_turn(client, source, "以后讲解时先给数值例子，记住这个偏好。")
        before = [m.rule for m in memory_service.list_memories("v11_user")]

        response = client.post(
            f"/api/v1/sessions/{source}/forks",
            json={},
            headers={"x-user-id": "v11_user"},
        )
        forks = response.json()["data"]["forks"]
        for fork in forks:
            post_turn(client, fork["sessionId"], "以后请先讲公式，记住这个偏好。")

        after = [m.rule for m in memory_service.list_memories("v11_user")]
        assert after == before


def test_metrics_summary_reports_runtime_percentiles(tmp_path):
    with create_client(tmp_path) as client:
        session = create_session(client)
        response = post_turn(client, session, "我知道导数，但不理解反向传播每层梯度怎样连接。")
        assert response.status_code == 200

        summary = client.get("/api/v1/metrics/summary", headers={"x-user-id": "v11_user"})
        assert summary.status_code == 200
        data = summary.json()["data"]
        assert data["turns"]["completed"] >= 1
        assert data["latencyMs"]["p50"] is not None
        assert data["latencyMs"]["p95"] is not None
        assert data["ttftMs"]["p50"] is not None
        assert data["memoryModeCounts"]["default"] >= 1


def test_fair_ab_fork_freezes_memory_snapshot(tmp_path):
    with create_client(tmp_path) as client:
        source = create_session(client)
        response = client.post(
            f"/api/v1/sessions/{source}/forks",
            json={},
            headers={"x-user-id": "v11_user"},
        )
        forks = response.json()["data"]["forks"]
        memory_service.write_memory(
            "v11_user", type_="interaction_rule", rule="fork 之后新增的规则", source_event_id="evt_after_fork"
        )
        on_fork = next(fork for fork in forks if fork["memoryMode"] == "on")
        response = post_turn(client, on_fork["sessionId"], "我知道导数，但不理解反向传播每层梯度怎样连接。")
        presentation = parse_sse(response.text)[-1]["presentation"]
        assert all("fork 之后新增的规则" not in item["label"] for item in presentation["personalization"])


def test_fork_creation_rolls_back_on_partial_failure(tmp_path, monkeypatch):
    """双分支创建必须原子：第二个分支失败时，第一个分支也不能落库（无孤儿）。"""
    from app.services import brief as brief_service

    db.reset_for_tests(str(tmp_path / "v11_rollback.db"))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        source = create_session(client)
        calls = {"n": 0}
        original = brief_service.create_session_fork

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("boom")
            return original(*args, **kwargs)

        monkeypatch.setattr(brief_service, "create_session_fork", flaky)
        response = client.post(
            f"/api/v1/sessions/{source}/forks",
            json={},
            headers={"x-user-id": "v11_user"},
        )
        assert response.status_code == 500
        assert calls["n"] == 2
        leftovers = db.query(
            "SELECT COUNT(*) AS n FROM session_forks WHERE source_session_id=?", (source,)
        )
        assert leftovers[0]["n"] == 0
        orphan_sessions = db.query(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id=? AND id != ?", ("v11_user", source)
        )
        assert orphan_sessions[0]["n"] == 0
