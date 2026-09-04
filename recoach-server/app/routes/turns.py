from __future__ import annotations

import json
import sqlite3
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import error_payload
from ..ids import new_id
from ..services import coach as coach_service
from ..services import orchestrator, turns as turn_store
from ..sse import encode, frame
from .sessions import current_user_id, owned_session

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class TurnMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: TurnMessage
    clientTurnId: str = Field(min_length=4, max_length=128)
    locale: str = "zh-CN"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_id("req")


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error_payload(code, request_id=_request_id(request))},
    )


def _sse_headers(request: Request) -> dict[str, str]:
    return {**SSE_HEADERS, "x-request-id": _request_id(request)}


async def _replay_turn(turn: dict) -> AsyncIterator[str]:
    """相同逻辑 Turn 的完成态重放 canonical 最终结果。"""
    presentation = json.loads(turn["presentation_json"])
    started: dict[str, Any] = {
        "type": "turn.started",
        "turnId": turn["id"],
        "mode": presentation["mode"],
        "focus": presentation["focus"],
        "plan": presentation["plan"],
    }
    yield frame(started)
    for chunk in coach_service._split_chunks(turn["response_text"]):
        yield frame({"type": "assistant.delta", "turnId": turn["id"], "delta": chunk})
    yield frame(
        {"type": "turn.completed", "turnId": turn["id"], "presentation": presentation}
    )


def _handle_duplicate_key(
    request: Request, session_id: str, client_turn_id: str, payload: dict
) -> StreamingResponse | JSONResponse | tuple[str, dict] | None:
    """同 clientTurnId 在插入竞态窗口内已存在时的 canonical 处理。

    返回 StreamingResponse（重放）/ JSONResponse（进行中）/ (turn_id, existing)
    （可原子重试）/ None（重查仍不存在，由调用方决定）。
    """
    existing = turn_store.find_by_client_key(session_id, client_turn_id)
    if existing is None:
        return None
    if existing["status"] == "completed" and existing["presentation_json"]:
        return StreamingResponse(
            _replay_turn(existing),
            media_type="text/event-stream",
            headers=_sse_headers(request),
        )
    if existing["status"] == "streaming":
        return _error(request, 409, "TURN_IN_PROGRESS")
    if not turn_store.restart_turn(existing["id"]):
        return _error(request, 409, "TURN_IN_PROGRESS")
    return existing["id"], existing


@router.post("/sessions/{session_id}/turns")
async def create_turn(session_id: str, body: TurnRequest, request: Request):
    session = owned_session(request, session_id)
    if session is None:
        return _error(request, 404, "SESSION_NOT_FOUND")
    user_id = current_user_id(request)
    payload = {
        "message": {"content": body.message.content},
        "locale": body.locale,
    }
    existing = turn_store.find_by_client_key(session_id, body.clientTurnId)
    if existing is not None:
        existing_payload = json.loads(existing["request_json"])
        # 2026-08-25 起普通 Turn 不再接受客户端切换记忆模式。兼容升级前
        # 已保存的 canonical payload：重放比较时忽略旧 memoryMode 字段。
        existing_payload.pop("memoryMode", None)
        same_payload = existing_payload == payload
        if not same_payload:
            return _error(request, 409, "TURN_CONFLICT")
        if existing["status"] == "completed" and existing["presentation_json"]:
            return StreamingResponse(
                _replay_turn(existing),
                media_type="text/event-stream",
                headers=_sse_headers(request),
            )
        if existing["status"] == "streaming":
            return _error(request, 409, "TURN_IN_PROGRESS")
        turn_id = existing["id"]
        if not turn_store.restart_turn(turn_id):
            # claim 失败：另一进程已抢先恢复并开始执行，本请求按进行中处理。
            return _error(request, 409, "TURN_IN_PROGRESS")
    else:
        try:
            turn_id = turn_store.create_turn(session_id, user_id, body.clientTurnId, payload)
        except sqlite3.IntegrityError:
            # 并发窗口：另一个同 key 请求刚插入。重查当前状态按 canonical 处理，
            # 避免 INSERT 竞态直接 500（B8/B9）。
            outcome = _handle_duplicate_key(request, session_id, body.clientTurnId, payload)
            if isinstance(outcome, StreamingResponse) or isinstance(outcome, JSONResponse):
                return outcome
            if outcome is None:
                raise  # 重查也不存在：极端情况，保留原始 IntegrityError 语义
            turn_id, _ = outcome

    stream = orchestrator.run_turn(
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        user_text=body.message.content,
        request_id=_request_id(request),
    )
    return StreamingResponse(
        encode(stream), media_type="text/event-stream", headers=_sse_headers(request)
    )
