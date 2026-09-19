"""用户输入处理的体验回归。

背景（2026-09-18 真实浏览器复现）：
用户在澄清轮看到选项后，很自然地**打字回「1」或「A」**而不是点击。
服务端原本把这类输入当成全新问题，于是又抛出一整篇泛泛的讲解
（实测打字回「1」产生 1438 字符，点选同项是 1184 字符且针对性完全不同）。

本文件锁死三件事：
1. 澄清轮的选项可以被「打字选择」识别（编号 / 字母 / 选项文案）
2. 没有说话内容的输入（纯编号/符号）不再触发全量讲解
3. 内部提示定界符不会出现在用户可见的回答里
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.services import compiler as compiler_service
from app.services import selection
from app.services.gate import run_gate, _is_non_informative


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "ux.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "ux.db"))
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _sse(text: str) -> list[dict]:
    events = []
    for raw in text.strip().split("\n\n"):
        data = [l[5:].lstrip() for l in raw.splitlines() if l.startswith("data:")]
        if data:
            try:
                events.append(json.loads("\n".join(data)))
            except json.JSONDecodeError:
                pass
    return events


def _post(client, session_id: str, content: str):
    resp = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
    )
    return resp, _sse(resp.text)


# --------------------------------------------------------- 选择符解析

OPTIONS = [
    {"label": "整体直觉", "followUp": "我想先建立整体直觉。用简单的例子，先不要公式。"},
    {"label": "机制细节", "followUp": "我已经知道大概是什么，但不理解内部具体怎么运作。"},
    {"label": "公式推导", "followUp": "我想看公式和推导，从定义开始。"},
]


class TestOptionSelection:
    @pytest.mark.parametrize(
        "typed,expected_index",
        [("1", 0), ("2", 1), ("3", 2), ("A", 0), ("b", 1), ("C", 2),
         ("1.", 0), ("A、", 0), ("第2个", 1), ("选2", 1), ("选择 3", 2)],
    )
    def test_typed_selector_resolves_to_option(self, typed, expected_index):
        """用户打的编号/字母必须映射到对应选项，而不是被当成新问题。"""
        got = selection.resolve_option_selection(typed, OPTIONS)
        assert got == OPTIONS[expected_index]["followUp"], f"{typed!r} 解析错误"

    def test_option_label_itself_resolves(self):
        assert selection.resolve_option_selection("整体直觉", OPTIONS) == OPTIONS[0]["followUp"]

    @pytest.mark.parametrize("typed", ["", "   ", "9", "Z", "讲讲反向传播", "什么是梯度下降"])
    def test_non_selector_is_not_hijacked(self, typed):
        """超出范围的编号、以及正常问题，都不能被当成选项选择。"""
        assert selection.resolve_option_selection(typed, OPTIONS) is None

    def test_no_options_means_no_resolution(self):
        """没有澄清轮时，「1」不应被解释成任何选项。"""
        assert selection.resolve_option_selection("1", []) is None

    def test_parse_options_requires_clarify_mode(self):
        assert selection.parse_options({"mode": "explain", "clarificationOptions": OPTIONS}) == []
        assert len(selection.parse_options({"mode": "clarify", "clarificationOptions": OPTIONS})) == 3


# --------------------------------------------------------- 非信息性输入

class TestNonInformativeInput:
    @pytest.mark.parametrize("text", ["1", "12", "A", "z", "???", "。。", "1.", "-"])
    def test_bare_selector_is_non_informative(self, text):
        assert _is_non_informative(text) is True, f"{text!r} 应判为非信息性"

    @pytest.mark.parametrize(
        "text",
        ["讲讲反向传播", "什么是梯度下降", "flashattention", "过拟合", "1+1为什么等于2"],
    )
    def test_real_questions_are_informative(self, text):
        assert _is_non_informative(text) is False, f"{text!r} 不应被判为非信息性"

    def test_bare_number_does_not_launch_explanation(self):
        """核心回归：发「1」不能再换来一整篇讲解。"""
        result = run_gate("1", clarify_streak=0)
        assert result.decision == "NEEDS_CLARIFICATION"
        assert result.options, "应给出可直接开始的入口"
        # 面向这种情况的措辞不该是"你卡在哪"——用户根本没说想问什么
        assert "编号" in result.question or "没看出" in result.question

    def test_bare_number_repeatedly_still_gated_then_assumes(self):
        """连续澄清达到上限后按显式假设继续，不会无限追问。"""
        first = run_gate("1", clarify_streak=0)
        assert first.decision == "NEEDS_CLARIFICATION"
        later = run_gate("1", clarify_streak=2)
        assert later.decision == "ANSWER_WITH_ASSUMPTION"


# --------------------------------------------------------- 定界符不泄漏到回答

class TestInternalMarkerStripping:
    def test_markers_are_stripped_from_output(self):
        raw = "<untrusted_memory> 里没保存住选项列表，所以我按最主流的一档来假设。"
        cleaned = compiler_service.strip_internal_markers(raw)
        assert "untrusted_memory" not in cleaned
        assert "没保存住选项列表" in cleaned, "只剥标记，正文内容要保留"

    def test_closing_tag_and_escaped_variants_also_stripped(self):
        for marker in ("</untrusted_memory>", "<\\untrusted_memory>", "<\\/untrusted_memory>"):
            assert "untrusted_memory" not in compiler_service.strip_internal_markers(
                f"前{marker}后"
            )

    def test_normal_text_untouched(self):
        text = "反向传播就是把损失的责任沿网络往前分摊。"
        assert compiler_service.strip_internal_markers(text) == text

    def test_system_prompt_forbids_mentioning_markers(self):
        """主防线是系统提示里明确要求不要提及标记。"""
        prompt = compiler_service.SYSTEM_PROMPT
        assert "绝不要在你的回答正文里提及" in prompt


# --------------------------------------------------------- 端到端

def test_typing_1_after_clarification_selects_first_option(client):
    """端到端：澄清轮之后打「1」，应与点选第一项等效。"""
    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]

    _, first_events = _post(client, session_id, "讲讲反向传播")
    presentation = first_events[-1]["presentation"]
    assert presentation["mode"] == "clarify", "该问法应触发澄清"
    options = presentation["clarificationOptions"]
    assert len(options) >= 1

    _, second_events = _post(client, session_id, "1")
    assert second_events[-1]["type"] == "turn.completed"
    assert second_events[-1]["presentation"]["mode"] == "explain", "应进入讲解而不是再次澄清"

    # 事件表留下"打字选择"的痕迹，便于排查
    row = db.query_one(
        "SELECT payload_json FROM events WHERE session_id=? AND kind='clarification_resolved'"
        " AND payload_json LIKE '%typed_selector%'",
        (session_id,),
    )
    assert row is not None, "应记录这是通过打字选择触发的"


def test_typed_selection_persists_raw_input_in_history(client):
    """对话历史要显示用户真正打的字，而不是被替换后的长文本。"""
    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]
    _post(client, session_id, "讲讲反向传播")
    _post(client, session_id, "1")

    rows = db.query(
        "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY created_at, rowid",
        (session_id,),
    )
    contents = [r["content"] for r in rows]
    assert "1" in contents, f"历史里应保留原始输入「1」，实际：{contents}"
