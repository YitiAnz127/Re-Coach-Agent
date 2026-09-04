from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import coach
from app.schemas import ResolvedTask


def settings(**overrides):
    values = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "test-key",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_thinking": "disabled",
        "deepseek_reasoning_effort": "low",
        "llm_max_tokens": 100,
        "llm_timeout": 0.1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def collect_stream(monkeypatch, response: httpx.Response):
    monkeypatch.setattr(coach, "get_settings", lambda: settings())
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response))

    async def collect():
        meta = coach.CoachMeta()
        chunks = []
        try:
            async for chunk in coach._stream_deepseek("system", "user", meta, client=client):
                chunks.append(chunk)
        finally:
            await client.aclose()
        return meta, chunks

    return asyncio.run(collect())


def test_provider_429_is_injected_and_raises(monkeypatch):
    response = httpx.Response(429, text="rate limited")
    with pytest.raises(httpx.HTTPStatusError):
        collect_stream(monkeypatch, response)


def test_provider_5xx_is_injected_and_raises(monkeypatch):
    response = httpx.Response(503, text="upstream unavailable")
    with pytest.raises(httpx.HTTPStatusError):
        collect_stream(monkeypatch, response)


def test_partial_provider_stream_preserves_partial_output_and_raises(monkeypatch):
    response = httpx.Response(200, text=(
        'data: {"choices":[{"delta":{"content":"部分正文"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    ), headers={"content-type": "text/event-stream"})

    meta, chunks = collect_stream(monkeypatch, response)
    assert meta.truncated is False
    assert chunks == [("content", "部分正文")]


def test_provider_truncated_stream_is_detected(monkeypatch):
    response = httpx.Response(200, text=(
        'data: {"choices":[{"delta":{"content":"部分正文"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
        'data: [DONE]\n\n'
    ), headers={"content-type": "text/event-stream"})

    meta, chunks = collect_stream(monkeypatch, response)
    assert meta.truncated is True
    assert chunks == [("content", "部分正文")]


def test_stream_explanation_auto_continues_partial_truncation(monkeypatch):
    monkeypatch.setattr(coach, "resolve_provider", lambda: ("deepseek", "test-model"))
    monkeypatch.setattr(coach, "get_settings", lambda: settings(llm_max_continuations=1))
    calls = []

    async def fake_stream(_system, user, meta):
        calls.append(user)
        if len(calls) == 1:
            yield ("content", "第一段")
            meta.truncated = True
        else:
            yield ("content", "续写段")
            meta.truncated = False

    monkeypatch.setattr(coach, "_stream_deepseek", fake_stream)
    task = ResolvedTask(concept="反向传播", proposition="如何连接", task_scope="机制分析")

    async def collect():
        meta = coach.CoachMeta()
        chunks = []
        async for kind, text in coach.stream_explanation(
            system="system", user="user", task=task, applied_labels=[], meta=meta
        ):
            if kind == "content":
                chunks.append(text)
        return meta, chunks

    meta, chunks = asyncio.run(collect())
    assert chunks == ["第一段", "续写段"]
    assert meta.continuation_count == 1
    assert meta.truncated is False
    assert len(calls) == 2
