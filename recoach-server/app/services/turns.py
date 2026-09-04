from __future__ import annotations

import json

from .. import db
from ..ids import new_id, now_iso


def create_turn(session_id: str, user_id: str, client_turn_id: str, request_payload: dict) -> str:
    turn_id = new_id("turn")
    now = now_iso()
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO turns
               (id, session_id, user_id, client_turn_id, status, mode,
                request_json, response_text, presentation_json, error_json,
                created_at, updated_at)
               VALUES (?,?,?,?, 'streaming', 'explain', ?, '', NULL, NULL, ?, ?)""",
            (turn_id, session_id, user_id, client_turn_id,
             json.dumps(request_payload, ensure_ascii=False), now, now),
        )
    return turn_id


def find_by_client_key(session_id: str, client_turn_id: str) -> dict | None:
    row = db.query_one(
        "SELECT * FROM turns WHERE session_id=? AND client_turn_id=?",
        (session_id, client_turn_id),
    )
    return dict(row) if row else None


def restart_turn(turn_id: str) -> bool:
    """原子地 claim 一个失败 Turn（error→streaming），返回是否 claim 成功。

    只有 error 状态可被恢复；并发下两个进程同时重试时，只有一个能 claim 成功，
    失败的调用方应返回 TURN_IN_PROGRESS。副作用存储由各服务按 Turn 幂等。
    """
    with db.tx() as conn:
        cursor = conn.execute(
            """UPDATE turns SET status='streaming', response_text='', presentation_json=NULL,
               error_json=NULL, updated_at=? WHERE id=? AND status='error'""",
            (now_iso(), turn_id),
        )
        return cursor.rowcount > 0


def recover_stale_streaming() -> int:
    """服务启动时把遗留 streaming Turn 标为 error（CANCELLED/ProcessRestart）。

    进程崩溃/重启会留下 streaming 态的 Turn；启动即恢复，客户端用同一
    clientTurnId 重试时路由会原子 claim 后重新执行。返回恢复条数。
    """
    error_json = json.dumps({"code": "CANCELLED", "type": "ProcessRestart"}, ensure_ascii=False)
    with db.tx() as conn:
        cursor = conn.execute(
            """UPDATE turns SET status='error', error_json=?, updated_at=?
               WHERE status='streaming'""",
            (error_json, now_iso()),
        )
        return cursor.rowcount


def finish_turn(
    turn_id: str,
    *,
    status: str,
    mode: str,
    response_text: str = "",
    presentation: dict | None = None,
    error: dict | None = None,
) -> None:
    with db.tx() as conn:
        conn.execute(
            """UPDATE turns SET status=?, mode=?, response_text=?,
               presentation_json=?, error_json=?, updated_at=? WHERE id=?""",
            (
                status,
                mode,
                response_text,
                json.dumps(presentation, ensure_ascii=False) if presentation else None,
                json.dumps(error, ensure_ascii=False) if error else None,
                now_iso(),
                turn_id,
            ),
        )
