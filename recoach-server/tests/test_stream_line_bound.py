"""流式响应单行缓冲上限的回归测试。

背景：TUI 的 core/coach.ts 一直有 MAX_PENDING_LINE，后端用的是 httpx 的
`aiter_lines()`——**没有**任何单行上限。read timeout 只限制两次数据之间的间隔，
不限制总量，所以一个持续发送不含换行数据的端点足以在超时窗口内把堆打爆。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from app.services import coach


class FakeStream:
    """最小可用的流式响应替身：只需要 aiter_text()。"""

    def __init__(self, chunks: list[str]):
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk


def _lines(chunks: list[str], max_chars: int | None = None):
    async def run():
        agen = (
            coach._iter_sse_lines(FakeStream(chunks))
            if max_chars is None
            else coach._iter_sse_lines(FakeStream(chunks), max_chars)
        )
        return [line async for line in agen]

    return asyncio.run(run())


def test_splits_lines_across_chunk_boundaries():
    """行被任意切分也必须完整还原（上游不会按行分块发送）。"""
    assert _lines(["data: {\"a\"", ":1}\n\ndata: [DONE]\n\n"]) == [
        'data: {"a":1}',
        "",
        "data: [DONE]",
        "",
    ]


def test_yields_trailing_line_without_newline():
    """末尾没有换行的残行不能丢。"""
    assert _lines(["data: [DONE]"]) == ["data: [DONE]"]


def test_raises_when_a_line_never_terminates():
    """持续不发换行的数据必须中止读取，而不是无限缓冲。"""
    try:
        _lines(["x" * 50, "y" * 50, "z" * 50], max_chars=100)
    except coach.ProviderStreamTooLong as exc:
        assert "100" in str(exc)
    else:
        raise AssertionError("超长未终止行应当抛 ProviderStreamTooLong")


def test_buffer_below_limit_is_not_treated_as_an_error():
    assert _lines(["x" * 40, "y" * 40], max_chars=100) == ["x" * 40 + "y" * 40]


def test_classified_as_stream_too_long():
    assert coach._classify_provider_failure(coach.ProviderStreamTooLong("x")) == "STREAM_TOO_LONG"


def test_real_http_stream_with_cjk_arrives_intact(monkeypatch):
    """走真实 httpx 流：中文不能被分块切断，且能正常解析出正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            'data: {"choices":[{"delta":{"content":"梯度"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"下降"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(
        coach,
        "get_settings",
        lambda: SimpleNamespace(
            llm_provider="deepseek",
            deepseek_api_key="k",
            effective_deepseek_key="k",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="m",
            deepseek_thinking="disabled",
            deepseek_reasoning_effort="low",
            llm_max_tokens=100,
            llm_timeout=10.0,
        ),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = coach.CoachMeta()

    async def run():
        out = []
        async for kind, delta in coach._stream_deepseek("sys", "user", meta, client=client):
            out.append((kind, delta))
        return out

    try:
        deltas = asyncio.run(run())
    finally:
        asyncio.run(client.aclose())

    assert "".join(d for _, d in deltas) == "梯度下降"
    assert all(kind == "content" for kind, _ in deltas)


def test_splits_on_bare_cr_terminators():
    """SSE 允许 \n、\r、\r\n 三种行终止符，必须都认。

    回归：_iter_sse_lines 初版只切 \n（httpx 的 aiter_lines 三种都认），
    上游若用裸 \r 分帧，整个响应会被当成一行，正文静默变空。
    """
    assert _lines(["data: a\r\rdata: b\r\r"]) == ["data: a", "", "data: b", ""]
    assert _lines(["data: a\r\n\r\n"]) == ["data: a", ""]
    assert _lines(["data: a", "\r", "\rdata: b\r\r"]) == ["data: a", "", "data: b", ""]


def test_bare_cr_framing_still_yields_content(monkeypatch):
    """端到端：裸 \r 分帧的上游必须仍能解出正文（修复前为空且不报错）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            'data: {"choices":[{"delta":{"content":"裸CR"}}]}\r\r'
            'data: {"choices":[{"delta":{"content":"可用"}}]}\r\r'
            "data: [DONE]\r\r"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(
        coach,
        "get_settings",
        lambda: SimpleNamespace(
            llm_provider="deepseek", deepseek_api_key="k", effective_deepseek_key="k",
            deepseek_base_url="https://api.deepseek.com", deepseek_model="m",
            deepseek_thinking="disabled", deepseek_reasoning_effort="low",
            llm_max_tokens=100, llm_timeout=10.0,
        ),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = coach.CoachMeta()

    async def run():
        return [d async for d in coach._stream_deepseek("sys", "user", meta, client=client)]

    try:
        deltas = asyncio.run(run())
    finally:
        asyncio.run(client.aclose())

    assert "".join(d for _, d in deltas) == "裸CR可用"
