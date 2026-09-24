from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..config import get_settings
from ..errors import error_payload
from ..ids import new_id
from ..services import coach as coach_service
from ..services import brief as brief_service
from ..services import orchestrator, turn_gate, turns as turn_store
from ..services import teaching as teaching_service
from ..sse import encode, frame
from .sessions import (
    current_user_id_or_error,
    enforce_rate_limit,
    owned_session,
)

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
    # 同 CreateSessionRequest：locale 会落库，必须限长。
    locale: str = Field(default="zh-CN", max_length=35)


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["too_basic", "just_right", "too_fast"]


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
    existing_payload = json.loads(existing["request_json"])
    existing_payload.pop("memoryMode", None)
    if existing_payload != payload:
        return _error(request, 409, "TURN_CONFLICT")
    if existing["status"] == "completed" and existing["presentation_json"]:
        return StreamingResponse(
            _replay_turn(existing),
            media_type="text/event-stream",
            headers=_sse_headers(request),
        )
    # 已超过任何一轮合法时长的孤儿行不算"进行中"，放它落到下面的 claim，
    # 否则这个 clientTurnId 永远走不出来（见 turns.stale_streaming_cutoff）。
    if existing["status"] == "streaming" and not turn_store.is_stale_streaming(existing):
        return _error(request, 409, "TURN_IN_PROGRESS")
    if not turn_store.restart_turn(existing["id"]):
        return _error(request, 409, "TURN_IN_PROGRESS")
    return existing["id"], existing


@router.get("/sessions/{session_id}/turns")
def list_turns(session_id: str, request: Request):
    """恢复会话历史。

    没有这个接口时客户端刷新即丢失全部对话（sessionId 只存在内存里），
    用户中途刷新页面就得重新开始——对学习场景是不可接受的。
    归属校验与其它会话级接口一致：不存在与不属于当前用户都返回 404。
    """
    if owned_session(request, session_id) is None:
        return _error(request, 404, "SESSION_NOT_FOUND")
    return {
        "data": {
            "sessionId": session_id,
            "turns": turn_store.list_completed_turns(session_id),
        }
    }


@router.post("/sessions/{session_id}/turns/{turn_id}/calibration")
def calibrate_turn(session_id: str, turn_id: str, body: CalibrationRequest, request: Request):
    session = owned_session(request, session_id)
    if session is None:
        return _error(request, 404, "SESSION_NOT_FOUND")
    if not get_settings().memory_on or brief_service.get_session_fork(session_id) is not None:
        return _error(request, 422, "INVALID_REQUEST")
    row = db.query_one(
        """SELECT status, mode, presentation_json FROM turns
           WHERE id=? AND session_id=? AND user_id=?""",
        (turn_id, session_id, session[0]),
    )
    if row is None:
        return _error(request, 404, "SESSION_NOT_FOUND")
    if row["status"] != "completed" or row["mode"] != "explain" or not row["presentation_json"]:
        return _error(request, 422, "INVALID_REQUEST")
    start = json.loads(row["presentation_json"]).get("teachingStart")
    if not isinstance(start, dict) or not start.get("concept") or not start.get("domain"):
        return _error(request, 422, "INVALID_REQUEST")
    result = teaching_service.record_calibration(
        turn_id=turn_id,
        user_id=session[0],
        domain=start["domain"],
        concept=start["concept"],
        rating=body.rating,
        base_level=start["level"],
    )
    return {"data": result}


@router.post("/sessions/{session_id}/turns")
async def create_turn(session_id: str, body: TurnRequest, request: Request):
    session = owned_session(request, session_id)
    if session is None:
        return _error(request, 404, "SESSION_NOT_FOUND")
    user_id, identity_error = current_user_id_or_error(request)
    if identity_error is not None:
        return identity_error
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
            # 完成态重放：不产生 LLM 成本，因此不受限流约束。
            return StreamingResponse(
                _replay_turn(existing),
                media_type="text/event-stream",
                headers=_sse_headers(request),
            )
        # 同上：孤儿行按"可认领"处理，不当作进行中
        if existing["status"] == "streaming" and not turn_store.is_stale_streaming(existing):
            return _error(request, 409, "TURN_IN_PROGRESS")

    # ---- 到这里可以确定本次请求会真正执行一次 Turn ----
    # 限流与并发闸门必须放在创建/重启 Turn 行**之前**：
    # 若先落库再拒绝，会留下永不完成的 status='streaming' 行，
    # 导致同一个 clientTurnId 之后永远返回 TURN_IN_PROGRESS（消息彻底卡死）。
    limited = enforce_rate_limit(request, user_id)
    if limited is not None:
        return limited

    concurrency_limit = get_settings().max_concurrent_turns
    if not turn_gate.try_reserve(concurrency_limit):
        return _error(request, 503, "SERVICE_BUSY")

    def _bail(response):
        """放弃本次执行：先归还并发槽位再返回，避免闸门泄漏。"""
        turn_gate.release(concurrency_limit)
        return response

    # 槽位已占用，从此处到"成功建流"之间的**任何**异常都必须归还槽位。
    # 只处理显式 return 路径是不够的：数据库故障等意外异常会漏掉归还，
    # 累计 max_concurrent_turns 次之后服务会永久返回 503。
    try:
        if existing is not None:
            turn_id = existing["id"]
            if not turn_store.restart_turn(turn_id):
                # claim 失败：另一进程已抢先恢复并开始执行，本请求按进行中处理。
                return _bail(_error(request, 409, "TURN_IN_PROGRESS"))
        else:
            try:
                turn_id = turn_store.create_turn(session_id, user_id, body.clientTurnId, payload)
            except sqlite3.IntegrityError:
                # 并发窗口：另一个同 key 请求刚插入。重查当前状态按 canonical 处理，
                # 避免 INSERT 竞态直接 500（B8/B9）。
                outcome = _handle_duplicate_key(request, session_id, body.clientTurnId, payload)
                if isinstance(outcome, StreamingResponse) or isinstance(outcome, JSONResponse):
                    return _bail(outcome)
                if outcome is None:
                    # 重查也不存在：极端情况，保留原始 IntegrityError 语义
                    raise
                turn_id, _ = outcome
    except Exception:
        # 意外异常（如数据库不可用）：归还槽位后原样抛出，交给统一错误处理器。
        turn_gate.release(concurrency_limit)
        raise

    async def _guarded_stream():
        # run_turn 内部没有顶层 try/finally，用外层包装保证槽位一定归还
        # （客户端断开时 StreamingResponse 会 close 生成器，finally 同样执行）。
        #
        # 心跳由独立线程按墙钟发送，**不挂在事件上**：上游可能出现"持续有数据
        # 但不产出任何事件"的情况（代理注入的 `: keep-alive` 注释行、空 delta
        # 分片），此时 httpx 的 read timeout 不会触发（数据在流动），事件却一个
        # 都不来——挂在事件上的心跳会静默停掉，让这一行被判成孤儿并抢走。
        # 心跳的含义是"本进程仍在处理这一轮"，与是否有数据无关。
        #
        # 用线程而不是 asyncio 任务：清理只需 set()，不必在 async 生成器的
        # finally 里 await（客户端断开走的是 GeneratorExit 路径，在那里 await
        # 会触发 "async generator ignored GeneratorExit"）；而且事件循环被阻塞时
        # 心跳依然有效——那恰恰是"这一轮看起来像死了"的时候。
        stop_heartbeat = threading.Event()

        def heartbeat() -> None:
            while True:
                try:
                    turn_store.touch_turn(turn_id)
                except Exception:  # noqa: BLE001 — 心跳是尽力而为，绝不能因此停摆
                    # 一次瞬时 DB 错误（锁竞争、IO 抖动）不能让心跳永久停掉：
                    # 停摆超过阈值后这一轮会被判成孤儿并抢走 → 重复执行，
                    # 而这正是心跳要防的事。线程带着未捕获异常退出还会往日志里
                    # 打一段堆栈。真正的持续性 DB 故障会在本轮其它 DB 调用上
                    # 更响亮地暴露，不需要在这里报告。
                    pass
                if stop_heartbeat.wait(turn_store.TURN_HEARTBEAT_SECONDS):
                    return

        beat = threading.Thread(
            target=heartbeat, name=f"turn-heartbeat-{turn_id}", daemon=True
        )
        beat.start()
        try:
            async for event in orchestrator.run_turn(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                user_text=body.message.content,
                request_id=_request_id(request),
            ):
                yield event
        finally:
            stop_heartbeat.set()
            turn_gate.release(concurrency_limit)

    return StreamingResponse(
        encode(_guarded_stream()),
        media_type="text/event-stream",
        headers=_sse_headers(request),
    )
