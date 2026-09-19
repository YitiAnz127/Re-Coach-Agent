"""真实 provider 降级必须对客户端可见。

背景（2026-09-18 用真实密钥复现）：配置了真实 provider 后，如果密钥无效 /
额度不足 / 超时，`stream_explanation` 会在首字之前捕获异常并**静默改发模板文本**，
同时把 `meta.provider` 改成 "template"。这个信息原本只写进 `model_called` 事件表，
**不进 presentation**；前端侧栏读的是 `/api/v1/meta` 的**配置值**，于是
界面一边显示 "deepseek"、一边输出模板文本，用户完全无从察觉。

本测试锁死：降级必须出现在本轮 presentation.metrics 里，且带上可诊断的原因类别。
"""
from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.services import coach as coach_service
from app.services.coach import CoachMeta, _classify_provider_failure


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "deg.db"))
    monkeypatch.setenv("RECOACH_API_TOKEN", "")
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "deg.db"))
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _parse_sse(text: str) -> list[dict]:
    events = []
    for raw in text.strip().split("\n\n"):
        data = [l[5:].lstrip() for l in raw.splitlines() if l.startswith("data:")]
        if data:
            try:
                events.append(json.loads("\n".join(data)))
            except json.JSONDecodeError:
                pass
    return events


def _post_turn(client, content: str = "我知道导数，请解释反向传播的链式法则。"):
    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]
    resp = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": content},
            "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
    )
    events = _parse_sse(resp.text)
    presentation = next(
        (e["presentation"] for e in reversed(events) if e["type"] == "turn.completed"), None
    )
    return resp, events, presentation


# --------------------------------------------------------- 失败分类

class TestFailureClassification:
    @pytest.mark.parametrize(
        "status,expected",
        [(401, "AUTH"), (403, "AUTH"), (429, "QUOTA"), (500, "PROVIDER_ERROR"),
         (502, "PROVIDER_ERROR"), (400, "HTTP_ERROR"), (404, "HTTP_ERROR")],
    )
    def test_http_status_maps_to_reason(self, status, expected):
        request = httpx.Request("POST", "https://api.example.com/v1/chat")
        response = httpx.Response(status, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _classify_provider_failure(exc) == expected

    def test_timeout_and_network(self):
        request = httpx.Request("POST", "https://api.example.com/v1")
        assert _classify_provider_failure(httpx.ConnectTimeout("t")) == "TIMEOUT"
        assert _classify_provider_failure(httpx.ConnectError("c")) == "NETWORK"

    def test_unknown_exception_is_generic(self):
        assert _classify_provider_failure(ValueError("x")) == "ERROR"

    def test_reason_never_contains_exception_text(self):
        """原因必须只是类别，不能把异常原文（可能含 URL/响应体）带出去。"""
        request = httpx.Request("POST", "https://secret-host.example/v1?key=leak")
        response = httpx.Response(401, request=request, text="secret body")
        exc = httpx.HTTPStatusError(
            "Client error '401 Unauthorized' for url 'https://secret-host.example/v1?key=leak'",
            request=request,
            response=response,
        )
        reason = _classify_provider_failure(exc)
        assert reason == "AUTH"
        for leak in ("secret-host", "key=leak", "secret body", "401"):
            assert leak not in reason


# --------------------------------------------------------- 端到端披露

class TestDegradationDisclosure:
    def test_provider_failure_is_disclosed_to_client(self, client, monkeypatch):
        """核心回归：真实 provider 失败降级时，客户端必须能看出来。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)

        def _fail(*_args, **_kwargs):
            request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

        monkeypatch.setattr(coach_service, "_stream_deepseek", _fail)

        resp, events, presentation = _post_turn(client)
        assert resp.status_code == 200
        assert presentation is not None, "降级仍应正常完成这一轮"

        metrics = presentation["metrics"]
        assert metrics["fallback"] is True, "降级必须如实上报"
        assert metrics["fallbackReason"] == "AUTH", "原因应可诊断（密钥问题）"
        assert metrics["provider"] == "template", "实际使用的是模板"

    def test_normal_template_mode_is_not_reported_as_degradation(self, client):
        """未配置模型时是正常模式，不是降级——避免误报。"""
        _, _, presentation = _post_turn(client)
        metrics = presentation["metrics"]
        assert metrics["provider"] == "template"
        assert metrics["fallback"] is False
        assert metrics["fallbackReason"] == ""

    def test_partial_output_failure_raises_instead_of_silent_switch(self, client, monkeypatch):
        """已经吐出内容后再失败，必须报错而不是悄悄换成模板文本。

        否则用户会看到"前半段是模型写的、后半段是模板拼的"混合回答。
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)

        async def _partial_then_fail(system, prompt, meta):
            meta.provider, meta.model = "deepseek", "deepseek-chat"
            yield ("content", "这是模型已经产出的一部分内容。")
            raise RuntimeError("连接在流中途断开")

        monkeypatch.setattr(coach_service, "_stream_deepseek", _partial_then_fail)

        resp, events, presentation = _post_turn(client)
        types = [e["type"] for e in events]
        assert "turn.error" in types, "中途失败必须报错"
        body = "".join(e.get("delta", "") for e in events if e["type"] == "assistant.delta")
        assert "模板" not in body, "不得把模板文本混进已产出的正文"


# --------------------------------------------------------- 配置不一致的可诊断性

class TestConfigDiagnostics:
    def test_configured_key_with_template_provider_is_detectable(self, client, monkeypatch):
        """/meta 必须让"填了 key 但 provider 还是 template"这种情况可被发现。

        用户常见失误：只填了密钥，忘了改 RECOACH_LLM_PROVIDER。
        此时 provider=template 且 configured=False，界面能据此提示。
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)
        monkeypatch.setattr(settings, "llm_provider", "template")

        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["llm"]["provider"] == "template"
        assert meta["llm"]["configured"] is False

    def test_meta_reports_configured_when_provider_set(self, client, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)
        monkeypatch.setattr(settings, "llm_provider", "deepseek")

        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["llm"]["provider"] == "deepseek"
        assert meta["llm"]["configured"] is True


# --------------------------------------------------------- 事件表仍保留完整信息

def test_event_records_requested_provider_on_fallback(client, monkeypatch):
    """降级后 provider 会被改成 template，因此必须另行记录"原本想用谁"。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)

    def _fail(*_args, **_kwargs):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(coach_service, "_stream_deepseek", _fail)

    session_id = client.post("/api/v1/sessions", json={"locale": "zh-CN"}).json()["data"]["sessionId"]
    client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "message": {"content": "我知道导数，请解释反向传播的链式法则。"},
            "clientTurnId": f"c_{uuid.uuid4().hex[:12]}",
            "locale": "zh-CN",
        },
    )
    row = db.query_one(
        "SELECT payload_json FROM events WHERE session_id=? AND kind='model_called'",
        (session_id,),
    )
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["provider"] == "template"
    assert payload["fallback"] is True
    assert payload["fallbackReason"] == "AUTH"
    assert payload["requestedProvider"] == "deepseek"


def test_meta_exposes_key_presence_without_leaking_values(client, monkeypatch):
    """keysPresent 只暴露布尔，且不得带出任何密钥内容或片段。"""
    settings = get_settings()
    secret = "sk-" + "a" * 32
    monkeypatch.setattr(settings, "deepseek_api_key", secret)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "llm_provider", "template")

    resp = client.get("/api/v1/meta")
    raw = resp.text
    body = resp.json()["data"]["llm"]

    assert body["keysPresent"]["deepseek"] is True
    assert body["keysPresent"]["anthropic"] is False
    assert body["keysPresent"]["openaiCompatible"] is False
    # 绝不能把密钥本身或其片段写进响应
    assert secret not in raw
    assert "a" * 32 not in raw


def test_meta_keys_present_flags_common_misconfiguration(client, monkeypatch):
    """填了密钥但 provider 未切换 —— 这是最常见的失误，必须可从 /meta 判定。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "b" * 32)
    monkeypatch.setattr(settings, "llm_provider", "template")

    body = client.get("/api/v1/meta").json()["data"]["llm"]
    assert body["provider"] == "template"      # 实际没启用
    assert body["configured"] is False         # 与 configured 一致
    assert body["keysPresent"]["deepseek"] is True  # 但密钥确实填了 → 可提示


# --------------------------------------------------------- fail-fast 模式

class TestFailFast:
    """RECOACH_LLM_FAIL_FAST=true 时不降级，直接以 MODEL_UNAVAILABLE 结束本轮。"""

    def _setup(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)
        monkeypatch.setattr(settings, "llm_fail_fast", True)

        def _fail(*_a, **_k):
            request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

        monkeypatch.setattr(coach_service, "_stream_deepseek", _fail)

    def test_reports_model_unavailable_instead_of_template(self, client, monkeypatch):
        self._setup(monkeypatch)
        resp, events, presentation = _post_turn(client)

        assert resp.status_code == 200  # SSE 已建流，错误以事件形式下发
        errors = [e for e in events if e["type"] == "turn.error"]
        assert len(errors) == 1, "必须且只能有一个终止事件"
        assert errors[0]["code"] == "MODEL_UNAVAILABLE"
        assert presentation is None, "不得再有 turn.completed"

        body = "".join(e.get("delta", "") for e in events if e["type"] == "assistant.delta")
        assert body == "", "fail-fast 下不得下发模板文本"

    def test_message_is_actionable_and_retryable_flag_is_correct(self, client, monkeypatch):
        """密钥错误给"检查密钥"，且不应标记为可重试（重试无意义）。"""
        self._setup(monkeypatch)
        _, events, _ = _post_turn(client)
        err = next(e for e in events if e["type"] == "turn.error")

        assert "密钥" in err["message"]
        assert err["retryable"] is False

    def test_message_never_leaks_provider_internals(self, client, monkeypatch):
        """提示必须只含类别信息，不能带出异常原文 / URL / 响应体。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)
        monkeypatch.setattr(settings, "llm_fail_fast", True)

        def _fail(*_a, **_k):
            request = httpx.Request("POST", "https://secret-host.example/v1?key=leak")
            response = httpx.Response(401, request=request, text="secret body")
            raise httpx.HTTPStatusError(
                "401 for url 'https://secret-host.example/v1?key=leak'",
                request=request, response=response,
            )

        monkeypatch.setattr(coach_service, "_stream_deepseek", _fail)
        resp, events, _ = _post_turn(client)

        err = next(e for e in events if e["type"] == "turn.error")
        for leak in ("secret-host", "key=leak", "secret body", "401"):
            assert leak not in err["message"], f"提示泄露了内部信息：{leak}"
            assert leak not in resp.text

    def test_reason_mapping_covers_all_classes(self):
        from app.errors import PROVIDER_FAILURE_MESSAGES, provider_failure_message

        for reason in ("AUTH", "QUOTA", "TIMEOUT", "NETWORK",
                       "PROVIDER_ERROR", "HTTP_ERROR", "ERROR"):
            message, _retryable = provider_failure_message(reason)
            assert message and isinstance(message, str)
        # 未知原因也要有兜底，不能让用户拿到空提示
        message, retryable = provider_failure_message("SOMETHING_NEW")
        assert message == PROVIDER_FAILURE_MESSAGES["ERROR"][0]
        assert retryable is True

    def test_fail_fast_does_not_apply_after_partial_output(self, client, monkeypatch):
        """已产出内容后失败，两种模式都应报错（而不是中途换成模板）。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "0" * 32)
        monkeypatch.setattr(settings, "llm_fail_fast", False)  # 即便不是 fail-fast

        async def _partial_then_fail(system, prompt, meta):
            meta.provider, meta.model = "deepseek", "deepseek-chat"
            yield ("content", "已经产出的一段内容。")
            raise RuntimeError("流中断")

        monkeypatch.setattr(coach_service, "_stream_deepseek", _partial_then_fail)
        _, events, presentation = _post_turn(client)

        assert any(e["type"] == "turn.error" for e in events)
        assert presentation is None
