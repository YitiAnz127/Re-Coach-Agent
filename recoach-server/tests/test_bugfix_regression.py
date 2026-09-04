"""2026-08-26 核心 bug 回归测试：

1. 思考链约束（SYSTEM_PROMPT 必须包含精简思考规则）
2. 纯反馈轮不污染 Session Brief（goal/exact_anchors/concept_states）
3. fork 复制对话时剔除纯反馈轮，off 分支不泄漏偏好
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app import db
from app.main import create_app


def create_client(tmp_path):
    db.reset_for_tests(str(tmp_path / "bugfix.db"))
    return TestClient(create_app())


def create_session(client: TestClient, user_id: str = "bf_user") -> str:
    response = client.post("/api/v1/sessions", json={"locale": "zh-CN"}, headers={"x-user-id": user_id})
    assert response.status_code == 200
    return response.json()["data"]["sessionId"]


def post_turn(client: TestClient, session_id: str, content: str, *, user_id: str = "bf_user"):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": f"client_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
        headers={"x-user-id": user_id},
    )


def _brief(client: TestClient, session_id: str) -> dict:
    row = db.query_one("SELECT brief_json FROM sessions WHERE id=?", (session_id,))
    return json.loads(row["brief_json"])


def _messages(session_id: str) -> list[dict]:
    return [dict(r) for r in db.query("SELECT role, content FROM messages WHERE session_id=?", (session_id,))]


# ---------- 问题 1：思考链约束 ----------


def test_system_prompt_has_thinking_constraint():
    """回归：SYSTEM_PROMPT 必须包含精简思考约束（否则模型思考链失控）。"""
    from app.services.compiler import SYSTEM_PROMPT

    assert "思考过程必须精简" in SYSTEM_PROMPT
    assert "不要在思考中重复你即将输出的正文内容" in SYSTEM_PROMPT


# ---------- 问题 2a：纯反馈轮不污染 Brief ----------


def test_feedback_turn_does_not_pollute_brief_goal(tmp_path):
    """回归：反馈轮（≤40 字符记忆声明）不得把偏好文本写入 goal/exact_anchors。"""
    with create_client(tmp_path) as client:
        sid = create_session(client)
        response = post_turn(client, sid, "以后讲解时不要用公式，多用直觉和例子")
        assert response.status_code == 200

        brief = _brief(client, sid)
        assert "不要用公式" not in brief.get("goal", "")
        assert brief.get("current_focus") != "以后讲解时不要用公式，多用直觉和例子"
        assert brief.get("exact_anchors") == []


def test_feedback_turn_does_not_write_concept_state(tmp_path):
    """回归：反馈轮即使含概念词也不写 Concept State（避免污染 fork 快照）。"""
    with create_client(tmp_path) as client:
        sid = create_session(client)
        response = post_turn(client, sid, "以后讲梯度下降时不要用公式")
        assert response.status_code == 200

        rows = db.query("SELECT id FROM concept_states WHERE user_id=?", ("bf_user",))
        assert rows == []


# ---------- 问题 2b：fork 复制对话剔除纯反馈轮 ----------


def test_fork_skips_feedback_turns_in_messages(tmp_path):
    """回归：fork 分支的可见对话不得包含纯反馈轮（否则 off 分支泄漏偏好）。"""
    with create_client(tmp_path) as client:
        sid = create_session(client)
        post_turn(client, sid, "以后讲解时不要用公式，多用直觉和例子")
        post_turn(client, sid, "我知道导数，但不理解反向传播每层梯度怎样连接。")

        response = client.post(f"/api/v1/sessions/{sid}/forks", json={}, headers={"x-user-id": "bf_user"})
        assert response.status_code == 200
        forks = response.json()["data"]["forks"]
        assert {fork["memoryMode"] for fork in forks} == {"on", "off"}

        for fork in forks:
            contents = [m["content"] for m in _messages(fork["sessionId"])]
            # 反馈轮被剔除
            assert not any("不要用公式" in c for c in contents)
            # 教学轮保留（共同基线）
            assert any("反向传播" in c for c in contents)


def test_fork_off_branch_context_has_no_preference(tmp_path):
    """回归：fork off 分支编译出的上下文不含用户偏好文本。

    通过 events 的 memory_selected 记录与回复正文双重验证：
    非 fork 分支检索在 template 模式下为空（无外部模型），泄漏只能来自
    messages/brief 快照；该测试直接检查 fork 分支的消息与 Brief 均无偏好。
    """
    with create_client(tmp_path) as client:
        sid = create_session(client)
        post_turn(client, sid, "以后讲解时不要用公式，多用直觉和例子")
        post_turn(client, sid, "我知道导数，但不理解反向传播每层梯度怎样连接。")

        response = client.post(f"/api/v1/sessions/{sid}/forks", json={}, headers={"x-user-id": "bf_user"})
        off_sid = next(f["sessionId"] for f in response.json()["data"]["forks"] if f["memoryMode"] == "off")

        # 分支内的消息与 Brief 快照都不得出现偏好文本
        contents = [m["content"] for m in _messages(off_sid)]
        assert not any("不要用公式" in c for c in contents)
        brief = _brief(client, off_sid)
        blob = json.dumps(brief, ensure_ascii=False)
        assert "不要用公式" not in blob


# ---------- 问题 3：寒暄消息不套教学模板 ----------


def test_greeting_marked_as_social_not_teaching():
    """回归：寒暄消息不得套用默认 task_scope='直觉解释'（否则模型脑补
    教学偏好如'避免公式'，思考链出现与记忆无关的偏好内容）。"""
    from app.services.gate import run_gate

    for greeting in ("你好", "您好", "谢谢", "hello", "在吗", "Hi!"):
        result = run_gate(greeting, clarify_streak=0)
        assert result.decision == "READY"
        assert result.task.task_scope == "寒暄与开场"
        assert result.focus == "寒暄与开场"


def test_teaching_question_still_uses_teaching_scope():
    """教学请求不受寒暄标记影响。"""
    from app.services.gate import run_gate

    result = run_gate("讲讲梯度下降", clarify_streak=0)
    assert result.decision == "NEEDS_CLARIFICATION"
    result2 = run_gate("请解释为什么反向传播会梯度消失", clarify_streak=0)
    assert result2.task.task_scope != "寒暄与开场"
    assert result2.decision == "READY"


def test_confused_statement_triggers_clarification():
    """回归：'我不懂 X' 即使概念词很长（flashattention）也应澄清卡点，
    不得因字符数阈值放行全量讲解。"""
    from app.services.gate import run_gate

    # 长英文概念词：字符数超过旧阈值仍须澄清
    assert run_gate("我不懂flashattention", clarify_streak=0).decision == "NEEDS_CLARIFICATION"
    # 短概念词原有行为保持
    assert run_gate("我不懂rope", clarify_streak=0).decision == "NEEDS_CLARIFICATION"
    # 中文概念同样触发
    assert run_gate("我不懂反向传播", clarify_streak=0).decision == "NEEDS_CLARIFICATION"
    # 已有具体卡点（为什么）不再追问
    r = run_gate("我不懂flashattention为什么比attention快", clarify_streak=0)
    assert r.decision != "NEEDS_CLARIFICATION"
    # 连续两轮澄清后不追问（走显式假设）
    assert run_gate("我不懂flashattention", clarify_streak=2).decision != "NEEDS_CLARIFICATION"


# ---------- 问题 4：thinking 吃满预算的续写兜底 ----------


def test_thinking_exhausts_budget_then_continuation_produces_body(monkeypatch):
    """回归：thinking 吃满 max_tokens 被截断且正文为空时，续写必须关闭思考
    并产出正文；否则用户看到 60s+ 的空白回复（实测场景）。"""
    import asyncio
    from types import SimpleNamespace

    from app.schemas import ResolvedTask
    from app.services import coach
    from app.services.coach import CoachMeta

    monkeypatch.setattr(
        coach,
        "get_settings",
        lambda: SimpleNamespace(
            llm_provider="deepseek",
            deepseek_api_key="test-key",
            effective_deepseek_key="test-key",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-v4-flash",
            deepseek_thinking="enabled",
            deepseek_reasoning_effort="medium",
            llm_max_tokens=100,
            llm_timeout=0.1,
            llm_max_continuations=2,
        ),
    )
    calls = {"n": 0}

    async def fake_streamer(system, user, meta, *, client=None):
        calls["n"] += 1
        if calls["n"] == 1:
            meta.truncated = True
            meta.thinking_ttft_ms = 500
            meta.ttft_ms = 1000
            yield ("thinking", "这一段思考非常长，把预算全部吃光了……")
            return
        # 第二次调用：正文为空续写时必须关闭思考
        assert getattr(meta, "suppress_thinking", False), "正文为空续写时必须关闭思考"
        assert "不要再思考" in user, "续写 prompt 必须要求直接给出正文"
        yield ("content", "这是续写出来的完整正文。")

    monkeypatch.setattr(coach, "_stream_deepseek", fake_streamer)
    task = ResolvedTask(
        goal="目标", domain="machine_learning", concept="flashattention",
        proposition="我不懂flashattention", task_scope="机制分析",
    )

    async def collect():
        meta = CoachMeta()
        chunks = []
        async for kind, delta in coach.stream_explanation(
            system="sys", user="user", task=task, applied_labels=[], meta=meta
        ):
            chunks.append((kind, delta))
        return meta, chunks

    meta, chunks = asyncio.run(collect())
    contents = "".join(d for k, d in chunks if k == "content")
    assert contents == "这是续写出来的完整正文。"
    assert calls["n"] == 2
    assert meta.continuation_count == 1


# ---------- 单元级：apply_turn_delta 反馈短路 ----------


def test_apply_turn_delta_feedback_short_circuit(tmp_path):
    from app.schemas import ResolvedTask, SessionBrief
    from app.services.brief import apply_turn_delta

    brief = SessionBrief(goal="原会话目标")
    task = ResolvedTask(
        goal="以后讲梯度下降时不要用公式",
        domain="machine_learning",
        concept="梯度下降",
        proposition="以后讲梯度下降时不要用公式",
        task_scope="直觉解释",
    )
    updated = apply_turn_delta(
        brief,
        user_text="以后讲梯度下降时不要用公式",
        focus="偏好已更新",
        mode="explain",
        task=task,
        from_feedback=True,
    )
    assert updated.goal == "原会话目标"
    assert updated.exact_anchors == []
    assert updated.current_focus == ""
