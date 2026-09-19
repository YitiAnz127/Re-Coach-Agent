from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ..config import get_settings
from ..schemas import ResolvedTask


class ProviderFailure(RuntimeError):
    """真实 provider 失败，且调用方选择了 fail-fast（不降级为模板）。

    只携带粗粒度原因类别，异常原文不外传——原文可能含请求 URL、响应体片段。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class CoachMeta:
    provider: str = "template"
    model: str = "template"
    # 真实 provider 失败并降级成模板时，记录**原本想用的** provider/model，
    # 否则降级后 provider 被改成 "template"，外部再也看不出本该怎么走。
    requested_provider: str = ""
    requested_model: str = ""
    ttft_ms: int = 0
    thinking_ttft_ms: int = 0
    content_ttft_ms: int = 0  # 新增：正文首字时间
    fallback: bool = False
    # 降级的粗粒度原因，只暴露异常**类别**而非异常文本，避免带出 URL / 响应体等细节
    fallback_reason: str = ""
    truncated: bool = False
    continuation_count: int = 0
    thinking_tokens: int = 0
    actual_thinking_chars: int = 0  # 新增：实际thinking字符数


def resolve_provider() -> tuple[str, str]:
    settings = get_settings()
    if settings.llm_provider == "openai_compatible" and settings.llm_api_key and settings.llm_base_url and settings.llm_model:
        return "openai_compatible", settings.llm_model
    if settings.llm_provider == "deepseek" and settings.effective_deepseek_key and settings.deepseek_base_url and settings.deepseek_model:
        return "deepseek", settings.deepseek_model
    if settings.llm_provider == "anthropic" and settings.effective_anthropic_key and settings.anthropic_model:
        return "anthropic", settings.anthropic_model
    return "template", "template"


def _estimate_tokens_chinese(text: str) -> int:
    """更准确的中文token估算（仅用于统计）"""
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.2 + other_chars / 4)


async def _stream_chat_completions(
    system: str,
    user: str,
    meta: CoachMeta,
    *,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout: float,
    extra_payload: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
    thinking_char_limit: int | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """
    OpenAI兼容的流式调用，支持thinking控制
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if extra_payload:
        payload.update(extra_payload)
    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.perf_counter()
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
    
    thinking_char_count = 0
    thinking_stopped = False
    content_started = False
    
    try:
        async with request_client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason") == "length":
                    meta.truncated = True
                delta = choice.get("delta", {})
                
                # 处理thinking
                reasoning = delta.get("reasoning_content")
                if reasoning and not thinking_stopped:
                    if meta.thinking_ttft_ms == 0:
                        meta.thinking_ttft_ms = int((time.perf_counter() - started) * 1000)
                        if meta.ttft_ms == 0:
                            meta.ttft_ms = meta.thinking_ttft_ms
                    
                    thinking_char_count += len(reasoning)
                    meta.actual_thinking_chars = thinking_char_count
                    
                    # 字符限制检查
                    if thinking_char_limit and thinking_char_count >= thinking_char_limit:
                        if not thinking_stopped:
                            thinking_stopped = True
                            yield ("thinking", "\n[思考已达字符限制，继续生成回答...]")
                        continue
                    
                    yield ("thinking", reasoning)
                
                # 处理正文内容
                content = delta.get("content")
                if content:
                    if not content_started:
                        content_started = True
                        meta.content_ttft_ms = int((time.perf_counter() - started) * 1000)
                        if meta.ttft_ms == 0:
                            meta.ttft_ms = meta.content_ttft_ms
                    yield ("content", content)
                    
    finally:
        if meta.actual_thinking_chars > 0:
            meta.thinking_tokens = _estimate_tokens_chinese(str(meta.actual_thinking_chars))
        if owns_client:
            await request_client.aclose()


async def _stream_openai_compatible(system: str, user: str, meta: CoachMeta, *, client: httpx.AsyncClient | None = None) -> AsyncIterator[tuple[str, str]]:
    settings = get_settings()
    async for delta in _stream_chat_completions(
        system, user, meta, base_url=settings.llm_base_url, api_key=settings.llm_api_key,
        model=settings.llm_model, max_tokens=settings.llm_max_tokens, timeout=settings.llm_timeout, client=client
    ):
        yield delta


async def _stream_deepseek(system: str, user: str, meta: CoachMeta, *, client: httpx.AsyncClient | None = None) -> AsyncIterator[tuple[str, str]]:
    """DeepSeek流式处理，支持thinking字符限制"""
    settings = get_settings()
    
    # 续写时完全关闭thinking
    if getattr(meta, "suppress_thinking", False):
        extra_payload: dict[str, Any] = {"thinking": {"type": "disabled"}}
        thinking_char_limit = None
    else:
        extra_payload: dict[str, Any] = {"thinking": {"type": settings.deepseek_thinking}}
        if settings.deepseek_thinking == "enabled":
            extra_payload["reasoning_effort"] = settings.deepseek_reasoning_effort
            
            # 根据reasoning_effort动态设置字符限制
            effort_limits = {
                "low": 3000,
                "medium": 6000,
                "high": 9000
            }
            thinking_char_limit = effort_limits.get(settings.deepseek_reasoning_effort, 6000)
        else:
            thinking_char_limit = None
    
    api_key = getattr(settings, "effective_deepseek_key", getattr(settings, "deepseek_api_key", ""))
    async for delta in _stream_chat_completions(
        system, user, meta, 
        base_url=settings.deepseek_base_url, 
        api_key=api_key,
        model=settings.deepseek_model, 
        max_tokens=settings.llm_max_tokens, 
        timeout=settings.llm_timeout,
        extra_payload=extra_payload, 
        client=client,
        thinking_char_limit=thinking_char_limit,
    ):
        yield delta


async def _stream_anthropic(system: str, user: str, meta: CoachMeta) -> AsyncIterator[tuple[str, str]]:
    """Anthropic流式处理"""
    from anthropic import AsyncAnthropic

    settings = get_settings()
    api_key = getattr(settings, "effective_anthropic_key", getattr(settings, "anthropic_api_key", ""))
    client = AsyncAnthropic(api_key=api_key)
    started = time.perf_counter()
    async with client.messages.stream(
        model=settings.anthropic_model, max_tokens=settings.llm_max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        async for text in stream.text_stream:
            if meta.ttft_ms == 0:
                meta.ttft_ms = int((time.perf_counter() - started) * 1000)
            yield ("content", text)
        final = await stream.get_final_message()
        if final.stop_reason == "max_tokens":
            meta.truncated = True

_DEPTH_LABEL = {
    "L0": "只建立感觉", "L1": "直觉优先", "L2": "直觉加少量机制",
    "L3": "机制加数值", "L4": "公式推导", "L5": "研究级细节",
}


def _template_text(task: ResolvedTask, applied_labels: list[str]) -> str:
    concept = task.concept or "这个概念"
    depth = task.desired_depth if task.desired_depth != "auto" else "L2"
    preference_note = f"\n\n已按你的稳定偏好调整：{'；'.join(applied_labels)}。" if applied_labels else ""
    if task.output_preference:
        preference_note += f"\n本轮要求：{'；'.join(task.output_preference)}。"
    if depth == "L0":
        body = "先只建立一个感觉：它把一个原本难以直接处理的问题，转换成更容易观察的一步。先记住输入、变化和结果，不展开公式。"
    elif depth == "L1":
        body = "先用一个最小情境建立直觉，再用一句话说明它为什么有效；暂时不展开完整公式和代码。"
    elif depth == "L4":
        body = "先写清定义和变量，再说明公式每一项的含义，最后把推导连接回直觉。需要时使用 LaTeX 公式。"
    elif depth == "L5":
        body = "先给出核心机制，再讨论假设、边界、复杂度和与相邻方法的差异；公式只在能支撑结论时使用。"
    else:
        body = "先说它解决的问题，再连接已知前置；随后用最小直觉、机制步骤和边界把它落地。"
    return (
        f"下面按「{concept} · {task.task_scope}」来讲，局部深度 {depth}（{_DEPTH_LABEL.get(depth, '')}）。\n\n"
        f"{body}\n\n"
        "当前由内置模板回答：未配置外部模型。配置 RECOACH_LLM_PROVIDER 后，这里将替换为真实讲解，结构与个性化行为保持不变。"
        f"{preference_note}"
    )


def verify_output(task: ResolvedTask, text: str) -> dict[str, Any]:
    checks: list[str] = []
    violations: list[str] = []
    has_code = "```" in text
    has_formula = bool(re.search(r"\$[^$]+\$|\\frac|\\sum|\\partial", text))
    if task.desired_depth in ("L0", "L1"):
        checks.append("低深度不主动展开公式和代码")
        if has_formula:
            violations.append("low_depth_formula")
        if has_code:
            violations.append("low_depth_code")
    for pref in task.output_preference:
        if "避免公式" in pref:
            checks.append("遵守本轮避免公式")
            if has_formula:
                violations.append("formula_forbidden")
        if "不要代码" in pref:
            checks.append("遵守本轮不要代码")
            if has_code:
                violations.append("code_forbidden")
    if not checks:
        return {"status": "unknown", "checks": [], "violations": []}
    return {"status": "passed" if not violations else "partial", "checks": checks, "violations": violations}


async def stream_explanation(*, system: str, user: str, task: ResolvedTask, applied_labels: list[str], meta: CoachMeta) -> AsyncIterator[tuple[str, str]]:
    """主 Coach 流式输出；检测截断后最多有限次数续写。"""
    provider, model = resolve_provider()
    meta.provider, meta.model = provider, model
    meta.requested_provider, meta.requested_model = provider, model
    if provider == "template":
        meta.ttft_ms = 1
        for chunk in _split_chunks(_template_text(task, applied_labels)):
            yield ("content", chunk)
        return

    if provider == "openai_compatible":
        streamer = _stream_openai_compatible
    elif provider == "deepseek":
        streamer = _stream_deepseek
    else:
        streamer = _stream_anthropic

    max_continuations = max(0, min(2, int(getattr(get_settings(), "llm_max_continuations", 2))))
    produced_any = False
    accumulated = ""
    prompt = user
    try:
        while True:
            meta.truncated = False
            attempt_content = ""
            async for kind, delta in streamer(system, prompt, meta):
                produced_any = True
                if kind == "content":
                    attempt_content += delta
                    accumulated += delta
                yield (kind, delta)
            if not meta.truncated or meta.continuation_count >= max_continuations:
                break
            meta.continuation_count += 1
            # 正文为空被截断（thinking 吃满预算）：关闭思考续写，直接补正文。
            # 原逻辑 `not attempt_content` 直接 break，导致 high 思考模式下
            # 出现 60s+ 空白回复；thinking 有产出也算产出，必须续写兜底。
            meta.suppress_thinking = not attempt_content
            if attempt_content:
                prompt = (
                    f"{user}\n\n【已生成正文】\n{accumulated[-12000:]}\n\n"
                    "请只从正文中断处继续，禁止重复已经生成的内容，直接输出后续正文。"
                )
            else:
                prompt = (
                    f"{user}\n\n"
                    "上一轮思考过长被截断，正文尚未开始。请不要再思考，直接给出完整回答正文。"
                )
        if meta.truncated and meta.continuation_count < max_continuations and not produced_any:
            return
    except Exception as exc:
        # 已经吐出部分内容就如实报错；一个字都还没出，才考虑降级。
        if produced_any:
            raise
        reason = _classify_provider_failure(exc)
        if get_settings().llm_fail_fast:
            # 明确选择"失败即报错"：不降级，把问题直接暴露出来。
            raise ProviderFailure(reason) from exc
        meta.fallback = True
        meta.fallback_reason = reason
        meta.provider, meta.model = "template", "template"
        meta.ttft_ms = 1
        for chunk in _split_chunks(_template_text(task, applied_labels)):
            yield ("content", chunk)


def _classify_provider_failure(exc: BaseException) -> str:
    """把 provider 失败归到一个粗粒度类别，供 UI 提示与排查。

    刻意只返回类别字符串：异常原文可能含请求 URL、响应体片段甚至密钥（若
    调用方把 key 拼进了 URL），绝不能外流到客户端或事件表。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            return "AUTH"
        if status == 429:
            return "QUOTA"
        if status >= 500:
            return "PROVIDER_ERROR"
        return "HTTP_ERROR"
    if isinstance(exc, (httpx.TimeoutException,)):
        return "TIMEOUT"
    if isinstance(exc, httpx.TransportError):
        return "NETWORK"
    return "ERROR"


def _split_chunks(text: str) -> list[str]:
    return re.findall(r"[^。！？\n]+[。！？]?|\n+", text) or [text]
