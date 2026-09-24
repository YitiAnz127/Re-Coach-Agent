"""单用户锁定（RECOACH_LOCKED_USER）的回归测试。

背景：本项目默认的信任模型是"单实例 + 可信客户端"，身份由 `x-user-id` 表达，
因此**任何持令牌者都能读写他人的记忆与会话**。本机单人使用没有实际风险，
但把服务暴露给更多人（同机多账户、内网共享）时就需要一个收紧开关。

开启后身份被钉死为配置的单一用户；请求显式指定他人会被**拒绝**而不是
静默忽略——静默忽略会让冒充尝试在日志里完全不可见。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db
from app.config import Settings, get_settings
from app.main import create_app
from app.services import memory as memory_service
from app.services import ratelimit, turn_gate

LOCKED = "alice"
TOKEN = "identity-lock-token"


@pytest.fixture(autouse=True)
def _settings_cache_isolation():
    """Settings 是 lru_cache 的，改环境变量前后都必须清缓存。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(tmp_path, monkeypatch, *, locked: str = "", token: str = "", name: str = "lock"):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / f"{name}.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", token)
    monkeypatch.setenv("RECOACH_LOCKED_USER", locked)
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / f"{name}.db"))
    ratelimit.reset()
    turn_gate.reset()
    return TestClient(create_app())


def _create_session(client: TestClient, user_id: str = "") -> str:
    headers = {"x-user-id": user_id} if user_id else {}
    response = client.post("/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers)
    assert response.status_code == 200
    return response.json()["data"]["sessionId"]


# ------------------------------------------------- 默认行为必须完全不变

def test_default_allows_free_identity_selection(tmp_path, monkeypatch):
    """未配置锁定时保持原样：x-user-id 仍然自由取值（单机单人模型）。"""
    client = _client(tmp_path, monkeypatch)
    memory_service.write_memory(
        "bob", type_="explanation_preference",
        rule="bob 的私有偏好", source_event_id="evt_lock_free",
    )
    response = client.get("/api/v1/memories", headers={"x-user-id": "bob"})
    assert response.status_code == 200
    assert any(m["rule"] == "bob 的私有偏好" for m in response.json()["data"])


def test_default_without_header_uses_dev_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    memory_service.write_memory(
        "dev_user", type_="explanation_preference",
        rule="默认用户偏好", source_event_id="evt_lock_dev",
    )
    response = client.get("/api/v1/memories")
    assert response.status_code == 200
    assert any(m["rule"] == "默认用户偏好" for m in response.json()["data"])


# ------------------------------------------------- 锁定后身份被钉死

def test_lock_forces_configured_identity(tmp_path, monkeypatch):
    """不带头时身份就是锁定值，而不是 dev_user。"""
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    memory_service.write_memory(
        LOCKED, type_="explanation_preference",
        rule="alice 的偏好", source_event_id="evt_lock_own",
    )
    response = client.get("/api/v1/memories")
    assert response.status_code == 200
    assert any(m["rule"] == "alice 的偏好" for m in response.json()["data"])


def test_lock_accepts_matching_header(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    assert client.get(
        "/api/v1/memories", headers={"x-user-id": LOCKED}
    ).status_code == 200


def test_lock_rejects_impersonation_with_401(tmp_path, monkeypatch):
    """显式冒充他人必须被明确拒绝，且响应里不得出现对方数据。"""
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    memory_service.write_memory(
        "bob", type_="explanation_preference",
        rule="bob 的私有偏好", source_event_id="evt_lock_victim",
    )
    response = client.get("/api/v1/memories", headers={"x-user-id": "bob"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert "bob 的私有偏好" not in response.text


def test_lock_collapses_session_routes_to_404(tmp_path, monkeypatch):
    """会话级路由沿用既有约定：不区分"无权限"与"不存在"。"""
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    session_id = _create_session(client)

    other = client.get(
        f"/api/v1/sessions/{session_id}/turns", headers={"x-user-id": "bob"}
    )
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "SESSION_NOT_FOUND"

    # 锁定用户自己仍然读得到
    assert client.get(
        f"/api/v1/sessions/{session_id}/turns", headers={"x-user-id": LOCKED}
    ).status_code == 200


def test_lock_blocks_creating_turn_as_another_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    session_id = _create_session(client)
    response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "我知道导数，请解释链式法则。"},
            "clientTurnId": "c_impersonate",
            "locale": "zh-CN",
        },
        headers={"x-user-id": "bob"},
    )
    assert response.status_code == 404
    assert db.query(
        "SELECT id FROM turns WHERE session_id=?", (session_id,)
    ) == [], "被拒的请求不得留下任何 Turn 行"


# ------------------------------------------------- 与令牌模式叠加

def test_lock_applies_in_token_mode(tmp_path, monkeypatch):
    """锁定与令牌是两层独立机制：配上令牌后锁定依然生效。"""
    client = _client(tmp_path, monkeypatch, locked=LOCKED, token=TOKEN, name="locked_token")
    auth = {"authorization": f"Bearer {TOKEN}"}

    assert client.get("/api/v1/memories").status_code == 401          # 无令牌
    assert client.get("/api/v1/memories", headers=auth).status_code == 200
    assert client.get(
        "/api/v1/memories", headers={**auth, "x-user-id": "bob"}
    ).status_code == 401


def test_health_is_unaffected_by_lock(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, locked=LOCKED)
    assert client.get("/health").status_code == 200


# ------------------------------------------------- 配置校验

def test_invalid_locked_user_fails_loudly(monkeypatch):
    """非法值必须让进程起不来。

    静默忽略等于锁根本没开，而部署方会以为已经隔离好了——比不提供开关更危险。
    """
    monkeypatch.setenv("RECOACH_LOCKED_USER", "bad user id!")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()


def test_locked_user_is_trimmed(monkeypatch):
    monkeypatch.setenv("RECOACH_LOCKED_USER", "  alice  ")
    get_settings.cache_clear()
    assert Settings().locked_user == "alice"
