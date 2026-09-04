from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx

from app.services import coach


def deepseek_settings(**overrides):
    values = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "test-key",
        "effective_deepseek_key": "test-key",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_thinking": "enabled",
        "deepseek_reasoning_effort": "high",
        "llm_max_tokens": 1600,
        "llm_timeout": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resolve_provider_supports_deepseek(monkeypatch):
    monkeypatch.setattr(coach, "get_settings", lambda: deepseek_settings())

    assert coach.resolve_provider() == ("deepseek", "deepseek-v4-flash")


def test_deepseek_stream_enables_high_thinking_and_forwards_reasoning(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        stream = (
            'data: {"choices":[{"delta":{"reasoning_content":"private reasoning"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"最终答案"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(coach, "get_settings", lambda: deepseek_settings())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def collect():
        meta = coach.CoachMeta()
        chunks = []
        try:
            async for chunk in coach._stream_deepseek("system", "user", meta, client=client):
                chunks.append(chunk)
        finally:
            await client.aclose()
        return meta, chunks

    meta, chunks = asyncio.run(collect())

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "high"
    assert chunks == [("thinking", "private reasoning"), ("content", "最终答案")]
    assert meta.ttft_ms >= 0
    assert meta.truncated is False


def test_deepseek_stream_marks_truncated_when_finish_reason_length(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            'data: {"choices":[{"delta":{"reasoning_content":"很长的思考"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"部分正文"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(coach, "get_settings", lambda: deepseek_settings())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def collect():
        meta = coach.CoachMeta()
        chunks = []
        try:
            async for chunk in coach._stream_deepseek("system", "user", meta, client=client):
                chunks.append(chunk)
        finally:
            await client.aclose()
        return meta, chunks

    meta, chunks = asyncio.run(collect())

    assert meta.truncated is True
    assert chunks == [("thinking", "很长的思考"), ("content", "部分正文")]
