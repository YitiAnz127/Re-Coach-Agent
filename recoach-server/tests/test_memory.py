from __future__ import annotations

import os

os.environ.setdefault("RECOACH_DEV_USER", "test_user")

import pytest

from app import db
from app.schemas import ResolvedTask, SessionBrief
from app.services import compiler as compiler_service
from app.services import gate as gate_service
from app.services import memory as memory_service


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.reset_for_tests(str(tmp_path / "unit.db"))
    yield


class TestGate:
    def test_broad_question_needs_clarification(self):
        result = gate_service.run_gate("讲讲反向传播", clarify_streak=0)
        assert result.decision == "NEEDS_CLARIFICATION"
        assert len(result.options) >= 3
        assert result.question  # 每轮只问一个问题

    def test_specific_question_is_ready(self):
        result = gate_service.run_gate(
            "我知道导数和梯度下降，但不理解反向传播每层的梯度怎样通过链式法则连起来。",
            clarify_streak=0,
        )
        assert result.decision == "READY"
        assert result.task.concept == "反向传播"
        assert result.task.domain == "deep_learning"

    def test_max_two_clarification_rounds_then_assumption(self):
        result = gate_service.run_gate("讲讲反向传播", clarify_streak=2)
        assert result.decision == "ANSWER_WITH_ASSUMPTION"
        assert result.assumption

    def test_output_preferences_detected(self):
        result = gate_service.run_gate(
            "我学过导数，请解释反向传播，先给公式再讲直觉。", clarify_streak=0
        )
        assert any("公式" in p for p in result.task.output_preference)


class TestMemory:
    def test_write_and_retrieve_scoped(self):
        m = memory_service.write_memory(
            "u1",
            type_="explanation_preference",
            rule="讲解深度学习概念时先用数值例子",
            domain="deep_learning",
            source_event_id="evt_test",
        )
        task = ResolvedTask(
            goal="理解反向传播",
            domain="deep_learning",
            concept="反向传播",
            proposition="梯度如何逐层传递",
        )
        result = memory_service.retrieve("u1", task)
        assert m.id in [x.id for x in result.selected]

    def test_unrelated_concept_memory_not_selected(self):
        memory_service.write_memory(
            "u1",
            type_="explanation_preference",
            rule="讲聚类时多用图示",
            domain="machine_learning",
            concept_scope="kmeans",
            source_event_id="evt_test",
        )
        task = ResolvedTask(
            goal="理解反向传播", domain="deep_learning",
            concept="反向传播", proposition="梯度如何逐层传递",
        )
        result = memory_service.retrieve("u1", task)
        assert result.selected == []

    def test_rewrite_archives_old_with_chain(self):
        old = memory_service.write_memory(
            "u1", type_="explanation_preference", rule="先给公式",
            domain="deep_learning", concept_scope="反向传播", source_event_id="evt_1",
        )
        new = memory_service.write_memory(
            "u1", type_="explanation_preference", rule="先给公式再补充直觉",
            domain="deep_learning", concept_scope="反向传播", source_event_id="evt_2",
        )
        old_after = memory_service.get_memory(old.id)
        assert old_after is not None and old_after.status == "archived"
        assert old_after.superseded_by == new.id

    def test_forget_archives_not_deletes(self):
        m = memory_service.write_memory(
            "u1", type_="explanation_preference", rule="先给公式", source_event_id="evt_1"
        )
        forgotten = memory_service.forget_memories("u1", "公式", "evt_2")
        assert m.id in forgotten
        after = memory_service.get_memory(m.id)
        assert after is not None and after.status == "forgotten"  # 物理行仍在，供审计

    def test_feedback_classification(self):
        f = memory_service.classify_feedback("以后讲解时请先用数值例子。")
        assert f.kind == "write_longterm"
        f = memory_service.classify_feedback("这次先不要公式。")
        assert f.kind == "session_only"
        f = memory_service.classify_feedback("忘记之前关于公式的偏好。")
        assert f.kind == "forget" and "公式" in f.keyword
        f = memory_service.classify_feedback("忘记之前关于先给公式的偏好。")
        assert f.kind == "forget" and f.keyword == "先给公式"
        f = memory_service.classify_feedback("反向传播是什么？")
        assert f.kind == "none"



def test_same_domain_unrelated_concept_is_not_selected():
    memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="讲 kmeans 时先画簇中心变化",
        domain="machine_learning",
        concept_scope="kmeans",
        source_event_id="evt_kmeans",
    )
    task = ResolvedTask(
        goal="理解线性回归",
        domain="machine_learning",
        concept="线性回归",
        proposition="最小二乘为什么有效",
    )

    assert memory_service.retrieve("u1", task).selected == []


def test_global_and_domain_specific_rules_can_coexist():
    global_rule = memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="先用数值例子再给公式",
        source_event_id="evt_global",
    )
    domain_rule = memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="先用数值例子再给公式",
        domain="deep_learning",
        source_event_id="evt_domain",
    )

    assert memory_service.get_memory(global_rule.id).status == "active"
    assert memory_service.get_memory(domain_rule.id).status == "active"


def test_current_request_temporarily_overrides_conflicting_formula_memory():
    memory = memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="讲解时先给公式",
        domain="deep_learning",
        concept_scope="反向传播",
        source_event_id="evt_formula",
    )
    task = ResolvedTask(
        goal="理解反向传播",
        domain="deep_learning",
        concept="反向传播",
        proposition="梯度如何传递",
        output_preference=["避免公式，先讲直觉"],
    )

    context = compiler_service.compile_context(
        task=task,
        brief=SessionBrief(),
        selected_memories=[memory],
        concept_states=[],
        recent_messages=[],
    )

    assert context.applied == []
    assert context.trace["overridden"] == [{"memoryId": memory.id, "by": "current_request"}]


def test_forget_without_keyword_is_noop():
    memory_service.write_memory(
        "u1",
        type_="interaction_rule",
        rule="以后先给公式",
        source_event_id="evt_formula",
    )
    memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="讲解时先给数值例子",
        source_event_id="evt_example",
    )

    forgotten = memory_service.forget_memories("u1", "", "evt_forget")

    assert forgotten == []
    assert all(m.status == "active" for m in memory_service.list_memories("u1"))


def test_chinese_memory_query_gets_lexical_candidate():
    memory = memory_service.write_memory(
        "u1",
        type_="explanation_preference",
        rule="讲解时先用数值例子再讲公式",
        source_event_id="evt_chinese",
    )

    scores = memory_service._fts_candidates("u1", "请用数值例子解释反向传播")

    assert scores.get(memory.id, 0.0) > 0.0
