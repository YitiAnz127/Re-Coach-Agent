from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .. import db
from ..errors import error_payload
from ..ids import new_id
from ..services import brief as brief_service
from .sessions import (
    current_user_id_or_error,
    enforce_rate_limit,
    owned_session,
)

router = APIRouter()


class ForkRequest(BaseModel):
    """Fork 模式由服务端固定，客户端只决定是否创建本次对照。"""

    model_config = ConfigDict(extra="forbid")


@router.post("/sessions/{session_id}/forks")
def create_forks(session_id: str, body: ForkRequest, request: Request):
    session = owned_session(request, session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": error_payload("SESSION_NOT_FOUND")})
    user_id, identity_error = current_user_id_or_error(request)
    if identity_error is not None:
        return identity_error
    limited = enforce_rate_limit(request, user_id)
    if limited is not None:
        return limited
    group_id = new_id("ab")
    forks = []
    with db.tx() as conn:
        for mode in ("on", "off"):
            fork_session_id = brief_service.create_session_fork(
                session_id, user_id=user_id, fork_group_id=group_id, memory_mode=mode, conn=conn
            )
            forks.append({"forkGroupId": group_id, "sessionId": fork_session_id, "memoryMode": mode})
    return {"data": {"forkGroupId": group_id, "sourceSessionId": session_id, "forks": forks}}

