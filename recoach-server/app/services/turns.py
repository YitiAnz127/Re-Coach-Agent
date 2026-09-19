from __future__ import annotations

import json

from .. import db
from ..ids import new_id, now_iso
from . import selection as from_selection


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


def list_completed_turns(session_id: str, limit: int = 200) -> list[dict]:
    """按时间顺序返回该会话已完成的轮次，供客户端恢复历史。

    只返回 completed 且带 presentation 的轮次：error/streaming 轮次没有可渲染的
    呈现结构，恢复出来只会是半截内容。原始用户输入取自 request_json
    （与实时对话里显示的一致，编号选择不会被替换成长文本）。
    """
    rows = db.query(
        """SELECT id, request_json, response_text, presentation_json, mode, created_at
           FROM turns
           WHERE session_id=? AND status='completed' AND presentation_json IS NOT NULL
           ORDER BY created_at ASC, rowid ASC LIMIT ?""",
        (session_id, limit),
    )
    result: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["request_json"])
            user_text = (payload.get("message") or {}).get("content") or ""
        except (json.JSONDecodeError, AttributeError, TypeError):
            user_text = ""
        presentation = from_selection.load_presentation(row["presentation_json"])
        if presentation is None:
            continue
        result.append(
            {
                "turnId": row["id"],
                "userText": user_text,
                "assistantText": row["response_text"] or "",
                "mode": row["mode"],
                "presentation": presentation,
                "createdAt": row["created_at"],
            }
        )
    return result


def latest_completed_presentation(session_id: str) -> dict | None:
    """取该会话最近一轮**已完成**的 presentation。

    用于识别"用户在回应上一轮的澄清选项"。只取 status='completed'，
    因此不会命中本轮刚创建、尚在 streaming 的那一行（它的 presentation 为 NULL）。
    rowid 兜底排序：同一秒内创建的多轮 created_at 可能相同。
    """
    row = db.query_one(
        """SELECT presentation_json FROM turns
           WHERE session_id=? AND status='completed' AND presentation_json IS NOT NULL
           ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (session_id,),
    )
    return from_selection.load_presentation(row["presentation_json"]) if row else None


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
