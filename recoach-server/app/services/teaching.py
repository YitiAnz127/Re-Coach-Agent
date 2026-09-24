from __future__ import annotations

import re
from typing import Mapping

from .. import db
from ..ids import now_iso
from ..schemas import ResolvedTask, TeachingStart


def infer_teaching_start(
    task: ResolvedTask,
    user_text: str,
    concept_states: list[dict],
    calibration: Mapping | None = None,
) -> TeachingStart:
    """只用有作用域的可观察证据确定讲解起点；没有证据就保持 unknown。"""
    base = {"domain": task.domain, "concept": task.concept}
    if re.search(r"(我是新手|第一次学|没学过|零基础|从零开始|小白|我不懂|我不会)", user_text):
        return TeachingStart(level="novice", source="current_explicit", **base)
    if re.search(r"(我熟悉|我掌握|我已经掌握|跳过基础|不用讲基础)", user_text):
        return TeachingStart(level="advanced", source="current_explicit", **base)
    if re.search(r"(我知道|我学过|我了解|我会)", user_text):
        return TeachingStart(level="familiar", source="current_explicit", **base)

    if task.concept and calibration and calibration.get("level") in {"novice", "familiar", "advanced"}:
        return TeachingStart(level=calibration["level"], source="concept_feedback", **base)

    for state in concept_states:
        if state.get("concept") != task.concept or state.get("proposition") != task.proposition:
            continue
        if state.get("state") == "self_reported_understood" and state.get("evidence_kind") == "user_self_report":
            return TeachingStart(level="familiar", source="proposition_state", **base)
        if state.get("state") == "unresolved" and state.get("evidence_kind") == "user_explicit_unresolved":
            return TeachingStart(level="novice", source="proposition_state", **base)
    return TeachingStart(**base)


def latest_calibration(user_id: str, domain: str, concept: str) -> dict | None:
    if not concept:
        return None
    row = db.query_one(
        """SELECT * FROM teaching_calibrations
           WHERE user_id=? AND domain=? AND concept=?
           ORDER BY updated_at DESC, rowid DESC LIMIT 1""",
        (user_id, domain, concept),
    )
    return dict(row) if row else None


def _adjust(base_level: str, rating: str) -> str:
    levels = ("novice", "familiar", "advanced")
    if rating == "just_right":
        return base_level
    if rating == "too_basic":
        return levels[min(levels.index(base_level) + 1, 2)] if base_level in levels else "familiar"
    return levels[max(levels.index(base_level) - 1, 0)] if base_level in levels else "novice"


def record_calibration(
    *, turn_id: str, user_id: str, domain: str, concept: str, rating: str, base_level: str,
) -> dict:
    """同一回答可改评；始终从回答当时的起点调整，重复提交不会逐次升级。"""
    now = now_iso()
    with db.tx() as conn:
        existing = conn.execute("SELECT base_level, created_at FROM teaching_calibrations WHERE turn_id=?", (turn_id,)).fetchone()
        original = existing["base_level"] if existing else base_level
        created = existing["created_at"] if existing else now
        level = _adjust(original, rating)
        conn.execute(
            """INSERT INTO teaching_calibrations
               (turn_id,user_id,domain,concept,rating,base_level,level,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(turn_id) DO UPDATE SET
                 rating=excluded.rating, level=excluded.level, updated_at=excluded.updated_at""",
            (turn_id, user_id, domain, concept, rating, original, level, created, now),
        )
    return {"rating": rating, "level": level, "concept": concept}
