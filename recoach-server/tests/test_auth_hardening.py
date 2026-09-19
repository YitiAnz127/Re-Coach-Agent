"""访问控制与稳健性加固的回归测试。

覆盖 2026-09-17 安全审查中确认的问题：
- 无认证：任意伪造 x-user-id 即可读写他人记忆/会话
- 无速率限制：LLM 计费端点可无限刷
- 无并发上限：可开大量 SSE 拖垮进程
- 生产暴露 /docs 与 /openapi.json
- 客户端可控的 x-request-id 未净化
- locale / 请求体无长度上限
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.services import memory as memory_service
from app.services import ratelimit, turn_gate

TOKEN = "test-token-please-change"


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    """令牌模式客户端。"""
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", TOKEN)
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "auth.db"))
    ratelimit.reset()
    turn_gate.reset()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture()
def dev_client(tmp_path, monkeypatch):
    """开发模式客户端（未配置令牌，TestClient 属于回环来源）。"""
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "dev.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "dev.db"))
    ratelimit.reset()
    turn_gate.reset()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


# --------------------------------------------------------------- 鉴权

def test_api_requires_token_when_configured(auth_client):
    """配置令牌后，无凭证访问 /api/v1 一律 401（这是修复前可任意读他人记忆的入口）。"""
    for path in ("/api/v1/memories", "/api/v1/meta", "/api/v1/metrics/summary"):
        response = auth_client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_api_rejects_wrong_token(auth_client):
    response = auth_client.get("/api/v1/memories", headers={"authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_api_accepts_correct_token(auth_client):
    response = auth_client.get("/api/v1/memories", headers=_auth())
    assert response.status_code == 200


def test_health_stays_public(auth_client):
    """健康检查必须免鉴权，否则容器编排无法探活。"""
    assert auth_client.get("/health").status_code == 200


def test_cannot_read_other_users_memories_without_token(auth_client):
    """冒烟复现原漏洞：修复后伪造 x-user-id 无法绕过鉴权。"""
    memory_service.write_memory(
        "victim", type_="explanation_preference",
        rule="受害者私有偏好", source_event_id="evt_auth_1",
    )
    response = auth_client.get("/api/v1/memories", headers={"x-user-id": "victim"})
    assert response.status_code == 401
    assert "受害者私有偏好" not in response.text


def test_docs_hidden_in_token_mode(auth_client):
    """配了令牌就不该再暴露交互式文档与 openapi。"""
    assert auth_client.get("/docs").status_code == 404
    assert auth_client.get("/openapi.json").status_code == 404


def test_docs_available_in_dev_mode(dev_client):
    assert dev_client.get("/docs").status_code == 200


def test_dev_mode_rejects_non_local_client(tmp_path, monkeypatch):
    """开发模式只信任回环来源；来自外部地址的请求必须 401。"""
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "remote.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "remote.db"))
    with TestClient(create_app(), client=("203.0.113.7", 51234)) as c:
        assert c.get("/api/v1/memories").status_code == 401
    get_settings.cache_clear()


def test_invalid_user_id_is_rejected(auth_client):
    """异常字符集的 x-user-id 不能进入 SQL/日志。"""
    response = auth_client.get(
        "/api/v1/memories", headers={**_auth(), "x-user-id": "bad user id!"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------- 限流与并发

def test_rate_limit_returns_429(auth_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]

    codes = []
    for i in range(6):
        codes.append(
            auth_client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={
                    "message": {"content": f"我知道导数，请解释第{i}次链式法则。"},
                    "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
                    "locale": "zh-CN",
                },
                headers=headers,
            ).status_code
        )
    assert codes.count(200) == 3
    assert 429 in codes
    assert codes[-1] == 429


def test_replay_does_not_consume_quota(auth_client, monkeypatch):
    """完成态重放不产生 LLM 成本，不应吃掉限流配额。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]
    payload = {
        "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
        "clientTurnId": "c_replay_quota",
        "locale": "zh-CN",
    }
    assert auth_client.post(
        f"/api/v1/sessions/{session_id}/turns", json=payload, headers=headers
    ).status_code == 200
    # 配额只剩 1；重放同一 key 多次仍应成功
    for _ in range(3):
        assert auth_client.post(
            f"/api/v1/sessions/{session_id}/turns", json=payload, headers=headers
        ).status_code == 200


def test_concurrency_gate_returns_503(auth_client, monkeypatch):
    """并发槽位占满时新 Turn 应被拒绝，而不是无限堆积。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "max_concurrent_turns", 1)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]

    # 手工占满唯一槽位（模拟一个正在流式输出的 Turn）
    assert turn_gate.try_reserve(1) is True
    try:
        response = auth_client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": "我知道导数，请解释链式法则。"},
                "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
                "locale": "zh-CN",
            },
            headers=headers,
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_BUSY"
    finally:
        turn_gate.reset()


def test_slot_is_released_after_turn(auth_client):
    """一轮 Turn 结束后槽位必须归还，否则闸门会永久卡死。"""
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]
    auth_client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "我知道导数，请解释链式推理。"},
            "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers=headers,
    )
    assert turn_gate.inflight() == 0


def test_rate_limited_turn_leaves_no_stuck_row(auth_client, monkeypatch):
    """被限流拒绝的 Turn 不得留下 status='streaming' 的僵尸行。

    回归 2026-09-17：限流最初放在 create_turn 之后，被拒请求会落一条永远
    不会完成的 streaming 行，导致同一个 clientTurnId 之后永远 409
    TURN_IN_PROGRESS——用户那条消息彻底卡死，只能重启进程恢复。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]

    def turn(key: str, text: str):
        return auth_client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": text},
                "clientTurnId": key,
                "locale": "zh-CN",
            },
            headers=headers,
        )

    # 第一条吃掉唯一配额
    assert turn("c_stuck_a", "我知道导数，请解释链式法则。").status_code == 200
    # 第二条（不同 key，必须真正执行）撞限流
    blocked = turn("c_stuck_b", "我知道导数，请解释梯度下降的方向。")
    assert blocked.status_code == 429

    rows = db.query(
        "SELECT id, client_turn_id FROM turns WHERE session_id=? AND status='streaming'",
        (session_id,),
    )
    assert rows == [], f"限流拒绝后残留 streaming 僵尸行：{[r['client_turn_id'] for r in rows]}"

    # 放开限流后，被拒的那条必须能正常执行，而不是永远 TURN_IN_PROGRESS
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    retried = turn("c_stuck_b", "我知道导数，请解释梯度下降的方向。")
    assert retried.status_code == 200
    assert "turn.completed" in retried.text


def test_busy_turn_leaves_no_stuck_row(auth_client, monkeypatch):
    """被并发闸门拒绝的 Turn 同样不得留下僵尸行。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "max_concurrent_turns", 1)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]
    payload = {
        "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
        "clientTurnId": "c_busy_row",
        "locale": "zh-CN",
    }
    assert turn_gate.try_reserve(1) is True
    try:
        assert auth_client.post(
            f"/api/v1/sessions/{session_id}/turns", json=payload, headers=headers
        ).status_code == 503
        assert db.query(
            "SELECT id FROM turns WHERE session_id=? AND status='streaming'", (session_id,)
        ) == []
    finally:
        turn_gate.reset()

    assert auth_client.post(
        f"/api/v1/sessions/{session_id}/turns", json=payload, headers=headers
    ).status_code == 200


def test_conflict_and_replay_do_not_leak_slot(auth_client, monkeypatch):
    """409 冲突路径必须归还并发槽位。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    headers = _auth()
    session_id = auth_client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers
    ).json()["data"]["sessionId"]
    assert auth_client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "我知道导数，请解释链式法则。"},
            "clientTurnId": "c_no_leak",
            "locale": "zh-CN",
        },
        headers=headers,
    ).status_code == 200

    # 同 key 不同内容 -> 409，不应占用槽位
    assert auth_client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "完全不同的内容。"},
            "clientTurnId": "c_no_leak",
            "locale": "zh-CN",
        },
        headers=headers,
    ).status_code == 409
    assert turn_gate.inflight() == 0


# --------------------------------------------------------------- 输入边界

def test_oversized_locale_rejected(auth_client):
    response = auth_client.post(
        "/api/v1/sessions", json={"locale": "x" * 200}, headers=_auth()
    )
    assert response.status_code == 422


def test_oversized_body_rejected(auth_client):
    """Content-Length 超上限应在读 body 之前被拒（413）。"""
    response = auth_client.post(
        "/api/v1/sessions",
        content=b'{"locale":"' + b"x" * 100_000 + b'"}',
        headers={**_auth(), "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_request_id_is_sanitized(auth_client):
    """客户端可控的 x-request-id 必须净化后再回填响应头。

    注入内容用 chr() 构造，避免源码里出现真实控制字节。
    """
    injected = "abc" + chr(1) + "def;rm -rf" + chr(27) + "[31m"
    response = auth_client.get(
        "/api/v1/meta", headers={**_auth(), "x-request-id": injected}
    )
    echoed = response.headers.get("x-request-id", "")
    assert chr(1) not in echoed
    assert chr(27) not in echoed
    assert ";" not in echoed
    assert echoed  # 净化后仍要有可追踪的值

def test_request_id_length_capped(auth_client):
    response = auth_client.get(
        "/api/v1/meta", headers={**_auth(), "x-request-id": "a" * 500}
    )
    assert len(response.headers.get("x-request-id", "")) <= 128


# --------------------------------------------------------------- 数据层

def test_forget_short_keyword_does_not_archive_everything(auth_client):
    """「忘记关于你的规则」曾会把所有含「你」的记忆一并归档。"""
    for i in range(3):
        memory_service.write_memory(
            "dev_user", type_="explanation_preference",
            rule=f"请用数值例子讲解第{i}个概念", source_event_id=f"evt_forget_{i}",
        )
    memory_service.write_memory(
        "dev_user", type_="explanation_preference",
        rule="你的偏好：先给公式", source_event_id="evt_forget_target",
    )
    before = len(memory_service.list_memories("dev_user", status="active"))
    forgotten = memory_service.forget_memories("dev_user", "你", "evt_forget_op")
    after = len(memory_service.list_memories("dev_user", status="active"))
    assert forgotten == []
    assert after == before, "单字关键字不得批量归档"


def test_forget_normal_keyword_still_works(auth_client):
    memory_service.write_memory(
        "dev_user", type_="explanation_preference",
        rule="先给公式再讲例子", source_event_id="evt_ok_1",
    )
    forgotten = memory_service.forget_memories("dev_user", "先给公式", "evt_ok_op")
    assert len(forgotten) == 1


def test_retrieve_is_bounded(auth_client):
    """候选集必须有上限，否则单轮成本随历史记忆线性上升。"""
    for i in range(30):
        memory_service.write_memory(
            "dev_user", type_="explanation_preference",
            rule=f"关于反向传播的偏好 {i}", source_event_id=f"evt_bound_{i}",
        )
    from app.schemas import ResolvedTask

    task = ResolvedTask(
        domain="ml", concept="反向传播", proposition="链式法则", task="explain", depth="L2"
    )
    result = memory_service.retrieve("dev_user", task)
    # 召回仍受 memory_hard_limit 约束，不因候选变多而放大
    assert len(result.selected) <= get_settings().memory_hard_limit


def test_fts_operator_chars_do_not_break_search(auth_client):
    """FTS 元字符不得注入查询语法或导致 500。"""
    memory_service.write_memory(
        "dev_user", type_="explanation_preference",
        rule="关于梯度的偏好", source_event_id="evt_fts_1",
    )
    for payload in ['" OR 1=1 --', "NEAR( ^ * ( )", "a* OR b*", '""""']:
        assert memory_service._fts_candidates("dev_user", payload) is not None


# --------------------------------------------------------------- 可信网段（docker 反代）

def _client_for(host: str, token: str, trusted: str, tmp_path, name: str):
    """按给定来源 IP / 令牌 / 可信网段构造客户端。"""
    os.environ["RECOACH_DB_PATH"] = str(tmp_path / f"{name}.db")
    os.environ["RECOACH_API_TOKEN"] = token
    os.environ["RECOACH_TRUSTED_HOSTS"] = trusted
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / f"{name}.db"))
    ratelimit.reset()
    turn_gate.reset()
    return TestClient(create_app(), client=(host, 51234))


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", 200),      # 本机
        ("172.28.0.3", 200),     # nginx 容器（反代来源）
        ("172.28.0.99", 200),    # 同网段
        ("192.168.1.50", 401),   # 同局域网其他机器
        ("10.0.0.7", 401),       # 其他内网
        ("203.0.113.7", 401),    # 公网
    ],
)
def test_trusted_network_scopes_dev_mode(host, expected, tmp_path):
    """回归：nginx 反代来源是容器内网 IP，只认回环会让默认部署全部 401。

    同时必须确保放开的只有声明的网段——局域网/公网仍要被拒。
    """
    with _client_for(host, "", "172.28.0.0/24", tmp_path, "trusted") as c:
        assert c.get("/api/v1/memories").status_code == expected


def test_empty_trusted_hosts_keeps_strict_loopback(tmp_path):
    """未声明可信网段时维持最严默认：只信回环。"""
    with _client_for("172.28.0.3", "", "", tmp_path, "strict") as c:
        assert c.get("/api/v1/memories").status_code == 401
    with _client_for("127.0.0.1", "", "", tmp_path, "strict_local") as c:
        assert c.get("/api/v1/memories").status_code == 200


def test_trusted_hosts_ignored_in_token_mode(tmp_path):
    """配了令牌后可信网段必须失效：来源可信不等于已认证。"""
    with _client_for("172.28.0.3", TOKEN, "172.28.0.0/24", tmp_path, "tokenize") as c:
        assert c.get("/api/v1/memories").status_code == 401
        assert c.get("/api/v1/memories", headers=_auth()).status_code == 200


def test_malformed_trusted_hosts_fails_closed(tmp_path):
    """写错的网段必须失败关闭（拒绝），不能因为解析失败就放行。"""
    with _client_for("172.28.0.3", "", "not-a-network,,999.1.1.1", tmp_path, "badnet") as c:
        assert c.get("/api/v1/memories").status_code == 401
