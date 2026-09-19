import pytest
from app import db
from app.services import brief, memory, compiler
from app.schemas import ResolvedTask
from tests.test_v11_contract import create_client, create_session, post_turn, parse_sse

def test_fork_preserves_memory_and_concept_content(tmp_path, monkeypatch):
    with create_client(tmp_path) as client:
        source = create_session(client)
        original = memory.write_memory("v11_user", type_="interaction_rule", rule="以后先给公式", source_event_id="original")
        task = ResolvedTask(concept="反向传播", domain="deep_learning", proposition="链式法则")
        brief.record_concept_state("v11_user", task, state="unresolved", evidence_kind="user_explicit_unresolved", source_event_id="state_before")
        response = client.post(f"/api/v1/sessions/{source}/forks", json={}, headers={"x-user-id": "v11_user"})
        fork = next(f for f in response.json()["data"]["forks"] if f["memoryMode"] == "on")
        memory.forget_memories("v11_user", "公式", "forgotten")
        brief.record_concept_state("v11_user", task, state="self_reported_understood", evidence_kind="user_self_report", source_event_id="state_after")
        captured = []
        original_compile = compiler.compile_context
        def capture(**kwargs):
            captured.extend(kwargs["concept_states"])
            return original_compile(**kwargs)
        monkeypatch.setattr(compiler, "compile_context", capture)
        result = post_turn(client, fork["sessionId"], "我想看反向传播的公式推导，从定义开始")
        presentation = parse_sse(result.text)[-1]["presentation"]
        assert original.id in [m["memoryId"] for m in presentation["personalization"]]
        assert [c["state"] for c in captured] == ["unresolved"]

def test_recent_messages_preserve_equal_timestamp_order(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "order.db"))
    monkeypatch.setattr(brief, "now_iso", lambda: "2026-01-01T00:00:00Z")
    session = brief.create_session("user", "zh-CN")
    brief.save_message(session, "t", "user", "question")
    brief.save_message(session, "t", "assistant", "answer")
    assert [m["content"] for m in brief.recent_messages(session)] == ["question", "answer"]
