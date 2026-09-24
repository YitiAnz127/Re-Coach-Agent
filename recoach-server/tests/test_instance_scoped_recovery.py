"""启动恢复的实例隔离回归测试。

背景：`recover_stale_streaming` 原本无条件把**所有** `streaming` 行标为 error。
单实例部署下没问题，但多进程共享同一份 DB 时，任何一个进程启动都会把别的进程
正在流式输出的轮次标成 error；那些轮次随后被客户端重试认领，导致同一轮
重复执行（双份 LLM 成本 + 两个写入者写同一行）。

修复：Turn 记录创建者（`owner_instance`），启动恢复只回收本实例的行。
"""
from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.services import brief as brief_service
from app.services import instances
from app.services import orchestrator
from app.services import turns as turn_store


def _payload() -> dict:
    return {"message": {"content": "解释梯度下降。"}, "locale": "zh-CN"}


def _create_turn_as(monkeypatch, session_id: str, client_turn_id: str, owner: str) -> str:
    """以指定实例身份创建一个 streaming Turn。"""
    monkeypatch.setattr(instances, "current_instance_id", lambda: owner)
    return turn_store.create_turn(session_id, "u", client_turn_id, _payload())


def test_own_streaming_turn_is_recovered_on_startup(tmp_path, monkeypatch):
    """本实例遗留的 Turn 仍然启动即恢复——这是原有的恢复保证，不能被削弱。"""
    db.reset_for_tests(str(tmp_path / "own.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_own", "instance-a")

    recovered = turn_store.recover_stale_streaming(instance_id="instance-a")

    assert recovered == 1
    row = turn_store.find_by_client_key(session_id, "c_own")
    assert row is not None
    assert row["status"] == "error"
    assert row["id"] == turn_id


def test_other_instances_streaming_turn_is_left_alone(tmp_path, monkeypatch):
    """核心修复：别的实例正在流式输出的 Turn 不得被本实例回收。"""
    db.reset_for_tests(str(tmp_path / "other.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    _create_turn_as(monkeypatch, session_id, "c_other", "instance-a")

    recovered = turn_store.recover_stale_streaming(instance_id="instance-b")

    assert recovered == 0
    row = turn_store.find_by_client_key(session_id, "c_other")
    assert row is not None
    assert row["status"] == "streaming"


def test_only_own_turns_are_recovered_when_both_exist(tmp_path, monkeypatch):
    """混合场景：只回收自己的，别人的原样保留。"""
    db.reset_for_tests(str(tmp_path / "mixed.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    mine = _create_turn_as(monkeypatch, session_id, "c_mine", "instance-a")
    _create_turn_as(monkeypatch, session_id, "c_theirs", "instance-b")

    recovered = turn_store.recover_stale_streaming(instance_id="instance-a")

    assert recovered == 1
    assert turn_store.find_by_client_key(session_id, "c_mine")["id"] == mine
    assert turn_store.find_by_client_key(session_id, "c_mine")["status"] == "error"
    assert turn_store.find_by_client_key(session_id, "c_theirs")["status"] == "streaming"


def test_rows_without_owner_are_treated_as_own(tmp_path, monkeypatch):
    """迁移之前的行 owner_instance 为空，必须保持原有的恢复行为。"""
    db.reset_for_tests(str(tmp_path / "legacy.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_legacy", "instance-a")
    with db.tx() as conn:
        conn.execute("UPDATE turns SET owner_instance='' WHERE id=?", (turn_id,))

    recovered = turn_store.recover_stale_streaming(instance_id="instance-a")

    assert recovered == 1
    assert turn_store.find_by_client_key(session_id, "c_legacy")["status"] == "error"


def test_restart_turn_moves_ownership_to_the_claiming_instance(tmp_path, monkeypatch):
    """认领失败 Turn 的进程成为新 owner，之后才由它负责回收。"""
    db.reset_for_tests(str(tmp_path / "claim.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_claim", "instance-a")
    turn_store.finish_turn(turn_id, status="error", mode="explain")

    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-b")
    assert turn_store.restart_turn(turn_id) is True

    with db.tx() as conn:
        owner = conn.execute(
            "SELECT owner_instance FROM turns WHERE id=?", (turn_id,)
        ).fetchone()["owner_instance"]
    assert owner == "instance-b"

    # instance-a 启动时不该动它，instance-b 才该动它
    assert turn_store.recover_stale_streaming(instance_id="instance-a") == 0
    assert turn_store.find_by_client_key(session_id, "c_claim")["status"] == "streaming"
    assert turn_store.recover_stale_streaming(instance_id="instance-b") == 1


def test_startup_lifespan_does_not_recover_a_foreign_turn(tmp_path, monkeypatch):
    """HTTP 层端到端：别的实例留下的 streaming Turn 不会因本进程启动被改写。"""
    db.reset_for_tests(str(tmp_path / "lifespan.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    _create_turn_as(monkeypatch, session_id, "c_live", "instance-other")

    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-me")
    with TestClient(create_app(), raise_server_exceptions=False):
        row = turn_store.find_by_client_key(session_id, "c_live")
        assert row is not None
        assert row["status"] == "streaming"


def _backdate(turn_id: str) -> None:
    """把 updated_at 推到远超任何一轮合法时长之前，模拟真孤儿。"""
    with db.tx() as conn:
        conn.execute(
            "UPDATE turns SET updated_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000+00:00", turn_id),
        )


def test_orphan_row_with_foreign_owner_can_still_be_recovered(tmp_path, monkeypatch):
    """owner 既不匹配当前实例也不是空时，该行必须仍能自愈。

    触发场景：RECOACH_INSTANCE_ID 改过，或 runtime_meta 丢失而 turns 仍在。
    回归：初版只按 owner 过滤，这种行既不被启动恢复清理、也不被 restart_turn
    认领——同一个 clientTurnId 永远 409，只能改库才能出来。
    """
    db.reset_for_tests(str(tmp_path / "orphan.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_orphan", "instance-gone")
    _backdate(turn_id)

    row = turn_store.find_by_client_key(session_id, "c_orphan")
    assert turn_store.is_stale_streaming(row) is True

    assert turn_store.recover_stale_streaming(instance_id="instance-new") == 1
    assert turn_store.find_by_client_key(session_id, "c_orphan")["status"] == "error"

    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-new")
    assert turn_store.restart_turn(turn_id) is True


def test_stale_orphan_can_be_claimed_directly_by_retry(tmp_path, monkeypatch):
    """即使没经过启动恢复，重试也必须能认领孤儿行。"""
    db.reset_for_tests(str(tmp_path / "orphan_claim.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_orphan2", "instance-gone")
    _backdate(turn_id)

    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-new")
    assert turn_store.restart_turn(turn_id) is True
    assert turn_store.find_by_client_key(session_id, "c_orphan2")["status"] == "streaming"


def test_fresh_foreign_turn_is_never_stolen_or_recovered(tmp_path, monkeypatch):
    """核心安全断言：别的实例**正在跑**的轮次不得被回收，也不得被认领。

    这是 owner 归属存在的意义；按运行时长的兜底不能把它削弱。
    """
    db.reset_for_tests(str(tmp_path / "fresh_foreign.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_live", "instance-other")

    row = turn_store.find_by_client_key(session_id, "c_live")
    assert turn_store.is_stale_streaming(row) is False
    assert turn_store.recover_stale_streaming(instance_id="instance-me") == 0
    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-me")
    assert turn_store.restart_turn(turn_id) is False
    assert turn_store.find_by_client_key(session_id, "c_live")["status"] == "streaming"


# --------------------------------------------------------- 心跳（孤儿判定的地基）

def test_heartbeat_keeps_a_long_running_turn_fresh(tmp_path, monkeypatch):
    """核心修复：只要还在心跳，跑多久都不是孤儿。

    回归：初版按"运行总时长"判定孤儿，但 llm_timeout 传给 httpx 时限制的是
    **两次数据之间的间隔**、不是总时长，所以稳定输出的合法轮次会被判成孤儿
    并抢走 → 同一轮重复执行。判据必须是"失去心跳多久"。
    """
    db.reset_for_tests(str(tmp_path / "hb_keep.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_long", "instance-other")
    _backdate(turn_id)  # 先把它做成"看起来早就该死了"

    assert turn_store.is_stale_streaming(turn_store.find_by_client_key(session_id, "c_long")) is True

    # 一次心跳就证明它还活着
    assert turn_store.touch_turn(turn_id) is True
    row = turn_store.find_by_client_key(session_id, "c_long")
    assert turn_store.is_stale_streaming(row) is False

    # 于是既不被回收，也抢不走
    monkeypatch.setattr(instances, "current_instance_id", lambda: "instance-me")
    assert turn_store.restart_turn(turn_id) is False
    assert turn_store.recover_stale_streaming(instance_id="instance-me") == 0
    assert turn_store.find_by_client_key(session_id, "c_long")["status"] == "streaming"


def test_touch_turn_ignores_finished_turns(tmp_path, monkeypatch):
    """已结束的轮次不再刷新心跳（心跳只对 streaming 有意义）。"""
    db.reset_for_tests(str(tmp_path / "hb_done.db"))
    session_id = brief_service.create_session("u", "zh-CN")
    turn_id = _create_turn_as(monkeypatch, session_id, "c_done", "instance-a")
    turn_store.finish_turn(turn_id, status="completed", mode="explain", presentation={"mode": "explain", "focus": "f", "plan": []})

    assert turn_store.touch_turn(turn_id) is False


def test_streaming_turn_actually_sends_heartbeats(tmp_path, monkeypatch):
    """端到端接线：流式期间必须真的调用 touch_turn。"""
    db.reset_for_tests(str(tmp_path / "hb_wire.db"))
    calls: list[str] = []

    def recorder(turn_id: str) -> bool:
        calls.append(turn_id)
        return True

    monkeypatch.setattr(turn_store, "touch_turn", recorder)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        session_id = client.post(
            "/api/v1/sessions", json={"locale": "zh-CN"}, headers={"x-user-id": "u"}
        ).json()["data"]["sessionId"]
        response = client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
                "clientTurnId": "c_hb_wire",
                "locale": "zh-CN",
            },
            headers={"x-user-id": "u"},
        )
        assert response.status_code == 200

    # 心跳在线程里发送，断言前给它一点调度时间，避免 flaky
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    assert calls, "流式期间没有发出任何心跳：长轮次会被误判为孤儿"


def test_heartbeat_fires_even_with_no_sse_events(tmp_path, monkeypatch):
    """心跳不依赖事件：上游"有数据但不产出事件"时也必须继续跳。

    回归：心跳曾经挂在 SSE 事件循环里，于是代理注入的 `: keep-alive` 注释行、
    或 `{"choices":[{"delta":{}}]}` 这类空分片会让它静默停掉——而 httpx 的
    read timeout 因为"数据在流动"也不会触发。超过阈值后该轮被判成孤儿并抢走，
    正是 owner 机制要消除的重复执行。
    """
    db.reset_for_tests(str(tmp_path / "hb_silent.db"))
    calls: list[str] = []
    monkeypatch.setattr(turn_store, "touch_turn", lambda tid: calls.append(tid) or True)
    monkeypatch.setattr(turn_store, "TURN_HEARTBEAT_SECONDS", 0.2)

    async def silent_turn(**_kwargs):
        # 远长于心跳间隔，但一个事件都不产出
        await asyncio.sleep(0.9)
        yield {"type": "turn.error", "turnId": "t", "code": "INTERNAL", "message": "silent"}

    monkeypatch.setattr(orchestrator, "run_turn", silent_turn)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        session_id = client.post(
            "/api/v1/sessions", json={"locale": "zh-CN"}, headers={"x-user-id": "u"}
        ).json()["data"]["sessionId"]
        response = client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
                "clientTurnId": "c_hb_silent",
                "locale": "zh-CN",
            },
            headers={"x-user-id": "u"},
        )
        assert response.status_code == 200

    assert len(calls) >= 3, f"事件静默期中心跳停了：0.9 秒内只跳了 {len(calls)} 次"


def test_heartbeat_survives_transient_db_errors(tmp_path, monkeypatch):
    """一次瞬时 DB 错误不能让心跳线程死掉。

    回归：`heartbeat()` 原本没有 try/except，一次锁竞争/IO 抖动就让线程带着
    未捕获异常退出——停摆超过阈值后这一轮会被判成孤儿并抢走（重复执行），
    而这正是心跳要防的事。
    """
    db.reset_for_tests(str(tmp_path / "hb_err.db"))
    monkeypatch.setattr(turn_store, "TURN_HEARTBEAT_SECONDS", 0.05)
    calls = {"ok": 0, "err": 0}

    def flaky(turn_id: str) -> bool:
        if calls["err"] < 3:  # 前 3 次模拟瞬时故障
            calls["err"] += 1
            raise RuntimeError("simulated transient DB error")
        calls["ok"] += 1
        return True

    monkeypatch.setattr(turn_store, "touch_turn", flaky)

    async def slow_turn(**_kwargs):
        await asyncio.sleep(0.6)
        yield {"type": "turn.error", "turnId": "t", "code": "INTERNAL", "message": "slow"}

    monkeypatch.setattr(orchestrator, "run_turn", slow_turn)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        session_id = client.post(
            "/api/v1/sessions", json={"locale": "zh-CN"}, headers={"x-user-id": "u"}
        ).json()["data"]["sessionId"]
        response = client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
                "clientTurnId": "c_hb_err",
                "locale": "zh-CN",
            },
            headers={"x-user-id": "u"},
        )
        assert response.status_code == 200

    assert calls["err"] == 3
    assert calls["ok"] >= 3, (
        f"心跳在瞬时错误后停摆了：抛错 {calls['err']} 次后只成功 {calls['ok']} 次"
    )
