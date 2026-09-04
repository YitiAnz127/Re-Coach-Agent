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
}


def error_payload(code: str, *, request_id: str | None = None, message: str | None = None, retryable: bool | None = None) -> dict[str, object]:
    spec = ERRORS.get(code, ERRORS["INTERNAL"])
    return {
        "code": spec.code,
        "message": message or spec.message,
        "retryable": spec.retryable if retryable is None else retryable,
        "requestId": request_id or new_id("req"),
    }


def sse_error(code: str, *, turn_id: str, request_id: str | None = None) -> dict[str, object]:
    return {"type": "turn.error", "turnId": turn_id, **error_payload(code, request_id=request_id)}
