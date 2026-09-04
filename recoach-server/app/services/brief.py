from __future__ import annotations

import json
import re

from .. import db
from ..ids import new_id, now_iso
from ..schemas import ResolvedTask, SessionBrief
from . import memory as memory_service


def create_session(user_id: str, locale: str) -> str:
    session_id = new_id("ses")
    brief = SessionBrief()
    now = now_iso()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, brief_json, version, created_at, updated_at) VALUES (?,?,?,1,?,?)",
            (session_id, user_id, brief.model_dump_json(), now, now),
        )
    return session_id


def get_session(session_id: str) -> tuple[str, SessionBrief, int] | None:
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        return None
    return row["user_id"], SessionBrief.model_validate_json(row["brief_json"]), row["version"]


def update_brief(session_id: str, brief: SessionBrief, expected_version: int | None = None) -> int:
    """确定性合并结果整体落库，版本号 +1。

    传入 expected_version 时做乐观锁（CAS）：会话版本已被他人推进则放弃写入，
    防止并发轮次基于旧 Brief 覆盖新状态。失败时调用方仍持有旧版本。
    """
    with db.tx() as conn:
        if expected_version is None:
            conn.execute(
                "UPDATE sessions SET brief_json=?, version=version+1, updated_at=? WHERE id=?",
                (brief.model_dump_json(), now_iso(), session_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET brief_json=?, version=version+1, updated_at=? WHERE id=? AND version=?",
                (brief.model_dump_json(), now_iso(), session_id, expected_version),
            )
    row = db.query_one("SELECT version FROM sessions WHERE id=?", (session_id,))
    return row["version"] if row else 0


def apply_turn_delta(
    brief: SessionBrief,
    *,
    user_text: str,
    focus: str,
    mode: str,
    task: ResolvedTask | None = None,
    clarification_question: str = "",
    from_feedback: bool = False,
) -> SessionBrief:
    """旧 Brief + 本轮可观察事件 → 最小字段级 Delta，不调用 LLM 重写会话。"""
    updated = brief.model_copy(deep=True)
    # 纯反馈轮（记忆声明/遗忘，≤40 字符被门控强制 READY）不是教学轮：
    # 其文本会污染 goal/current_focus/exact_anchors 等结构化字段，
    # 并经由 fork 复制的 Brief 快照泄漏给 off 分支（无记忆对照），
    # 让"记忆关闭"失效。反馈轮只重置澄清计数，不做任何结构化提取。
    if from_feedback:
        updated.clarify_streak = 0
        return updated
    if mode == "clarify":
        updated.clarify_streak += 1
        question = clarification_question.strip() or f"需要进一步明确：{user_text[:120]}"
        updated.open_questions = [question[:240]]
    else:
        updated.clarify_streak = 0
        # 不覆盖 open_questions：ResolvedTask.open_questions 从未被赋值（恒空），
        # 原本的覆盖会清空澄清轮/未解轮记录的"未解决问题"，导致
        # 上下文编译器的"未解决问题"段与复盘开放问题恒为空。保留旧值，
        # 新的未解命题由下方 unresolved 分支追加（[:3] 截断）。
        if task and task.goal:
            updated.goal = task.goal[:120]
    if focus:
        updated.current_focus = focus
    if not updated.goal and mode == "explain":
        updated.goal = user_text[:120]

    # P1：只从用户明确措辞提取结构化状态，避免把一次模型输出当成学习结论。
    if task and task.proposition:
        if re.search(r"(我知道|已经懂|理解了|掌握了)", user_text):
            updated.known_propositions = list(dict.fromkeys(
                [*updated.known_propositions, task.proposition[:200]]
            ))[-8:]
        if re.search(r"(不懂|不理解|没懂|还是不会|卡住|不明白)", user_text):
            updated.open_questions = list(dict.fromkeys(
                [*updated.open_questions, task.proposition[:200]]
            ))[:3]
        if re.search(r"(讲得很好|很清楚|有帮助|这样讲我懂了|明白了)", user_text):
            updated.effective_explanations = list(dict.fromkeys(
                [*updated.effective_explanations, focus[:120]]
            ))[-8:]
        if re.search(r"(没讲清|没帮助|还是不懂|不太明白)", user_text):
            updated.failed_explanations = list(dict.fromkeys(
                [*updated.failed_explanations, focus[:120]]
            ))[-8:]
        if re.search(r"(公式|\$|代码|pytorch|\b[a-zA-Z_]+\([^)]*\))", user_text):
            updated.exact_anchors = list(dict.fromkeys(
                [*updated.exact_anchors, user_text[:240]]
            ))[-8:]
    return updated


def infer_concept_state(user_text: str, assistant_text: str) -> tuple[str, str]:
    """返回 (state, evidence_kind)，只依据可观察语言信号。"""
    if re.search(r"(你说错|不对|应该是|纠正|不是这样)", user_text):
        return "corrected", "user_explicit_correction"
    if re.search(r"(还是不懂|没懂|不理解|不明白|卡住)", user_text):
        return "unresolved", "user_explicit_unresolved"
    if re.search(r"(我懂了|明白了|理解了|讲清楚了|有帮助)", user_text):
        return "self_reported_understood", "user_self_report"
    return "introduced", "agent_observation"


def record_concept_state(
    user_id: str,
    task: ResolvedTask,
    *,
    state: str,
    evidence_kind: str,
    source_event_id: str,
) -> str | None:
    """命题级 Concept State：只保存具体命题 + 状态 + 来源事件。"""
    if not task.concept or not task.proposition:
        return None
    state_id = new_id("cst")
    now = now_iso()
    with db.tx() as conn:
        row = conn.execute(
            """SELECT id FROM concept_states
               WHERE user_id=? AND domain=? AND concept=? AND proposition=?""",
            (user_id, task.domain, task.concept, task.proposition),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE concept_states SET state=?, evidence_kind=?, source_event_id=?, updated_at=?
                   WHERE id=?""",
                (state, evidence_kind, source_event_id, now, row["id"]),
            )
            return row["id"]
        conn.execute(
            """INSERT INTO concept_states
               (id, user_id, domain, concept, proposition, state, evidence_kind,
                source_event_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                state_id,
                user_id,
                task.domain,
                task.concept,
                task.proposition,
                state,
                evidence_kind,
                source_event_id,
                now,
                now,
            ),
        )
    return state_id


def concept_states_for(
    user_id: str, task: ResolvedTask, limit: int = 5, state_ids: set[str] | None = None
) -> list[dict]:
    """只取当前概念（或当前领域）的命题级状态，避免宽泛注入。"""
    rows = db.query(
        """SELECT * FROM concept_states
           WHERE user_id=? AND (concept=? OR (domain=? AND ?=''))
           ORDER BY updated_at DESC LIMIT ?""",
        (user_id, task.concept, task.domain, task.concept, limit),
    )
    return [
        dict(r) for r in rows
        if state_ids is None or r["id"] in state_ids
    ]


def recent_messages(session_id: str, limit: int = 8) -> list[dict]:
    rows = db.query(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    )
    return [dict(r) for r in reversed(rows)]


def save_message(session_id: str, turn_id: str, role: str, content: str) -> None:
    """每个 Turn/role 保存一条 canonical 消息；重试覆盖而不是重复追加。"""
    now = now_iso()
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO messages (id, session_id, turn_id, role, content, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(turn_id, role) DO UPDATE SET
                 session_id=excluded.session_id,
                 content=excluded.content,
                 created_at=excluded.created_at""",
            (new_id("msg"), session_id, turn_id, role, content, now),
        )


def create_session_fork(
    source_session_id: str,
    *,
    user_id: str,
    fork_group_id: str,
    memory_mode: str,
    conn=None,
) -> str:
    """克隆 Session 工作状态和可见对话；长期记忆在实验期间只读。

    默认在独立事务内写入；调用方传入 db.tx() 的连接时并入外部事务，
    保证同一 fork group 的两个分支整体原子落库（任一分支失败全部回滚）。
    """
    source = db.query_one("SELECT * FROM sessions WHERE id=? AND user_id=?", (source_session_id, user_id))
    if source is None:
        raise ValueError("source session not found")
    session_id = new_id("ses")
    now = now_iso()
    memory_snapshot = [row["id"] for row in db.query(
        "SELECT id FROM memories WHERE user_id=? AND status='active' ORDER BY id", (user_id,)
    )]
    concept_snapshot = [row["id"] for row in db.query(
        "SELECT id FROM concept_states WHERE user_id=? ORDER BY id", (user_id,)
    )]
    if conn is None:
        with db.tx() as conn:
            _write_session_fork(
                conn, source_session_id, source, user_id, fork_group_id, memory_mode,
                session_id, now, memory_snapshot, concept_snapshot,
            )
    else:
        _write_session_fork(
            conn, source_session_id, source, user_id, fork_group_id, memory_mode,
            session_id, now, memory_snapshot, concept_snapshot,
        )
    return session_id


def _write_session_fork(
    conn,
    source_session_id: str,
    source: dict,
    user_id: str,
    fork_group_id: str,
    memory_mode: str,
    session_id: str,
    now: str,
    memory_snapshot: list[str],
    concept_snapshot: list[str],
) -> None:
    conn.execute(
        "INSERT INTO sessions (id, user_id, brief_json, version, created_at, updated_at) VALUES (?,?,?,1,?,?)",
        (session_id, user_id, source["brief_json"], now, now),
    )
    source_messages = conn.execute(
        "SELECT turn_id, role, content, created_at FROM messages WHERE session_id=? ORDER BY created_at",
        (source_session_id,),
    ).fetchall()
    # 纯反馈轮（记忆声明/遗忘/会话规则，≤40 字符且被反馈门控识别）不属于教学对话。
    # 若复制进 fork，会通过"共享对话"把用户偏好泄漏给 off 分支（无记忆对照），
    # 让"记忆关闭"失效；on/off 分支同步剔除，保证共同基线一致。
    skip_turns: set[str] = set()
    for message in source_messages:
        if (
            message["role"] == "user"
            and len(message["content"]) <= 40
            and memory_service.classify_feedback(message["content"]).kind != "none"
        ):
            skip_turns.add(message["turn_id"])
    # 剔除判定用原 turn_id；插入时每轮映射为新 turn_id（messages 表对
    # (turn_id, role) 有全局唯一约束，on/off 两个 fork 不能复用同一 turn_id）。
    turn_map: dict[str, str] = {}
    for message in source_messages:
        if message["turn_id"] in skip_turns:
            continue
        if message["turn_id"] not in turn_map:
            turn_map[message["turn_id"]] = new_id("forkturn")
        conn.execute(
            "INSERT INTO messages (id, session_id, turn_id, role, content, created_at) VALUES (?,?,?,?,?,?)",
            (new_id("msg"), session_id, turn_map[message["turn_id"]], message["role"], message["content"], message["created_at"]),
        )
    conn.execute(
        """INSERT INTO session_forks
           (id, fork_group_id, source_session_id, session_id, user_id, memory_mode, created_at,
            memory_snapshot_json, concept_snapshot_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            new_id("fork"), fork_group_id, source_session_id, session_id, user_id, memory_mode, now,
            json.dumps(memory_snapshot, ensure_ascii=False),
            json.dumps(concept_snapshot, ensure_ascii=False),
        ),
    )


def get_session_fork(session_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM session_forks WHERE session_id=?", (session_id,))
    return dict(row) if row else None
