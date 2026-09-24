from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import USER_ID_RE, get_settings
from ..errors import error_payload
from ..services import brief as brief_service
from ..services import ratelimit

router = APIRouter()


class CreateSessionRequest(BaseModel):
    # locale 目前只是被接收并**回显**：create_session 并不落库这一项，
    # 回答语言由系统提示按"用户消息本身的语言"决定（见 compiler.SYSTEM_PROMPT）。
    # 长度上限仍然要保留——它会被放进响应体与日志，没有上限就是放大面。
    # 注意 Turn 的 locale 语义不同：它会进 canonical payload 并参与重试冲突判定
    # （见 routes/turns.py）。
    locale: str = Field(default="zh-CN", max_length=35)


class InvalidUserId(ValueError):
    """x-user-id 不满足字符集/长度约束。"""


class UserIdForbidden(InvalidUserId):
    """已开启单用户锁定（RECOACH_LOCKED_USER），且请求显式指定了另一个身份。

    继承 InvalidUserId 是刻意的：`owned_session` 等按"身份不可用 → 视作不存在"
    处理的路径不必逐个改捕获，对外仍保持"不区分无权限与不存在"。
    """


def current_user_id(request: Request) -> str:
    """解析调用方身份。

    信任边界：`x-user-id` 仅在请求已通过 `app.auth.AuthMiddleware` 之后才可信——
    令牌模式下调用方必须持有 `RECOACH_API_TOKEN`，开发模式下必须是本机回环客户端。
    绝不读取 JSON body 中的 user_id。

    开启 `RECOACH_LOCKED_USER` 后身份恒为该配置值，请求头不再有话语权。
    """
    settings = get_settings()
    header_user = (request.headers.get("x-user-id") or "").strip()
    locked = settings.locked_user
    if locked:
        if header_user and header_user != locked:
            # 显式冒充他人：拒绝而不是静默忽略。静默忽略会让冒充尝试在日志里
            # 完全不可见——而"能冒充谁"正是这个开关要关闭的面。
            raise UserIdForbidden(header_user)
        return locked
    if not header_user:
        return settings.dev_user
    if not USER_ID_RE.match(header_user):
        raise InvalidUserId(header_user)
    return header_user


def current_user_id_or_error(request: Request) -> tuple[str, JSONResponse | None]:
    """路由用包装：身份不可用时返回统一信封而不是抛 500。

    冒充他人按 401 处理——语义是"你的凭证不授权这个身份"，而不是"请求格式不对"。
    会话级路由仍按 404 收敛（见 owned_session），不向外区分有无权限。
    """
    request_id = getattr(request.state, "request_id", None)
    try:
        return current_user_id(request), None
    except UserIdForbidden:
        return "", JSONResponse(
            status_code=401,
            content={"error": error_payload("UNAUTHORIZED", request_id=request_id)},
        )
    except InvalidUserId:
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
