from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..config import get_settings
from ..services import brief as brief_service

router = APIRouter()


class CreateSessionRequest(BaseModel):
    locale: str = "zh-CN"


def current_user_id(request: Request) -> str:
    """P0 开发身份来自受信头或本地配置，绝不读取 JSON body 中的 user_id。"""
    header_user = (request.headers.get("x-user-id") or "").strip()
    return header_user or get_settings().dev_user


def owned_session(request: Request, session_id: str):
    """返回当前用户拥有的 Session；不存在或不属于当前用户时均返回 None。"""
    session = brief_service.get_session(session_id)
    if session is None or session[0] != current_user_id(request):
        return None
    return session


@router.post("/sessions")
def create_session(body: CreateSessionRequest, request: Request):
    user_id = current_user_id(request)
    session_id = brief_service.create_session(user_id, body.locale)
    return {"data": {"sessionId": session_id, "locale": body.locale}}
