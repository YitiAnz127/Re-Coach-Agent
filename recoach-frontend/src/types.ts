export type CognitiveDepth = "L0" | "L1" | "L2" | "L3" | "L4" | "L5";

export type TurnMode = "clarify" | "explain" | "reflect";

export interface ClarificationOption {
  id: string;
  label: string;
  detail: string;
  followUp: string;
}

export interface PersonalizationEvidence {
  memoryId: string;
  label: string;
  scope: string;
  effect: string;
}

export interface PerformanceMetrics {
  timeToFirstTokenMs: number;
  memorySearchMs: number;
  contextCompileMs: number;
  memoryCapsuleTokens: number;
  totalInputTokens: number;
}

export interface ExperimentPoint {
  label: string;
  value: number;
  displayValue: string;
}

export interface MicroExperiment {
  title: string;
  description: string;
  code: string;
  points: ExperimentPoint[];
  takeaway: string;
}

export interface Retrospective {
  connections: string[];
  openQuestions: string[];
  approach: string[];
}

export interface SuggestedAction {
  id: string;
  label: string;
  prompt: string;
}

export interface TurnPresentation {
  mode: TurnMode;
  depth?: CognitiveDepth;
  focus: string;
  plan: string[];
  personalization: PersonalizationEvidence[];
  metrics: PerformanceMetrics;
  clarificationOptions?: ClarificationOption[];
  experiment?: MicroExperiment;
  retrospective?: Retrospective;
  outputVerification?: {
    status: "passed" | "partial" | "unknown";
    checks: string[];
    violations: string[];
  };
  suggestedActions: SuggestedAction[];
  truncated?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  state?: "streaming" | "complete" | "error";
  thinking?: string;
  presentation?: TurnPresentation;
  clientTurnId?: string;
  requestContent?: string;
  retryable?: boolean;
  errorCode?: string;
  requestId?: string;
  turnId?: string;
  elapsedMs?: number;
  completedAt?: string;
}

export type AgentStreamEvent =
  | {
      type: "turn.started";
      turnId: string;
      mode: TurnMode;
      focus: string;
      plan: string[];
    }
  | {
      type: "assistant.delta";
      turnId: string;
      delta: string;
    }
  | {
      type: "assistant.thinking";
      turnId: string;
      delta: string;
    }
  | {
      type: "tool.started";
      turnId: string;
      tool: "run_micro_experiment";
    }
  | {
      type: "tool.completed";
      turnId: string;
      tool: "run_micro_experiment";
    }
  | {
      type: "turn.completed";
      turnId: string;
      presentation: TurnPresentation;
    }
  | {
      type: "turn.error";
      turnId: string;
      code: string;
      message: string;
      retryable: boolean;
      requestId?: string;
    };
