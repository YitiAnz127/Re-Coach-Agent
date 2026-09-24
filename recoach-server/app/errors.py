from __future__ import annotations

from dataclasses import dataclass

from .ids import new_id


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str
    retryable: bool


ERRORS = {
    "SESSION_NOT_FOUND": ErrorSpec("SESSION_NOT_FOUND", "该学习会话不存在或已过期。", False),
    "TURN_CONFLICT": ErrorSpec("TURN_CONFLICT", "相同回合内容不同，请重新发送。", False),
    "TURN_IN_PROGRESS": ErrorSpec("TURN_IN_PROGRESS", "该回合仍在处理中，请稍后重试。", True),
    "INTERNAL": ErrorSpec("INTERNAL", "讲解服务暂时不可用，请重试。", True),
    "CANCELLED": ErrorSpec("CANCELLED", "本轮已取消，可以重新发送。", True),
    "INVALID_REQUEST": ErrorSpec("INVALID_REQUEST", "请求内容不合法，请检查后重发。", False),
    "UNAUTHORIZED": ErrorSpec("UNAUTHORIZED", "缺少或无效的访问凭证。", False),
    "RATE_LIMITED": ErrorSpec("RATE_LIMITED", "请求过于频繁，请稍后重试。", True),
    "SERVICE_BUSY": ErrorSpec("SERVICE_BUSY", "当前讲解请求过多，请稍后重试。", True),
    "MODEL_UNAVAILABLE": ErrorSpec("MODEL_UNAVAILABLE", "模型服务当前不可用，请稍后重试。", True),
}

# 模型不可用时，按**粗粒度原因**给出可操作的提示。
# 只包含类别信息，绝不带出异常原文、URL、响应体或密钥。
PROVIDER_FAILURE_MESSAGES: dict[str, tuple[str, bool]] = {
    "AUTH": ("模型服务拒绝了访问凭证（密钥无效、已过期或无权限），请检查 API 密钥配置。", False),
    "QUOTA": ("模型服务额度不足或触发频率限制，请检查账户余额或降低请求频率。", True),
    "TIMEOUT": ("模型服务响应超时，请稍后重试。", True),
    "NETWORK": ("无法连接模型服务（网络或 DNS 问题），请检查网络与 Base URL 配置。", True),
    "PROVIDER_ERROR": ("模型服务端错误，请稍后重试。", True),
    "HTTP_ERROR": ("模型服务返回异常状态，请检查模型名称与接口地址配置。", False),
    # 上游流式响应里出现了超长未终止行：接口不是标准的 SSE，或对端在发垃圾数据。
    "STREAM_TOO_LONG": ("模型服务返回的流式响应格式异常，已中止本轮，请检查接口地址是否正确。", True),
    "ERROR": ("调用模型时出错，请稍后重试。", True),
}


def provider_failure_message(reason: str) -> tuple[str, bool]:
    """返回 (面向用户的安全提示, 是否值得重试)。"""
    return PROVIDER_FAILURE_MESSAGES.get(reason, PROVIDER_FAILURE_MESSAGES["ERROR"])


def error_payload(code: str, *, request_id: str | None = None, message: str | None = None, retryable: bool | None = None) -> dict[str, object]:
    spec = ERRORS.get(code, ERRORS["INTERNAL"])
    return {
        "code": spec.code,
        "message": message or spec.message,
        "retryable": spec.retryable if retryable is None else retryable,
        "requestId": request_id or new_id("req"),
    }


def sse_error(
    code: str,
    *,
    turn_id: str,
    request_id: str | None = None,
    message: str | None = None,
    retryable: bool | None = None,
) -> dict[str, object]:
    return {
        "type": "turn.error",
        "turnId": turn_id,
        **error_payload(code, request_id=request_id, message=message, retryable=retryable),
    }
