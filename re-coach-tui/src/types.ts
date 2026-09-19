// 领域模型：与 recoach 后端 schemas.py 对齐

export type Locale = "zh-CN" | "en";

export type Depth = "auto" | "L0" | "L1" | "L2" | "L3" | "L4" | "L5";

export type TaskScope =
  | "直觉解释"
  | "机制分析"
  | "数学推导"
  | "代码实现"
  | "论文理解"
  | "工程权衡"
  | "寒暄与开场";

export type GateDecision = "READY" | "NEEDS_CLARIFICATION" | "ANSWER_WITH_ASSUMPTION";

export interface ResolvedTask {
  goal: string;
  domain: string;
  concept: string;
  proposition: string;
  knownContext: string[];
  desiredDepth: Depth;
  taskScope: TaskScope;
  outputPreference: string[];
  openQuestions: string[];
  assumptions: string[];
}

export interface ClarificationOption {
  id: string;
  label: string;
  detail: string;
  followUp: string;
}

export interface GateResult {
  decision: GateDecision;
  task: ResolvedTask;
  focus: string;
  plan: string[];
  question?: string;
  options?: ClarificationOption[];
  assumption?: string;
}

// ------- 记忆 -------

export type MemoryType = "explanation_preference" | "interaction_rule";

export type MemoryPolarity = "positive" | "negative";

export type MemoryStatus = "active" | "archived" | "forgotten";

export interface Memory {
  id: string;
  userId: string;
  type: MemoryType;
  rule: string;
  domain: string;
  conceptScope: string;
  propositionScope: string;
  taskScope: string;
  polarity: MemoryPolarity;
  evidenceKind: string;
  sourceEventIds: string[];
  confidence: number;
  status: MemoryStatus;
  supersededBy?: string;
  createdAt: number;
  updatedAt: number;
}

export type MemoryScope = {
  domain: string;
  conceptScope: string;
  propositionScope: string;
  taskScope: string;
};

// ------- Session Brief -------

export interface SessionBrief {
  goal: string;
  currentFocus: string;
  knownPropositions: string[];
  openQuestions: string[];
  effectiveExplanations: string[];
  failedExplanations: string[];
  exactAnchors: string[];
  clarifyStreak: number;
  sessionRules: string[];
}

export function emptyBrief(): SessionBrief {
  return {
    goal: "",
    currentFocus: "",
    knownPropositions: [],
    openQuestions: [],
    effectiveExplanations: [],
    failedExplanations: [],
    exactAnchors: [],
    clarifyStreak: 0,
    sessionRules: [],
  };
}

// ------- Concept State -------

export interface ConceptState {
  id: string;
  userId: string;
  domain: string;
  concept: string;
  proposition: string;
  state: string;
  evidenceKind: string;
  sourceEventId: string;
  createdAt: number;
  updatedAt: number;
}

// ------- Turn / 消息 -------

export type TurnMode = "clarify" | "explain" | "feedback";

export type MessageRole = "user" | "assistant";

export interface Message {
  id: string;
  sessionId: string;
  turnId: string;
  role: MessageRole;
  content: string;
  createdAt: number;
}

// ------- 事件 -------

export const EVENT_KINDS = [
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
] as const;

export type EventKind = (typeof EVENT_KINDS)[number];

export interface LedgerEvent {
  id: string;
  userId: string;
  turnId: string;
  kind: EventKind;
  payload: Record<string, unknown>;
  tokenCount?: number;
  latencyMs?: number;
  createdAt: number;
}

// ------- 流式输出 -------

export type StreamKind = "thinking" | "content";

export interface StreamDelta {
  kind: StreamKind;
  delta: string;
}

// ------- 展示契约 -------

export interface PersonalizationEntry {
  memoryId: string;
  label: string;
  scope: string;
  effect: string;
}

export interface TurnMetrics {
  timeToFirstTokenMs?: number;
  memorySearchMs?: number;
  contextCompileMs?: number;
  memoryCapsuleTokens?: number;
  totalInputTokens?: number;
  /** 本轮**实际**使用的 provider。真实模型在首字前失败会降级为 "template"。 */
  provider?: string;
  model?: string;
  /** 本轮是否降级为模板兜底。必须依据本字段，而不是配置里的 provider。 */
  fallback?: boolean;
  /** 降级原因类别：AUTH / QUOTA / TIMEOUT / NETWORK / PROVIDER_ERROR / ... */
  fallbackReason?: string;
  /** 降级时"原本想用哪个模型"，用于向用户说明。 */
  requestedModel?: string;
}

export interface TurnPresentation {
  mode: TurnMode;
  depth: Depth;
  focus: string;
  plan: string[];
  personalization: PersonalizationEntry[];
  metrics: TurnMetrics;
  clarificationOptions?: ClarificationOption[];
  suggestedActions: string[];
  truncated: boolean;
  retrospective?: {
    connections: string[];
    openQuestions: string[];
    approach: string[];
  };
  outputVerification?: {
    status: string;
    checks: string[];
    violations: string[];
  };
}
