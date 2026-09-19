"""会话历史恢复接口。

背景（2026-09-18 用户旅程实测）：刷新页面后整段对话消失。
原因是 sessionId 只存在前端内存里，且**后端根本没有读取历史的接口**。
本文件锁死该接口的正确性与归属语义。
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "hist.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "hist.db"))
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _new_session(client, headers=None) -> str:
    return client.post(
        "/api/v1/sessions", json={"locale": "zh-CN"}, headers=headers or {}
    ).json()["data"]["sessionId"]


def _turn(client, session_id: str, text: str, headers=None):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": text},
            "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers=headers or {},
    )


def test_history_is_empty_for_a_fresh_session(client):
    sid = _new_session(client)
    resp = client.get(f"/api/v1/sessions/{sid}/turns")
    assert resp.status_code == 200
    assert resp.json()["data"]["turns"] == []


def test_history_returns_turns_in_order(client):
    sid = _new_session(client)
    _turn(client, sid, "讲讲反向传播")
    _turn(client, sid, "1")

    turns = client.get(f"/api/v1/sessions/{sid}/turns").json()["data"]["turns"]
    assert len(turns) == 2
    assert turns[0]["userText"] == "讲讲反向传播"
    assert turns[0]["presentation"]["mode"] == "clarify"
    assert turns[1]["presentation"]["mode"] == "explain"


def test_history_preserves_raw_user_input_not_resolved_text(client):
    """历史必须显示用户真正打的字；编号选择不能还原成长文本。"""
    sid = _new_session(client)
    _turn(client, sid, "讲讲反向传播")
    _turn(client, sid, "1")

    turns = client.get(f"/api/v1/sessions/{sid}/turns").json()["data"]["turns"]
    assert turns[1]["userText"] == "1", (
        "「1」被服务端解析成了选项的长文本，但历史里必须保留原始输入"
    )


def test_restored_turns_carry_renderable_presentation(client):
    """恢复出来的每一轮都要带完整 presentation，否则前端无法还原侧栏与选项。"""
    sid = _new_session(client)
    _turn(client, sid, "讲讲反向传播")

    turn = client.get(f"/api/v1/sessions/{sid}/turns").json()["data"]["turns"][0]
    presentation = turn["presentation"]
    assert turn["assistantText"], "要有正文"
    assert "metrics" in presentation
    assert presentation["clarificationOptions"], "澄清轮的选项必须一并返回"
    # 选项要有 followUp，前端点选后才知道该发什么
    assert all("followUp" in o for o in presentation["clarificationOptions"])


def test_missing_session_returns_404(client):
    assert client.get("/api/v1/sessions/ses_missing/turns").status_code == 404


def test_other_users_session_returns_404(client):
    """归属校验：别人的会话不能读，且与「不存在」返回一致（不泄露存在性）。"""
    sid = _new_session(client, headers={"x-user-id": "owner"})
    _turn(client, sid, "讲讲反向传播", headers={"x-user-id": "owner"})

    assert client.get(f"/api/v1/sessions/{sid}/turns").status_code == 404
    assert (
        client.get(f"/api/v1/sessions/{sid}/turns", headers={"x-user-id": "intruder"}).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/sessions/{sid}/turns", headers={"x-user-id": "owner"}).status_code
        == 200
    )


def test_history_requires_auth_in_token_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "tok-abc")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "auth.db"))
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/sessions/ses_x/turns").status_code == 401
        assert (
            c.get("/api/v1/sessions/ses_x/turns", headers={"authorization": "Bearer tok-abc"}).status_code
            == 404  # 鉴权通过，只是会话不存在
        )
    get_settings.cache_clear()


def test_incomplete_turns_are_not_restored(client):
    """未完成/出错轮次不该被恢复：没有可渲染的 presentation。"""
    sid = _new_session(client)
    _turn(client, sid, "讲讲反向传播")

    rows = db.query("SELECT id FROM turns WHERE session_id=?", (sid,))
    from app.services import turns as turn_store

    turn_store.finish_turn(rows[0]["id"], status="error", mode="explain", error={"code": "INTERNAL"})

    assert client.get(f"/api/v1/sessions/{sid}/turns").json()["data"]["turns"] == []


def test_history_limit_is_bounded(client):
    """历史接口必须有上限：长会话不能一次拉回无限多轮。"""
    from app.services import turns as turn_store

    sid = _new_session(client)
    _turn(client, sid, "讲讲反向传播")
    # 直接构造大量已完成轮次，验证 LIMIT 生效
    for i in range(5):
        tid = turn_store.create_turn(sid, "dev_user", f"seed_{i}", {"message": {"content": f"q{i}"}})
        turn_store.finish_turn(
            tid, status="completed", mode="explain",
            response_text="a", presentation={"mode": "explain"},
        )
    turns = turn_store.list_completed_turns(sid, limit=2)
    assert len(turns) == 2
