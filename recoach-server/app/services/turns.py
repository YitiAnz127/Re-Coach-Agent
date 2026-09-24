from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import get_settings
from ..ids import new_id, now_iso
from . import instances
from . import selection as from_selection


# 流式期间刷新心跳的最小间隔（秒）。
#
# 事件本身很频繁（每个 delta 一个），但关键是"模型长时间不吐字"时也必须有心跳，
# 所以按墙钟节流，而不是按事件计数。
TURN_HEARTBEAT_SECONDS = 5.0


def stale_streaming_cutoff() -> str:
    """超过它的 `streaming` 行才是孤儿。

    判据是"多久**没有心跳**"，而不是"跑了多久"——这一点是致命的：
    `llm_timeout` 传给 httpx 时限制的是**两次数据之间的间隔**，不是总时长
    （实测：客户端 timeout=2s 可以读完整整 6s、每秒一个 chunk 的响应）。
    因此一轮合法 Turn 可以稳定输出远超过 `llm_timeout` 的时间；但凡按
    "运行总时长"判定，就会把正在跑的轮次判成孤儿并抢走 → 同一轮重复执行。

    真正的活动信号由 `touch_turn` 提供：`routes/turns.py` 用一个**独立线程**按
    `TURN_HEARTBEAT_SECONDS` 的墙钟节拍刷新 `updated_at`，与上游是否吐数据无关。
    （曾经把心跳挂在 SSE 事件上，结果是"持续有数据但不产出事件"时——代理注入的
    `: keep-alive`、空 delta 分片——心跳会静默停掉。）
    因此门槛只需显著大于心跳间隔；这里取**两倍读取超时加余量**，给"进程被杀"
    与"事件循环被阻塞导致心跳停摆"两种情况都留足余量。

    为什么需要这条兜底：owner 归属解决了"误杀别的实例正在跑的轮次"，但一行
    若 owner 既不匹配当前实例、也不是空（`RECOACH_INSTANCE_ID` 改过，或
    `runtime_meta` 丢失而 `turns` 仍在），它就既不被启动恢复清理、也不被
    restart_turn 认领——同一个 clientTurnId 永远 409，只能改库才能出来。

    时间戳是同一格式的 UTC ISO-8601，字典序即时间序。
    """
    settings = get_settings()
    grace = settings.llm_timeout * 2 + 60.0
    return (
        datetime.now(timezone.utc) - timedelta(seconds=grace)
    ).isoformat(timespec="milliseconds")


def is_stale_streaming(turn: dict) -> bool:
    """该行是否是一个已失去心跳的孤儿 streaming 行（只判断，不改状态）。

    缺失时间戳时**失败关闭**（返回 False，即不当作孤儿）：这个判断为真会导致
    抢走并重新执行，方向搞错就变成重复执行，拿不准时必须保守。
    """
    if turn.get("status") != "streaming":
        return False
    updated_at = str(turn.get("updated_at") or "")
    if not updated_at:
        return False
    return updated_at < stale_streaming_cutoff()


def touch_turn(turn_id: str) -> bool:
    """流式期间刷新心跳，证明这一轮还活着。返回是否命中一行 streaming。

    未命中说明该轮已经结束、失败，或已被别的进程接管——此时不必再刷。
    """
    with db.tx() as conn:
        cursor = conn.execute(
            "UPDATE turns SET updated_at=? WHERE id=? AND status='streaming'",
            (now_iso(), turn_id),
        )
        return cursor.rowcount > 0


def create_turn(session_id: str, user_id: str, client_turn_id: str, request_payload: dict) -> str:
    turn_id = new_id("turn")
    now = now_iso()
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO turns
               (id, session_id, user_id, client_turn_id, status, mode,
                request_json, response_text, presentation_json, error_json,
                created_at, updated_at, owner_instance)
               VALUES (?,?,?,?, 'streaming', 'explain', ?, '', NULL, NULL, ?, ?, ?)""",
            (turn_id, session_id, user_id, client_turn_id,
             json.dumps(request_payload, ensure_ascii=False), now, now,
             instances.current_instance_id()),
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

    可被认领的三种状态：
    - `error`：正常失败态；
    - `completed` 但 `presentation_json IS NULL`：不一致态。它既不能被重放
      （路由要求 completed 且有 presentation），也不能被认领，**同一个
      clientTurnId 会永远卡在 409 TURN_IN_PROGRESS**，只能改库才能出来；
    - `streaming` 但已超过任何一轮合法时长：孤儿行（含 owner 不匹配的历史遗留，
      见 stale_streaming_cutoff）。真孤儿不会永远年轻，所以这一条总能自愈。

    并发下两个进程同时重试时只有一个能 claim 成功，失败的调用方应返回
    TURN_IN_PROGRESS。副作用存储由各服务按 Turn 幂等。
    """
    with db.tx() as conn:
        cursor = conn.execute(
            # `updated_at > ''` 排掉空串与 NULL：与 is_stale_streaming 的失败关闭
            # 保持一致（空串字典序小于任何时间戳，不排除的话同一行在"只判断"与
            # "真正认领"两条路径上会得出相反结论）。
            """UPDATE turns SET status='streaming', response_text='', presentation_json=NULL,
               error_json=NULL, updated_at=?, owner_instance=?
               WHERE id=? AND (status='error'
                              OR (status='completed' AND presentation_json IS NULL)
                              OR (status='streaming' AND updated_at > '' AND updated_at < ?))""",
            # 认领方成为新的 owner：本进程重启后才有资格回收这一轮。
            (now_iso(), instances.current_instance_id(), turn_id, stale_streaming_cutoff()),
        )
        return cursor.rowcount > 0


def recover_stale_streaming(instance_id: str | None = None) -> int:
    """服务启动时把**本实例**遗留的 streaming Turn 标为 error（CANCELLED/ProcessRestart）。

    进程崩溃/重启会留下 streaming 态的 Turn；启动即恢复，客户端用同一
    clientTurnId 重试时路由会原子 claim 后重新执行。返回恢复条数。

    只回收 owner_instance 等于本实例的行。原实现无条件扫描全部 streaming 行，
    多进程共享 DB 时任一进程启动都会把别的进程正在流式输出的轮次标成 error，
    那些轮次随后被重试认领 → 同一轮重复执行（双份 LLM 成本 + 两个写入者）。
    owner_instance 为空的行来自本迁移之前，按本实例处理以保留原有恢复行为；
    另外无条件回收已超过合法时长的孤儿行（见 stale_streaming_cutoff），
    否则 owner 不匹配又非本实例留下的行会永远卡住。
    """
    owner = instance_id if instance_id is not None else instances.current_instance_id()
    error_json = json.dumps({"code": "CANCELLED", "type": "ProcessRestart"}, ensure_ascii=False)
    with db.tx() as conn:
        cursor = conn.execute(
            # `updated_at > ''` 同 restart_turn：空/缺失时间戳不按孤儿处理。
            """UPDATE turns SET status='error', error_json=?, updated_at=?
               WHERE status='streaming'
                 AND (owner_instance=? OR owner_instance=''
                      OR (updated_at > '' AND updated_at < ?))""",
            (error_json, now_iso(), owner, stale_streaming_cutoff()),
        )
        return cursor.rowcount


def list_completed_turns(session_id: str, limit: int = 200) -> list[dict]:
    """按时间顺序返回该会话已完成的轮次，供客户端恢复历史。

    只返回 completed 且带 presentation 的轮次：error/streaming 轮次没有可渲染的
    呈现结构，恢复出来只会是半截内容。原始用户输入取自 request_json
    （与实时对话里显示的一致，编号选择不会被替换成长文本）。
    """
    rows = db.query(
        """SELECT t.id, t.request_json, t.response_text, t.presentation_json,
                  t.mode, t.created_at, c.rating AS calibration
           FROM turns t LEFT JOIN teaching_calibrations c ON c.turn_id=t.id
           WHERE t.session_id=? AND t.status='completed' AND t.presentation_json IS NOT NULL
           ORDER BY t.created_at ASC, t.rowid ASC LIMIT ?""",
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
                "calibration": row["calibration"],
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
