from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..errors import error_payload
from ..services import brief as brief_service
from ..services import ratelimit

router = APIRouter()

# 身份标识会进入 SQLite 并参与作用域判定，限制字符集避免异常值与日志注入。
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@:-]{1,128}$")


class CreateSessionRequest(BaseModel):
    # locale 会被持久化进 sessions/turns 行，必须有长度上限，否则是存储放大面。
    locale: str = Field(default="zh-CN", max_length=35)


class InvalidUserId(ValueError):
    """x-user-id 不满足字符集/长度约束。"""


def current_user_id(request: Request) -> str:
    """解析调用方身份。

    信任边界：`x-user-id` 仅在请求已通过 `app.auth.AuthMiddleware` 之后才可信——
    令牌模式下调用方必须持有 `RECOACH_API_TOKEN`，开发模式下必须是本机回环客户端。
    绝不读取 JSON body 中的 user_id。
    """
    header_user = (request.headers.get("x-user-id") or "").strip()
    if not header_user:
        return get_settings().dev_user
    if not _USER_ID_RE.match(header_user):
        raise InvalidUserId(header_user)
    return header_user


def current_user_id_or_error(request: Request) -> tuple[str, JSONResponse | None]:
    """路由用包装：身份非法时返回统一的 400 信封而不是抛 500。"""
    try:
        return current_user_id(request), None
    except InvalidUserId:
        request_id = getattr(request.state, "request_id", None)
        return "", JSONResponse(
            status_code=400,
            content={"error": error_payload("INVALID_REQUEST", request_id=request_id)},
        )


def enforce_rate_limit(request: Request, user_id: str) -> JSONResponse | None:
    """对计费型端点做每身份限流；超限返回 429，否则返回 None。"""
    settings = get_settings()
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return None
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = ratelimit.check(f"llm:{user_id}:{client}", limit=limit)
    if allowed:
        return None
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=429,
        content={"error": error_payload("RATE_LIMITED", request_id=request_id)},
        headers={"retry-after": str(max(1, int(retry_after + 0.999)))},
    )


def owned_session(request: Request, session_id: str):
    """返回当前用户拥有的 Session；不存在、身份非法或不属于当前用户时均返回 None。

    身份非法时统一按"不存在"处理，既避免 500 也不向外区分"无权限"与"不存在"。
    """
    try:
        user_id = current_user_id(request)
    except InvalidUserId:
        return None
    session = brief_service.get_session(session_id)
    if session is None or session[0] != user_id:
        return None
    return session


@router.post("/sessions")
def create_session(body: CreateSessionRequest, request: Request):
    user_id, error = current_user_id_or_error(request)
    if error is not None:
        return error
    session_id = brief_service.create_session(user_id, body.locale)
    return {"data": {"sessionId": session_id, "locale": body.locale}}
