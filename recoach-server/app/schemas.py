from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- v0.6 领域模型 ----------

Depth = Literal["L0", "L1", "L2", "L3", "L4", "L5"]
TurnMode = Literal["clarify", "explain", "reflect"]
GateDecision = Literal["READY", "NEEDS_CLARIFICATION", "ANSWER_WITH_ASSUMPTION"]
MemoryType = Literal["explanation_preference", "interaction_rule"]
MemoryStatus = Literal["active", "archived", "forgotten"]
ConceptState = Literal[
    "introduced",
    "self_reported_understood",
    "unresolved",
    "corrected",
    "observed_in_reasoning",
]

TASK_SCOPES = (
    "直觉解释",
    "机制分析",
    "数学推导",
    "代码实现",
    "论文理解",
    "工程权衡",
)


class ResolvedTask(BaseModel):
    """问题完整（或可按假设回答）后固化的任务。完整检索只发生在它形成之后。"""

    goal: str = ""
    domain: str = "machine_learning"
    concept: str = ""
    proposition: str = ""
    known_context: list[str] = Field(default_factory=list)
    desired_depth: str = "auto"  # auto | L0..L5
    task_scope: str = "直觉解释"
    output_preference: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Memory(BaseModel):
    id: str
    user_id: str
    type: MemoryType
    rule: str
    domain: str = "*"
    concept_scope: str = "*"
    proposition_scope: str = "*"
    task_scope: str = "*"
    polarity: str = "positive"  # positive | negative
    evidence_kind: str = "user_explicit_longterm"
    source_event_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.8
    status: MemoryStatus = "active"
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SessionBrief(BaseModel):
    """当前会话的结构化工作状态，字段级增量更新，不是聊天摘要。"""

    goal: str = ""
    current_focus: str = ""
    known_propositions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    effective_explanations: list[str] = Field(default_factory=list)
    failed_explanations: list[str] = Field(default_factory=list)
    exact_anchors: list[str] = Field(default_factory=list)
    clarify_streak: int = 0  # 连续澄清轮数，用于“最多连续两轮”
    session_rules: list[str] = Field(default_factory=list)  # 仅本会话生效的约定


# ---------- 前端 TurnPresentation 契约（与 src/types.ts 对齐） ----------


class PersonalizationItem(BaseModel):
    memoryId: str
    label: str
    scope: str
    effect: str


class Metrics(BaseModel):
    timeToFirstTokenMs: int = 0
    memorySearchMs: int = 0
    contextCompileMs: int = 0
    memoryCapsuleTokens: int = 0
    totalInputTokens: int = 0


class ClarificationOption(BaseModel):
    id: str
    label: str
    detail: str
    followUp: str


class ExperimentPoint(BaseModel):
    label: str
    value: float
    displayValue: str


class Experiment(BaseModel):
    title: str
    description: str
    code: str
    points: list[ExperimentPoint]
    takeaway: str


class Retrospective(BaseModel):
    connections: list[str]
    openQuestions: list[str]
    approach: list[str]


class OutputVerification(BaseModel):
    status: Literal["passed", "partial", "unknown"] = "unknown"
    checks: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class SuggestedAction(BaseModel):
    id: str
    label: str
    prompt: str


class TurnPresentation(BaseModel):
    """SSE turn.completed 展示契约（与 src/types.ts 对齐）。

    可选字段为空时必须在 model_dump(exclude_none=True) 中排除：
    前端校验器只容忍字段缺失（undefined），不容忍显式 null（INVALID_SSE_EVENT）。
    注意：Pydantic v2 的 ConfigDict(exclude_none=True) 不作用于 model_dump() 默认调用，
    因此必须在调用点显式传参（见 orchestrator._finalize_turn / run_turn）。
    """

    mode: TurnMode
    depth: Depth | None = None
    focus: str
    plan: list[str]
    personalization: list[PersonalizationItem] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    clarificationOptions: list[ClarificationOption] | None = None
    experiment: Experiment | None = None
    retrospective: Retrospective | None = None
    outputVerification: OutputVerification | None = None
    suggestedActions: list[SuggestedAction] = Field(default_factory=list)
    truncated: bool = False


# ---------- SSE 事件（与 src/types.ts 的 AgentStreamEvent 对齐） ----------


def sse_event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": event_type, **payload}
