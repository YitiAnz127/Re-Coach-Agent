from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import db
from ..config import get_settings
from ..ids import new_id, now_iso
from ..schemas import Memory, ResolvedTask
from ..tokens import estimate_tokens

# 证据强度排序（v0.6 §8.3）：显式长期规则 > 显式纠正 > 多次独立重复 > 认可的讲法 > 单次隐式候选
EVIDENCE_STRENGTH = {
    "user_explicit_longterm": 1.0,
    "user_explicit_correction": 0.85,
    "repeated_independent_feedback": 0.7,
    "user_endorsed_explanation": 0.55,
    "single_implicit_signal": 0.3,
}

# 候选排序基线权重（v0.6 §8.3，可解释、可用真实回放调参）
W_SCOPE = 0.40
W_LEXICAL = 0.25
W_EVIDENCE = 0.15
W_CONFIDENCE = 0.10
W_RECENCY = 0.10

SCOPE_FIELDS = ("domain", "concept_scope", "proposition_scope", "task_scope")


def _row_to_memory(row) -> Memory:
    return Memory(
        id=row["id"],
        user_id=row["user_id"],
        type=row["type"],
        rule=row["rule"],
        domain=row["domain"],
        concept_scope=row["concept_scope"],
        proposition_scope=row["proposition_scope"],
        task_scope=row["task_scope"],
        polarity=row["polarity"],
        evidence_kind=row["evidence_kind"],
        source_event_ids=json.loads(row["source_event_ids"] or "[]"),
        confidence=row["confidence"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_memory(memory_id: str) -> Memory | None:
    row = db.query_one("SELECT * FROM memories WHERE id=?", (memory_id,))
    return _row_to_memory(row) if row else None


def list_memories(
    user_id: str, *, status: str = "active", type_: str | None = None, domain: str | None = None
) -> list[Memory]:
    sql = "SELECT * FROM memories WHERE user_id=? AND status=?"
    params: list = [user_id, status]
    if type_:
        sql += " AND type=?"
        params.append(type_)
    if domain:
        sql += " AND (domain=? OR domain='*')"
        params.append(domain)
    sql += " ORDER BY updated_at DESC LIMIT 200"
    return [_row_to_memory(r) for r in db.query(sql, tuple(params))]


def _specificity(memory: Memory) -> int:
    """作用域精确度：非 * 的层级越多越好（越具体的记忆优先级越高）。"""
    return sum(1 for f in SCOPE_FIELDS if getattr(memory, f) not in ("*", ""))


def _scope_match(memory: Memory, task: ResolvedTask) -> float:
    """五级作用域匹配得分 0..1。具体记忆不会自动覆盖不相关概念。"""
    checks = {
        "domain": task.domain,
        "concept_scope": task.concept,
        "proposition_scope": task.proposition,
        "task_scope": task.task_scope,
    }
    matched = 0.0
    for f, value in checks.items():
        scope_value = getattr(memory, f)
        if scope_value in ("*", ""):
            matched += 0.5  # 通配：可用但不精确
            continue
        if not value:
            return 0.0
        if f == "proposition_scope":
            # 具体命题不重合就是硬不匹配，不能靠同领域得分被带入。
            # 只允许"记忆命题是任务命题的子串"（记忆更宽泛时可覆盖具体任务），
            # 不允许"任务命题是记忆命题的子串"（高度具体的记忆不得泛化到短命题）；
            # 短作用域（<4 字符）不做子串匹配，防两字词（如"梯度"）误伤相邻概念。
            if scope_value == value or (scope_value in value and len(scope_value) >= 4):
                matched += 1.0
            else:
                return 0.0
        elif scope_value == value:
            matched += 1.0
        else:
            return 0.0
    return matched / len(checks)


def _recency(updated_at: str) -> float:
    try:
        age_days = max(
            0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).days
        )
    except (ValueError, TypeError):
        return 0.5
    return max(0.0, 1.0 - age_days / 90.0)


_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _lexical_tokens(text: str) -> list[str]:
    """提取适合中英文记忆召回的短词；中文按相邻二字切分。"""
    tokens = [match.group(0).lower() for match in _WORD_RE.finditer(text)]
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(token for token in tokens if len(token) >= 2))[:12]


def _fts_candidates(user_id: str, text: str) -> dict[str, float]:
    """FTS5 关键词候选召回；中文短词使用 LIKE 参与召回。"""
    tokens = _lexical_tokens(text)
    if not tokens:
        return {}

    scores: dict[str, float] = {}
    fts_tokens = [token for token in tokens if not _CJK_RUN_RE.fullmatch(token)]
    fts_succeeded = False
    if db.FTS5_AVAILABLE and fts_tokens:
        match = " OR ".join(f'"{token}"' for token in fts_tokens)
        try:
            rows = db.query(
                """SELECT m.id, bm25(memories_fts) AS rank
                   FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                   WHERE memories_fts MATCH ? AND m.user_id=? AND m.status='active'
                   LIMIT 20""",
                (match, user_id),
            )
            if rows:
                # bm25 越小（越负）越相关：min 是最相关，max 是最不相关
                worst = min(r["rank"] for r in rows)
                best = max(r["rank"] for r in rows)
                span = (best - worst) or 1.0
                for row in rows:
                    scores[row["id"]] = (best - row["rank"]) / span
                fts_succeeded = True
        except db.sqlite3.OperationalError:
            pass

    # SQLite 默认 unicode61 tokenizer 不会把连续中文拆成二字词，
    # 因此中文 token 始终用参数化 LIKE 补充召回；FTS 失败时所有 token 都走 LIKE。
    like_tokens = (
        tokens
        if not fts_succeeded
        else [token for token in tokens if _CJK_RUN_RE.fullmatch(token)]
    )
    for token in like_tokens:
        for row in db.query(
            "SELECT id FROM memories WHERE user_id=? AND status='active' AND rule LIKE ? LIMIT 20",
            (user_id, f"%{token}%"),
        ):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / len(tokens)
    return scores


@dataclass
class RetrievalResult:
    selected: list[Memory] = field(default_factory=list)
    recalled_ids: list[str] = field(default_factory=list)
    search_ms: int = 0


def retrieve(
    user_id: str,
    task: ResolvedTask,
    *,
    memory_on: bool | None = None,
    memory_ids: set[str] | None = None,
) -> RetrievalResult:
    """问题完整后的完整检索：硬过滤 → 作用域筛选 → FTS5 候选 → 排序 → 选 1-3 条（硬上限 4）。

    只在 ResolvedTask 形成之后调用；澄清前禁止做这种宽泛主题召回。
    """
    started = time.perf_counter()
    settings = get_settings()
    result = RetrievalResult()
    effective_memory_on = settings.memory_on if memory_on is None else memory_on
    if not effective_memory_on:
        return result

    rows = db.query(
        "SELECT * FROM memories WHERE user_id=? AND status='active'", (user_id,)
    )
    memories = [
        _row_to_memory(r) for r in rows
        if memory_ids is None or r["id"] in memory_ids
    ]
    if not memories:
        result.search_ms = int((time.perf_counter() - started) * 1000)
        return result

    query_text = " ".join(
        [task.concept, task.proposition, task.goal, task.task_scope]
    ).strip()
    lexical = _fts_candidates(user_id, query_text)

    scored: list[tuple[float, Memory]] = []
    for m in memories:
        scope = _scope_match(m, task)
        # 完全不相关的具体记忆（如别的概念下的规则）不参与
        if scope <= 0.4 and _specificity(m) > 0:
            continue
        score = (
            W_SCOPE * scope
            + W_LEXICAL * lexical.get(m.id, 0.0)
            + W_EVIDENCE * EVIDENCE_STRENGTH.get(m.evidence_kind, 0.3)
            + W_CONFIDENCE * m.confidence
            + W_RECENCY * _recency(m.updated_at)
        )
        scored.append((score, m))

    scored.sort(key=lambda item: item[0], reverse=True)
    result.recalled_ids = [m.id for _, m in scored]

    # 折叠冲突：同 (type, domain, concept_scope) 只保留分数最高者；全局规则只在没有更具体规则时使用
    seen_keys: set[tuple] = set()
    has_specific = any(_specificity(m) > 0 for _, m in scored)
    for score, m in scored:
        if len(result.selected) >= settings.memory_hard_limit:
            break
        # 门槛 0.50：允许"用户明确长期声明"级的全局规则进入
        # （evidence=1.0 + confidence≈0.9 + recency=1.0 → 0.54）；
        # 弱证据全局规则（单次隐式 ≈0.40）仍被丢弃，不污染 Capsule。
        if _specificity(m) == 0 and has_specific and score < 0.50:
            continue
        key = (m.type, m.domain, m.concept_scope, m.proposition_scope, m.task_scope)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        result.selected.append(m)
        if len(result.selected) >= settings.memory_max_selected:
            break

    result.search_ms = int((time.perf_counter() - started) * 1000)
    return result


def scope_label(m: Memory) -> str:
    parts = [m.domain, m.concept_scope, m.proposition_scope, m.task_scope]
    return " / ".join(p if p else "*" for p in parts)


# ---------- 写入、归档、遗忘 ----------


def write_memory(
    user_id: str,
    *,
    type_: str,
    rule: str,
    domain: str = "*",
    concept_scope: str = "*",
    proposition_scope: str = "*",
    task_scope: str = "*",
    polarity: str = "positive",
    evidence_kind: str = "user_explicit_longterm",
    confidence: float = 0.8,
    source_event_id: str,
) -> Memory:
    """写入稳定记忆。相同来源事件幂等；同作用域新规则保留归档链。"""
    # 幂等按"来源事件是否参与过该记忆"判断：遗忘会在 source_event_ids 上追加遗忘
    # 事件，精确 blob 匹配会 miss，导致用户已遗忘的规则在重试写入时被静默复活。
    # 使用包含匹配：同一来源事件命中已存在记忆（无论 active/forgotten）即幂等返回，
    # 不再新建；用户用新事件重新声明同一规则仍走正常写入路径。
    marker = f'%"{source_event_id}"%'
    existing_source = db.query_one(
        "SELECT * FROM memories WHERE user_id=? AND source_event_ids LIKE ? ORDER BY created_at LIMIT 1",
        (user_id, marker),
    )
    if existing_source:
        return _row_to_memory(existing_source)

    now = now_iso()
    existing = [
        m
        for m in list_memories(user_id, domain=domain if domain != "*" else None)
        if m.type == type_
        and m.domain == domain
        and m.concept_scope == concept_scope
        and m.proposition_scope == proposition_scope
        and m.task_scope == task_scope
        and _rule_similar(m.rule, rule)
    ]
    memory_id = new_id("mem")
    with db.tx() as conn:
        for old in existing:
            conn.execute(
                "UPDATE memories SET status='archived', superseded_by=?, updated_at=? WHERE id=?",
                (memory_id, now, old.id),
            )
        conn.execute(
            """INSERT INTO memories
               (id, user_id, type, rule, domain, concept_scope, proposition_scope,
                task_scope, polarity, evidence_kind, source_event_ids, confidence,
                status, superseded_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active', NULL, ?, ?)""",
            (
                memory_id,
                user_id,
                type_,
                rule,
                domain,
                concept_scope,
                proposition_scope,
                task_scope,
                polarity,
                evidence_kind,
                json.dumps([source_event_id], ensure_ascii=False),
                confidence,
                now,
                now,
            ),
        )
    return get_memory(memory_id)  # type: ignore[return-value]


def _rule_similar(a: str, b: str) -> bool:
    """朴素的同义判断：去掉语气词后字符重合度 ≥ 0.6。"""
    stop = "的了呢吧啊哦嗯请帮我希望以后每次都不再要"

    def chars(s: str) -> set[str]:
        # str.isalnum() 对 CJK 字符返回 True，天然覆盖中文
        return {c for c in s if c.isalnum() and c not in stop}

    sa, sb = chars(a), chars(b)
    if not sa or not sb:
        return False
    # 以较短规则为基准：用户改口通常是在原规则上增删措辞
    overlap = len(sa & sb) / min(len(sa), len(sb))
    return overlap >= 0.5


def forget_memories(user_id: str, keyword: str, source_event_id: str) -> list[str]:
    """自然语言遗忘：归档而不物理删除，并以来源事件保证失败重试幂等。"""
    keyword = keyword.strip()
    # 未能解析出遗忘对象时必须安全无操作，不能把空字符串解释为“全部记忆”。
    if not keyword:
        return []

    marker = f'%"{source_event_id}"%'
    previous = db.query(
        """SELECT id FROM memories
           WHERE user_id=? AND status='forgotten' AND source_event_ids LIKE ?
           ORDER BY updated_at""",
        (user_id, marker),
    )
    if previous:
        return [row["id"] for row in previous]

    forgotten: list[str] = []
    now = now_iso()
    candidates = list_memories(user_id)
    with db.tx() as conn:
        for m in candidates:
            if keyword and keyword not in m.rule:
                continue
            # 遗忘事件追加进 source_event_ids：供同一遗忘操作的重试幂等
            # （previous 查询按事件 id 命中已 forgotten 记忆直接重放）。
            # write_memory 的幂等检查使用包含匹配（见 write_memory），不受追加影响，
            # 重试原写入不会静默复活已遗忘规则。
            source_ids = list(dict.fromkeys([*m.source_event_ids, source_event_id]))
            conn.execute(
                """UPDATE memories
                  SET status='forgotten', source_event_ids=?, updated_at=?
                  WHERE id=?""",
                (json.dumps(source_ids, ensure_ascii=False), now, m.id),
            )
            forgotten.append(m.id)
    return forgotten


# ---------- 第一级反馈门控：本地规则（v0.6 §8.6） ----------

_LONGTERM_HINT = re.compile(r"(以后|每次|记住|不要再|别再|一直|总是|都按|都先|我喜欢|我偏好|我习惯|保持|继续用|继续这样)")
_FORGET_HINT = re.compile(r"(忘记|忘掉|遗忘|不用记住|别记住|不要记住|清除|删除|取消)")
_SESSION_ONLY_HINT = re.compile(r"(这次|本次|这轮|今天|先这样|暂时|就这一次|仅此一次)")
_NEGATIVE_HINT = re.compile(r"(不要|别|不再|禁止|避免|不喜欢|不习惯|讨厌|反对)")


@dataclass
class FeedbackAction:
    kind: str  # write_longterm | session_only | forget | none
    rule: str = ""
    keyword: str = ""
    polarity: str = "positive"
    memory_type: str = "explanation_preference"


def classify_feedback(text: str) -> FeedbackAction:
    """本地规则门控。只处理明确表达；含糊或复杂意图返回 none，交给异步蒸馏（P1）。"""
    compact = text.strip()
    forget = _FORGET_HINT.search(compact)
    if forget:
        # “忘记之前关于 X 的偏好/记忆/规则/讲法” → 提取 X 用于匹配
        m = re.search(r"关于(.+?)(?:的)?(?:偏好|记忆|规则|讲法)[。！？!?.]?$", compact)
        keyword = m.group(1) if m else ""
        keyword = keyword.lstrip("关于与和").strip("的了 ")
        return FeedbackAction(kind="forget", keyword=keyword)
    if _SESSION_ONLY_HINT.search(compact) and _LONGTERM_HINT.search(compact) is None:
        return FeedbackAction(kind="session_only", rule=compact)
    if _LONGTERM_HINT.search(compact):
        polarity = "negative" if _NEGATIVE_HINT.search(compact) else "positive"
        memory_type = (
            "interaction_rule" if re.search(r"(每次|以后|一直|总是|都按|都先)", compact) else "explanation_preference"
        )
        return FeedbackAction(
            kind="write_longterm", rule=compact, polarity=polarity, memory_type=memory_type
        )
    return FeedbackAction(kind="none")


def capsule_of(memories: list[Memory]) -> tuple[str, int]:
    """把选中的记忆渲染成 Memory Capsule 文本，返回 (text, tokens)。预算在 Compiler 里强制。"""
    lines = []
    for m in memories:
        prefix = "避免" if m.polarity == "negative" else "偏好"
        lines.append(f"- [{m.type}/{scope_label(m)}] {prefix}：{m.rule}")
    text = "\n".join(lines)
    return text, estimate_tokens(text)
