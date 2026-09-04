from __future__ import annotations

import json
from typing import Any

from .. import db
from ..ids import new_id, now_iso

# 指标与审计的唯一事实来源（v0.6 §8.8）。常规回答不把 Event Log 原文注入 Prompt。
EVENT_KINDS = {
    "turn_started",
    "clarification_asked",
    "clarification_resolved",
    "memory_recalled",
    "memory_selected",
    "context_compiled",
    "model_called",
    "tool_called",
    "response_completed",
    "feedback_received",
    "memory_candidate_created",
    "memory_written",
    "memory_archived",
    "concept_state_updated",
    "session_brief_updated",
    "remote_sync_attempted",
    "remote_sync_completed",
    "turn_failed",
}


def log_event(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    mode: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    token_count: int | None = None,
    latency_ms: int | None = None,
) -> str:
    """按 (turn_id, kind) 幂等记录 P0 事件，重试时返回既有 event_id。"""
    if kind not in EVENT_KINDS:
        raise ValueError(f"未知事件类型: {kind}")
    with db.tx() as conn:
        existing = conn.execute(
            "SELECT id FROM events WHERE turn_id=? AND kind=?",
            (turn_id, kind),
        ).fetchone()
        if existing:
            return existing["id"]
        event_id = new_id("evt")
        conn.execute(
            """INSERT INTO events
               (id, user_id, session_id, turn_id, mode, kind, payload_json,
                token_count, latency_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                user_id,
                session_id,
                turn_id,
                mode,
                kind,
                json.dumps(payload or {}, ensure_ascii=False),
                token_count,
                latency_ms,
                now_iso(),
            ),
        )
        return event_id


def list_events(session_id: str, turn_id: str | None = None) -> list[dict[str, Any]]:
    if turn_id:
        rows = db.query(
            "SELECT * FROM events WHERE session_id=? AND turn_id=? ORDER BY created_at",
            (session_id, turn_id),
        )
    else:
        rows = db.query(
            "SELECT * FROM events WHERE session_id=? ORDER BY created_at", (session_id,)
        )
    return [dict(r) for r in rows]
